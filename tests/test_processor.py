import io
from zipfile import ZipFile

from docx import Document
from docx.enum.text import WD_COLOR_INDEX

from app import create_app
from app.processor import MARKER, TemplateCheckError, process_template


def save_template(path):
    document = Document()
    document.sections[0].header.paragraphs[0].text = "Header {{ header_title }}"
    document.add_paragraph("Hello {{ name }}")
    split = document.add_paragraph()
    split.add_run("{{")
    split.add_run("split_name")
    split.add_run("}}")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Amount {{ amount }}"
    document.add_paragraph("Mail {{ user.email }} <script>alert(1)</script>")
    document.save(path)


def marker_runs(document):
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    for section in document.sections:
        if section.header._has_definition:
            paragraphs.extend(section.header.paragraphs)
    found = []
    for paragraph in paragraphs:
        for run in paragraph.runs:
            if run.text == MARKER and run.font.highlight_color == WD_COLOR_INDEX.RED:
                found.append(run)
    return found


def test_process_replaces_and_highlights_tags(tmp_path):
    source = tmp_path / "offer.docx"
    dest = tmp_path / "offer_out.docx"
    save_template(source)

    result = process_template(source, dest)

    assert result["labels"][:5] == ["name", "split_name", "amount", "user.email", "header_title"]
    assert "<mark>GAY100%</mark>" in result["preview_html"]
    assert "<script>" not in result["preview_html"]
    assert "&lt;script&gt;" in result["preview_html"]
    assert "Header" in result["preview_html"]

    rendered = Document(dest)
    assert len(marker_runs(rendered)) >= 5
    with ZipFile(dest) as package:
        xml = package.read("word/document.xml")
    assert b'w:val="red"' in xml
    assert b"GAY100%" in xml


def test_loop_and_condition_render_once(tmp_path):
    document = Document()
    document.add_paragraph("{% if show %}Visible {{ note }}{% endif %}")
    document.add_paragraph("{% for item in items %}{{ item.label }}{% endfor %}")
    source = tmp_path / "loop.docx"
    dest = tmp_path / "loop_out.docx"
    document.save(source)

    result = process_template(source, dest)

    assert "note" in result["labels"]
    assert "items" in result["labels"]
    assert "Visible" in result["preview_html"]
    assert result["preview_html"].count("<mark>GAY100%</mark>") == 2
    assert Document(dest).paragraphs[1].text == MARKER


def test_document_without_tags_still_previews(tmp_path):
    document = Document()
    document.add_paragraph("Plain sentence.")
    source = tmp_path / "plain.docx"
    dest = tmp_path / "plain_out.docx"
    document.save(source)

    result = process_template(source, dest)

    assert result["labels"] == []
    assert "Plain sentence." in result["preview_html"]
    assert "<mark>" not in result["preview_html"]
    assert dest.exists()


def test_hyphenated_placeholder_still_previews(tmp_path):
    document = Document()
    document.add_paragraph("Duty {{notdienst_24h_n_1}}")
    source = tmp_path / "alarm.docx"
    dest = tmp_path / "alarm_out.docx"
    document.save(source)

    result = process_template(source, dest)

    assert "notdienst_24h_n_1" in result["labels"]
    assert "<mark>GAY100%</mark>" in result["preview_html"]
    assert dest.exists()
    assert Document(dest).paragraphs[0].text == "Duty " + MARKER


def test_operator_in_placeholder_is_not_rewritten(tmp_path):
    document = Document()
    document.add_paragraph("Duty {{foo-bar}} plus {{a+b}} set {{a=b}}")
    source = tmp_path / "operators.docx"
    dest = tmp_path / "operators_out.docx"
    document.save(source)

    try:
        process_template(source, dest)
    except TemplateCheckError as exc:
        assert "foo-bar" in exc.labels
    else:
        raise AssertionError("expected a template error")
    assert not dest.exists()


def test_broken_tag_does_not_leave_a_download(tmp_path):
    document = Document()
    document.add_paragraph("{% if %}")
    source = tmp_path / "broken.docx"
    dest = tmp_path / "broken_out.docx"
    document.save(source)

    try:
        process_template(source, dest)
    except TemplateCheckError as exc:
        assert exc.labels == []
    else:
        raise AssertionError("expected a template error")
    assert not dest.exists()


def test_upload_lists_labels_and_download_is_highlighted(tmp_path):
    source = tmp_path / "Offer.docx"
    save_template(source)
    app = create_app()
    app.config["JOBS_DIR"] = tmp_path / "jobs"
    client = app.test_client()

    with source.open("rb") as handle:
        response = client.post(
            "/upload-and-check",
            data={"file": (handle, "Offer.docx")},
            content_type="multipart/form-data",
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["status"] == "success"
    assert "name" in payload["labels"]
    assert "header_title" in payload["labels"]
    assert "<mark>GAY100%</mark>" in payload["preview_html"]
    assert payload["download_name"] == "Offer_GAY.docx"

    downloaded = client.get(f"/download-processed?job_id={payload['job_id']}")
    assert downloaded.status_code == 200
    assert "Offer_GAY.docx" in downloaded.headers["Content-Disposition"]
    rendered = Document(io.BytesIO(downloaded.data))
    assert marker_runs(rendered)

    missing = client.get("/download-processed?job_id=../secret")
    assert missing.status_code == 404


def test_rejects_a_file_that_is_not_a_docx(tmp_path):
    app = create_app()
    app.config["JOBS_DIR"] = tmp_path / "jobs"
    client = app.test_client()
    response = client.post(
        "/upload-and-check",
        data={"file": (io.BytesIO(b"not a word file"), "notes.docx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json()["ok"] is False
