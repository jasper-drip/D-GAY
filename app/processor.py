"""Extract docxtpl placeholders, render them as highlighted test values, and preview the result."""

from __future__ import annotations

import copy
import html
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.opc.exceptions import OpcError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docxtpl import DocxTemplate, RichText
from jinja2 import ChainableUndefined, Environment, TemplateError
from lxml import etree

MARKER = "GAY100%"
# docxtpl 0.20 writes `highlight` as run shading (w:shd/@w:fill), which expects hex RGB.
HIGHLIGHT_FILL = "FF0000"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"w": W_NS}

TAG_RE = re.compile(r"\{\{([^{}]*)\}\}")
FOR_RE = re.compile(
    r"\{%-?\s*(?:tr\s+|tc\s+|p\s+)?for\s+[A-Za-z_][A-Za-z0-9_]*\s+in\s+"
    r"([A-Za-z_][A-Za-z0-9_.]*)",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TemplateCheckError(Exception):
    """The template opened, but Jinja could not render it."""

    def __init__(self, message: str, labels: list[str] | None = None):
        super().__init__(message)
        self.labels = labels or []


class InvalidDocxError(Exception):
    """The upload is not a readable .docx package."""


def marker_xml() -> str:
    return str(RichText(MARKER, highlight=HIGHLIGHT_FILL))


class GayNode:
    """Lazy stand-in so dotted names, if-tests, and one loop pass all render."""

    def __getattr__(self, name: str):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return self

    def __getitem__(self, _key):
        return self

    def __iter__(self):
        yield self

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        return 1

    def __str__(self) -> str:
        return marker_xml()

    def __html__(self) -> str:
        return marker_xml()


class GayUndefined(ChainableUndefined):
    """Same test value for any placeholder the context did not pre-fill."""

    __slots__ = ()

    def __str__(self) -> str:
        return marker_xml()

    def __html__(self) -> str:
        return marker_xml()

    def __iter__(self):
        yield GayUndefined(name=self._undefined_name)

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        return 1


def process_template(src: Path, dest: Path) -> dict:
    labels, full_text = collect_labels(src)
    render_src, rewritten_tags = jinja_source(src)
    try:
        return _render_checked(src, render_src, dest, labels, full_text, rewritten_tags)
    finally:
        if render_src != src:
            render_src.unlink(missing_ok=True)


def _render_checked(src: Path, render_src: Path, dest: Path, labels: list[str], full_text: str, rewritten_tags: int) -> dict:
    try:
        undeclared = DocxTemplate(render_src).get_undeclared_template_variables()
    except TemplateError as exc:
        # #region agent log
        _agent_log("A", "processor.py:process_template", "undeclared parse failed", {"error": short_message(exc), "badTags": _bad_tags(full_text), "labelCount": len(labels), "rewrittenTags": rewritten_tags})
        # #endregion
        raise TemplateCheckError(short_message(exc), labels) from exc

    labels = merge_labels(labels, undeclared)
    context = build_context(labels, undeclared, full_text)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        render_docx(render_src, dest, context)
        normalize_package(dest)
        document = paint_markers(dest)
        preview_html = build_preview(document)
    except TemplateError as exc:
        dest.unlink(missing_ok=True)
        # #region agent log
        _agent_log("A", "processor.py:render", "render failed", {"error": short_message(exc), "badTags": _bad_tags(full_text), "labelCount": len(labels)})
        # #endregion
        raise TemplateCheckError(short_message(exc), labels) from exc
    except Exception as exc:
        dest.unlink(missing_ok=True)
        # #region agent log
        _agent_log("C", "processor.py:render", "render crashed", {"errorType": type(exc).__name__, "error": short_message(exc)})
        # #endregion
        raise
    # #region agent log
    _agent_log("A", "processor.py:process_template", "check rendered", {"runId": "post-fix", "rewrittenTags": rewritten_tags, "labelCount": len(labels), "previewLength": len(preview_html), "markerCount": preview_html.count(MARKER)})
    # #endregion
    return {"labels": labels, "preview_html": preview_html}


def collect_labels(path: Path) -> tuple[list[str], str]:
    try:
        document = Document(path)
    except (OpcError, KeyError, ValueError) as exc:
        raise InvalidDocxError("That file is not a readable .docx template.") from exc

    labels: list[str] = []
    seen: set[str] = set()
    chunks: list[str] = []
    for paragraph in iter_document_paragraphs(document):
        text = paragraph.text or ""
        chunks.append(text)
        add_labels(text, labels, seen)
    return labels, "\n".join(chunks)


def add_labels(text: str, labels: list[str], seen: set[str]) -> None:
    for match in TAG_RE.finditer(text):
        label = " ".join(match.group(1).split())
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)


def merge_labels(labels: list[str], undeclared: set[str]) -> list[str]:
    merged = list(labels)
    seen = set(labels)
    for name in sorted(undeclared):
        if name in seen or label_covers(labels, name):
            continue
        merged.append(name)
        seen.add(name)
    return merged


def label_covers(labels: list[str], name: str) -> bool:
    norm = name.replace("-", "_")
    for label in labels:
        head = label.split("|", 1)[0].strip().replace("-", "_")
        if head == norm or head.startswith(norm + ".") or head.startswith(norm + "["):
            return True
    return False


def jinja_source(src: Path) -> tuple[Path, int]:
    """Copy the docx and turn hyphenated {{ names }} into names Jinja can parse."""
    env = Environment()
    changed = 0

    def fix_xml(data: bytes) -> bytes:
        nonlocal changed
        xml = data.decode("utf-8")
        out: list[str] = []
        cursor = 0
        while True:
            start = xml.find("{{", cursor)
            if start < 0:
                out.append(xml[cursor:])
                break
            end = xml.find("}}", start + 2)
            if end < 0:
                out.append(xml[cursor:])
                break
            chunk = xml[start:end + 2]
            fixed = _rewrite_hyphenated_name(chunk)
            if fixed != chunk:
                try:
                    env.parse(re.sub(r"<[^>]*>", "", fixed))
                except TemplateError:
                    fixed = chunk
                else:
                    changed += 1
            out.append(xml[cursor:start])
            out.append(fixed)
            cursor = end + 2
        return "".join(out).encode("utf-8")

    temporary = src.with_name(src.name + ".jinja.docx")
    with ZipFile(src, "r") as source, ZipFile(temporary, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                data = fix_xml(data)
            info = ZipInfo(filename=item.filename, date_time=item.date_time)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = item.external_attr
            target.writestr(info, data)
    if changed == 0:
        temporary.unlink(missing_ok=True)
        return src, 0
    return temporary, changed


def _rewrite_hyphenated_name(chunk: str) -> str:
    """Turn name-name into name_name, including when Word splits the name across runs."""
    pieces = re.split(r"(<[^>]*>)", chunk)
    text_indexes = [index for index, piece in enumerate(pieces) if not piece.startswith("<")]
    chars: list[str] = []
    owners: list[tuple[int, int]] = []
    for index in text_indexes:
        for offset, char in enumerate(pieces[index]):
            chars.append(char)
            owners.append((index, offset))
    if not re.search(r"[A-Za-z0-9_]-[A-Za-z0-9_]", "".join(chars)):
        return chunk
    buffers = {index: list(pieces[index]) for index in text_indexes}
    for index, char in enumerate(chars):
        if char != "-" or index == 0 or index + 1 == len(chars):
            continue
        left = chars[index - 1]
        right = chars[index + 1]
        if re.match(r"[A-Za-z0-9_]", left) and re.match(r"[A-Za-z0-9_]", right):
            piece_index, offset = owners[index]
            buffers[piece_index][offset] = "_"
    rewritten = pieces[:]
    for index, buffer in buffers.items():
        rewritten[index] = "".join(buffer)
    return "".join(rewritten)


def build_context(labels: list[str], undeclared: set[str], full_text: str) -> dict:
    object_roots: set[str] = set()
    for label in labels:
        root = root_name(label)
        if root and not is_leaf_label(label):
            object_roots.add(root)
    for match in FOR_RE.finditer(full_text):
        object_roots.add(match.group(1).split(".", 1)[0])

    names = set(undeclared)
    for label in labels:
        root = root_name(label)
        if root:
            names.add(root)

    context: dict = {}
    for name in names:
        if not NAME_RE.fullmatch(name):
            continue
        if name in object_roots:
            context[name] = GayNode()
        else:
            context[name] = RichText(MARKER, highlight=HIGHLIGHT_FILL)
    return context


def root_name(label: str) -> str | None:
    expression = label.split("|", 1)[0].strip()
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", expression)
    if not match:
        return None
    return match.group(1)


def is_leaf_label(label: str) -> bool:
    expression = label.split("|", 1)[0].strip()
    return NAME_RE.fullmatch(expression) is not None


def render_docx(src: Path, dest: Path, context: dict) -> None:
    template = DocxTemplate(src)
    environment = Environment(autoescape=False, undefined=GayUndefined)
    template.render(context, jinja_env=environment)
    template.save(dest)


def normalize_package(path: Path) -> None:
    """Lift RichText runs out of the surrounding w:t so Word can display them."""
    temporary = path.parent / f"{path.name}.norm"
    try:
        with ZipFile(path, "r") as source, ZipFile(temporary, "w") as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                    data = unwrap_part(data)
                info = ZipInfo(filename=item.filename, date_time=item.date_time)
                info.compress_type = ZIP_DEFLATED
                info.external_attr = item.external_attr
                target.writestr(info, data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def unwrap_part(data: bytes) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    unwrap_nested_runs(root)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def unwrap_nested_runs(root) -> None:
    run_tag = f"{{{W_NS}}}r"
    rpr_tag = f"{{{W_NS}}}rPr"
    for _ in range(10000):
        target = None
        parent = None
        for candidate in root.xpath(".//w:t[*]", namespaces=NSMAP):
            owner = candidate.getparent()
            if owner is not None and owner.tag == run_tag and owner.getparent() is not None:
                target = candidate
                parent = owner
                break
        if target is None or parent is None:
            return

        container = parent.getparent()
        index = list(container).index(parent)
        run_properties = parent.find(rpr_tag)
        pieces = []
        if target.text:
            pieces.append(make_run(target.text, run_properties))
        for child in list(target):
            tail = child.tail
            child.tail = None
            pieces.append(child)
            if tail:
                pieces.append(make_run(tail, run_properties))
        container.remove(parent)
        for offset, node in enumerate(pieces):
            container.insert(index + offset, node)


def make_run(text: str, run_properties):
    run = etree.Element(f"{{{W_NS}}}r")
    if run_properties is not None:
        run.append(copy.deepcopy(run_properties))
    text_node = etree.SubElement(run, f"{{{W_NS}}}t")
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set(f"{{{XML_NS}}}space", "preserve")
    text_node.text = text
    return run


def paint_markers(path: Path) -> Document:
    document = Document(path)
    for paragraph in iter_document_paragraphs(document):
        for run in iter_runs(paragraph):
            if MARKER in (run.text or ""):
                run.font.highlight_color = WD_COLOR_INDEX.RED
    document.save(path)
    return document


def build_preview(document) -> str:
    headers, footers = header_footer_html(document)
    body = body_html(document)
    parts = ['<article class="paper">']
    parts.extend(headers)
    parts.append(f'<div class="paper-body">{body}</div>')
    parts.extend(footers)
    parts.append("</article>")
    return "".join(parts)


def header_footer_html(document) -> tuple[list[str], list[str]]:
    headers: list[str] = []
    footers: list[str] = []
    seen: set[int] = set()
    multiple = len(document.sections) > 1
    for index, section in enumerate(document.sections, start=1):
        parts = (
            ("Header", section.header, True),
            ("First header", section.first_page_header, True),
            ("Even header", section.even_page_header, True),
            ("Footer", section.footer, False),
            ("First footer", section.first_page_footer, False),
            ("Even footer", section.even_page_footer, False),
        )
        for title, part, is_header in parts:
            key = id(part._element)
            if key in seen:
                continue
            seen.add(key)
            inner = container_html(part)
            if not meaningful(inner):
                continue
            label = f"{title} {index}" if multiple else title
            block = (
                f'<section class="paper-note"><p class="paper-kicker">{html.escape(label)}</p>'
                f"{inner}</section>"
            )
            (headers if is_header else footers).append(block)
    return headers, footers


def body_html(document) -> str:
    chunks = []
    for kind, block in iter_body_blocks(document):
        if kind == "p":
            chunks.append(paragraph_html(block))
        else:
            chunks.append(table_html(block))
    rendered = "".join(chunks)
    if not meaningful(rendered):
        return '<p class="blank">The document body is empty.</p>'
    return rendered


def container_html(container) -> str:
    chunks = [paragraph_html(paragraph) for paragraph in container.paragraphs]
    for table in container.tables:
        chunks.append(table_html(table))
    return "".join(chunks)


def table_html(table: Table) -> str:
    rows = []
    for row in table.rows:
        seen: set[int] = set()
        cells = []
        for cell in row.cells:
            marker = id(cell._tc)
            if marker in seen:
                continue
            seen.add(marker)
            inner = "".join(paragraph_html(paragraph) for paragraph in cell.paragraphs)
            for nested in cell.tables:
                inner += table_html(nested)
            cells.append(f"<td>{inner}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table>{''.join(rows)}</table>"


def paragraph_html(paragraph: Paragraph) -> str:
    inner = "".join(block_html(block) for block in paragraph.iter_inner_content())
    if not inner:
        inner = inline_text(paragraph.text or "")
    if not meaningful(inner):
        return '<p class="blank">&nbsp;</p>'
    return f"<p>{inner}</p>"


def block_html(block) -> str:
    if isinstance(block, Hyperlink):
        return "".join(run_html(run) for run in block.runs)
    return run_html(block)


def run_html(run) -> str:
    text = run.text or ""
    if not text:
        return ""
    if MARKER in text:
        return inline_text(text)
    try:
        highlighted = run.font.highlight_color is not None
    except (AttributeError, ValueError):
        highlighted = False
    escaped = html.escape(text).replace("\t", "    ").replace("\n", "<br>")
    if highlighted:
        return f"<mark>{escaped}</mark>"
    return escaped


def inline_text(text: str) -> str:
    pieces = text.split(MARKER)
    chunks = []
    for index, piece in enumerate(pieces):
        if index:
            chunks.append(f"<mark>{html.escape(MARKER)}</mark>")
        if piece:
            chunks.append(html.escape(piece).replace("\t", "    ").replace("\n", "<br>"))
    return "".join(chunks)


def meaningful(fragment: str) -> bool:
    text = re.sub(r"<[^>]+>", "", fragment)
    text = html.unescape(text).replace("\xa0", " ").strip()
    return bool(text)


def iter_document_paragraphs(document):
    yield from iter_block_paragraphs(iter_body_blocks(document))
    for part in iter_header_footer_parts(document):
        yield from part.paragraphs
        for table in part.tables:
            yield from iter_table_paragraphs(table)


def iter_block_paragraphs(blocks):
    for kind, block in blocks:
        if kind == "p":
            yield block
        else:
            yield from iter_table_paragraphs(block)


def iter_body_blocks(document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "p", Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield "tbl", Table(child, document)


def iter_table_paragraphs(table: Table):
    for row in table.rows:
        seen: set[int] = set()
        for cell in row.cells:
            marker = id(cell._tc)
            if marker in seen:
                continue
            seen.add(marker)
            yield from cell.paragraphs
            for nested in cell.tables:
                yield from iter_table_paragraphs(nested)


def iter_header_footer_parts(document):
    seen: set[int] = set()
    for section in document.sections:
        for part in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            # Touching a missing header creates one. Skip those so saving the
            # painted document does not add empty header or footer parts.
            if not part._has_definition:
                continue
            key = id(part._element)
            if key in seen:
                continue
            seen.add(key)
            yield part


def iter_runs(paragraph: Paragraph):
    for block in paragraph.iter_inner_content():
        if isinstance(block, Hyperlink):
            yield from block.runs
        else:
            yield block


def _bad_tags(full_text: str) -> list[str]:
    found: list[str] = []
    env = Environment()
    for match in TAG_RE.finditer(full_text):
        label = " ".join(match.group(1).split())
        if not label:
            continue
        try:
            env.parse("{{ " + label + " }}")
        except TemplateError:
            found.append(label[:160])
            if len(found) >= 12:
                break
    return found


def _agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    try:
        import json
        import time

        payload = {
            "sessionId": "d2d014",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(r"c:\Users\nschi\Desktop\D-GAY\.cursor\debug-d2d014.log", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except Exception:
        pass
    # #endregion


def short_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if not message:
        return "The template could not be rendered."
    if len(message) > 500:
        return message[:500] + "..."
    return message
