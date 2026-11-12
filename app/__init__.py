from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from app.routes import bp

MAX_UPLOAD_BYTES = 16 * 1024 * 1024


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    jobs_dir = Path(__file__).resolve().parent.parent / "var" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    app.config["JOBS_DIR"] = jobs_dir
    app.register_blueprint(bp)

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(_error):
        message = "File exceeds the 16 MB limit."
        if request.path == "/upload-and-check":
            return jsonify(ok=False, status="error", message=message, labels=[]), 413
        return message, 413

    return app
