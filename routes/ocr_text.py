from flask import Blueprint, render_template, request, jsonify, send_file, abort, current_app
from pathlib import Path
from werkzeug.utils import secure_filename
from services.ocr_text_service import (
    process, capabilities, workfile_path, zip_path_for, cleanup_old_files,
    OUT, MAX_PAGES, MAX_FILES, PSM_OPTIONS, TROCR_MODELS,
)
import json
import os

ocr_text_bp = Blueprint(
    "ocr_text", __name__,
    template_folder="../templates", static_folder="../static",
    static_url_path="/ocr-text-static",
)

MAX_SIZE = 100 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
ALLOWED_MODES = {"auto", "ocr", "native"}
ALLOWED_ENGINES = {"auto", "basic", "enhanced", "trocr"}
ALLOWED_QUALITY = {"fast", "thorough"}
ALLOWED_FILTER = {"all", "odd", "even"}
TOKEN_LEN = 32


def new_token():
    return os.urandom(16).hex()


def is_token(token):
    return bool(token) and len(token) == TOKEN_LEN and all(c in "0123456789abcdef" for c in token.lower())


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Invalid settings.")
    if settings.get("mode", "auto") not in ALLOWED_MODES:
        raise ValueError("Invalid extraction mode.")
    if settings.get("engine", "auto") not in ALLOWED_ENGINES:
        raise ValueError("Invalid OCR engine choice.")
    if settings.get("page_filter", "all") not in ALLOWED_FILTER:
        raise ValueError("Invalid page filter.")
    for key in ("page_start", "page_end"):
        v = settings.get(key)
        if v is not None:
            try:
                if int(v) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"'{key}' must be a positive whole number.")
    try:
        if not (72 <= int(settings.get("dpi", 200)) <= 400):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("DPI must be a number between 72 and 400.")
    try:
        if int(settings.get("rotation", 0)) % 90 != 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.")
    psm = settings.get("psm", 3)
    try:
        if int(psm) not in PSM_OPTIONS:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Invalid page segmentation mode.")
    if settings.get("quality_mode", "fast") not in ALLOWED_QUALITY:
        raise ValueError("Invalid quality mode.")
    if settings.get("trocr_model", "base") not in TROCR_MODELS:
        raise ValueError("Invalid TrOCR model choice.")


@ocr_text_bp.route("/ocr-text")
@ocr_text_bp.route("/ocr-text/")
def ocr_text_page():
    return render_template("ocr_text.html")


@ocr_text_bp.get("/api/ocr-text/capabilities")
def get_capabilities():
    return jsonify(success=True, **capabilities())


@ocr_text_bp.route("/api/ocr-text/process", methods=["POST"])
def run_process():
    cleanup_old_files()

    files = request.files.getlist("file")
    if not files or not any(f.filename for f in files):
        return jsonify(success=False, error="Please upload a PDF or image."), 400
    if len(files) > MAX_FILES:
        return jsonify(success=False, error=f"Too many files at once (limit is {MAX_FILES})."), 400

    try:
        settings = json.loads(request.form.get("settings", "{}"))
    except (json.JSONDecodeError, TypeError):
        return jsonify(success=False, error="Invalid settings."), 400

    try:
        validate_settings(settings)
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400

    tmp_paths = []
    source_names = []
    try:
        for f in files:
            if not f.filename:
                continue
            suffix = Path(f.filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                return jsonify(success=False, error=f"'{f.filename}': unsupported type. Use PDF, PNG, JPG, TIFF, BMP, or WEBP."), 400

            data = f.read()
            if not data:
                return jsonify(success=False, error=f"'{f.filename}' is empty."), 400
            if len(data) > MAX_SIZE:
                return jsonify(success=False, error=f"'{f.filename}' exceeds the 100 MB limit."), 413
            if suffix == ".pdf" and not data.startswith(b"%PDF"):
                return jsonify(success=False, error=f"'{f.filename}' doesn't look like a valid PDF."), 400

            tmp = OUT / f"_input_{new_token()}{suffix}"
            tmp.write_bytes(data)
            tmp_paths.append(tmp)
            source_names.append(secure_filename(Path(f.filename).stem) or "document")

        token, results = process(tmp_paths, settings, source_names)

        preview_limit = 20000
        out_results = []
        for r in results:
            if r.get("error"):
                out_results.append({"filename": r["filename"], "error": r["error"]})
                continue
            combined = "\n\n".join(f"--- Page {p['page']} ---\n{p['text']}" for p in r["pages"])
            truncated = len(combined) > preview_limit
            pages_payload = r["pages"] if not truncated else [{
                "page": 0, "mode": "native", "text": combined[:preview_limit] + "\n\n… (truncated in preview — download the full text below)",
                "confidence": None, "handwriting": {"score": 0, "label": "", "method": ""},
            }]
            out_results.append({
                "filename": r["filename"],
                "error": None,
                "stats": r["stats"],
                "metadata": r["metadata"],
                "handwriting_summary": r["handwriting_summary"],
                "pages": pages_payload,
                "preview_truncated": truncated,
                "downloads": {
                    kind: f"/api/ocr-text/download/{token}/{fname}"
                    for kind, fname in r["files"].items()
                },
            })

        return jsonify(success=True, token=token, zip_url=f"/api/ocr-text/download/{token}/zip", results=out_results)

    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception:
        current_app.logger.exception("ocr-text processing failed")
        return jsonify(success=False, error="Something went wrong while processing the file(s)."), 500
    finally:
        for tmp in tmp_paths:
            try:
                tmp.unlink()
            except OSError:
                pass


@ocr_text_bp.get("/api/ocr-text/download/<token>/zip")
def download_zip(token):
    if not is_token(token):
        abort(404)
    p = zip_path_for(token)
    if not p:
        abort(404)
    return send_file(p, mimetype="application/zip", as_attachment=True, download_name="ocr-results.zip")


@ocr_text_bp.get("/api/ocr-text/download/<token>/<path:filename>")
def download_file(token, filename):
    if not is_token(token):
        abort(404)
    p = workfile_path(token, filename)
    if not p:
        abort(404)
    mimetypes = {
        ".txt": "text/plain", ".json": "application/json", ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    mimetype = mimetypes.get(p.suffix.lower(), "application/octet-stream")
    return send_file(p, mimetype=mimetype, as_attachment=True, download_name=p.name)