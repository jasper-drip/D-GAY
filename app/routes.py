import json
import re
import uuid
from pathlib import Path
from zipfile import BadZipFile

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file
from docx.opc.exceptions import OpcError

from app.processor import InvalidDocxError, TemplateCheckError, process_template

bp = Blueprint("checker", __name__)
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@bp.get("/")
def index():
    return render_template("index.html")


@bp.post("/upload-and-check")
def upload_and_check():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return error("Choose a .docx template.", 400)

    original_name = Path(upload.filename).name
    if Path(original_name).suffix.lower() != ".docx":
        return error("Only .docx templates can be checked.", 400)
    # #region agent log
    from app.processor import _agent_log
    _agent_log("B", "routes.py:upload", "upload received", {"filename": original_name, "size": upload.content_length})
    # #endregion

    job_id = uuid.uuid4().hex
    jobs_dir = Path(current_app.config["JOBS_DIR"])
    jobs_dir.mkdir(parents=True, exist_ok=True)
    source = jobs_dir / f"{job_id}.source.docx"
    dest = jobs_dir / f"{job_id}.docx"

    succeeded = False
    try:
        upload.save(source)
        result = process_template(source, dest)
        succeeded = True
    except TemplateCheckError as exc:
        # #region agent log
        _agent_log("A", "routes.py:upload", "template check error", {"filename": original_name, "message": str(exc), "labelCount": len(exc.labels)})
        # #endregion
        return (
            jsonify(
                ok=False,
                status="error",
                message=str(exc),
                labels=exc.labels,
                preview_html="",
                job_id=None,
            ),
            422,
        )
    except (InvalidDocxError, BadZipFile, OpcError, KeyError, ValueError) as exc:
        # #region agent log
        _agent_log("C", "routes.py:upload", "unreadable docx", {"filename": original_name, "errorType": type(exc).__name__})
        # #endregion
        return error("That file is not a readable .docx template.", 400)
    except Exception as exc:
        # #region agent log
        _agent_log("C", "routes.py:upload", "unexpected failure", {"filename": original_name, "errorType": type(exc).__name__, "error": str(exc)[:300]})
        # #endregion
        return error(f"The template could not be checked. {exc}".strip(), 500)
    finally:
        source.unlink(missing_ok=True)
        if not succeeded:
            dest.unlink(missing_ok=True)

    download_name = safe_download_name(original_name)
    (jobs_dir / f"{job_id}.json").write_text(
        json.dumps({"filename": download_name}),
        encoding="utf-8",
    )
    # #region agent log
    _agent_log("D", "routes.py:upload", "check succeeded", {"filename": original_name, "labelCount": len(result["labels"]), "previewLength": len(result["preview_html"]), "jobId": job_id})
    # #endregion
    return jsonify(
        ok=True,
        status="success",
        message=success_message(result["labels"]),
        labels=result["labels"],
        preview_html=result["preview_html"],
        job_id=job_id,
        download_name=download_name,
    )


@bp.get("/download-processed")
def download_processed():
    job_id = request.args.get("job_id", "")
    if not JOB_ID_RE.fullmatch(job_id):
        abort(404)

    jobs_dir = Path(current_app.config["JOBS_DIR"])
    dest = jobs_dir / f"{job_id}.docx"
    meta_path = jobs_dir / f"{job_id}.json"
    if not dest.is_file() or not meta_path.is_file():
        abort(404)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    download_name = safe_download_name(meta.get("filename") or "template_GAY.docx")
    return send_file(
        dest,
        as_attachment=True,
        download_name=download_name,
        mimetype=DOCX_MIME,
        max_age=0,
    )


def success_message(labels: list[str]) -> str:
    count = len(labels)
    if count == 0:
        return "No placeholders were found. The document is ready to download."
    noun = "placeholder" if count == 1 else "placeholders"
    return f"Replaced {count} {noun} with GAY100%."


def safe_download_name(filename: str) -> str:
    stem = Path(filename).name
    if stem.lower().endswith(".docx"):
        stem = stem[:-5]
    elif stem.lower().endswith("_gay"):
        stem = stem[:-4]
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", stem).strip(" .")
    if not stem:
        stem = "template"
    if not stem.lower().endswith("_gay"):
        stem = f"{stem}_GAY"
    return f"{stem}.docx"


def error(message: str, status: int):
    return jsonify(ok=False, status="error", message=message, labels=[], preview_html="", job_id=None), status
