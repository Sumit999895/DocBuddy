from flask import Blueprint, render_template, request, jsonify, send_file, abort, current_app
from pathlib import Path
from werkzeug.utils import secure_filename
from services.page_format_service import format_pdf, cleanup_old_files, OUT, MAX_PAGES
import json
import os
import fitz

page_format_bp = Blueprint(
    "page_format", __name__,
    template_folder="../templates", static_folder="../static",
    static_url_path="/page-format-static",
)

MAX_SIZE = 150 * 1024 * 1024
ALLOWED_FIT = {"contain", "cover", "stretch", "original"}
ALLOWED_QUALITY = {"draft", "standard", "high", "print"}
ALLOWED_ALIGN = {"left", "center", "right"}
ALLOWED_FILTER = {"all", "odd", "even"}
TOKEN_LEN = 32


def new_token():
    return os.urandom(16).hex()


def safe_token_path(token):
    if not token or len(token) != TOKEN_LEN or any(c not in "0123456789abcdef" for c in token.lower()):
        abort(404)
    p = (OUT / f"{token}.pdf").resolve()
    if OUT.resolve() not in p.parents or not p.is_file():
        abort(404)
    return p


def validate_settings(settings):
    """Raises ValueError with a user-facing message on anything invalid."""
    if not isinstance(settings, dict):
        raise ValueError("Invalid page format settings.")

    for key in ("width_mm", "height_mm"):
        try:
            if float(settings.get(key, 0)) < 10:
                raise ValueError("Page dimensions must be at least 10 mm.")
        except (TypeError, ValueError):
            raise ValueError("Page dimensions must be numeric and at least 10 mm.")

    m = settings.get("margins") or {}
    if not isinstance(m, dict):
        raise ValueError("Invalid margin settings.")
    try:
        mt, mb = float(m.get("mt", 0)), float(m.get("mb", 0))
        ml, mr = float(m.get("ml", 0)), float(m.get("mr", 0))
    except (TypeError, ValueError):
        raise ValueError("Margins must be numeric.")
    if mt + mb >= float(settings["height_mm"]):
        raise ValueError("Top and bottom margins are too large for the page height.")
    if ml + mr >= float(settings["width_mm"]):
        raise ValueError("Left and right margins are too large for the page width.")

    if settings.get("fit", "contain") not in ALLOWED_FIT:
        raise ValueError("Invalid content fit mode.")
    if settings.get("quality", "standard") not in ALLOWED_QUALITY:
        raise ValueError("Invalid quality preset.")
    if settings.get("header_align", "center") not in ALLOWED_ALIGN:
        raise ValueError("Invalid header alignment.")
    if settings.get("footer_align", "center") not in ALLOWED_ALIGN:
        raise ValueError("Invalid footer alignment.")
    if settings.get("page_filter", "all") not in ALLOWED_FILTER:
        raise ValueError("Invalid page filter.")

    columns = settings.get("columns", 1)
    try:
        if not (1 <= int(columns) <= 12):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Columns must be a whole number between 1 and 12.")

    rotation = settings.get("rotation", 0)
    try:
        if int(rotation) % 90 != 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.")

    for key in ("page_start", "page_end"):
        v = settings.get(key)
        if v is not None:
            try:
                if int(v) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"'{key}' must be a positive whole number.")

    if float(settings.get("watermark_opacity", 0.15) or 0) > 1:
        raise ValueError("Watermark opacity must be between 0 and 1.")


@page_format_bp.route("/page-format")
@page_format_bp.route("/page-format/")
@page_format_bp.route("/format")
@page_format_bp.route("/format/")
def page_format_page():
    return render_template("page_format.html")


@page_format_bp.route("/api/page-format/process", methods=["POST"])
def process():
    cleanup_old_files()

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(success=False, error="Please upload a PDF."), 400
    if Path(f.filename).suffix.lower() != ".pdf":
        return jsonify(success=False, error="Only PDF files are supported."), 400

    data = f.read()
    if not data:
        return jsonify(success=False, error="The uploaded file is empty."), 400
    if len(data) > MAX_SIZE:
        return jsonify(success=False, error="Maximum PDF size is 150 MB."), 413
    if not data.startswith(b"%PDF"):
        return jsonify(success=False, error="That file doesn't look like a valid PDF."), 400

    original_name = secure_filename(Path(f.filename).stem) or "document"
    tmp = OUT / f"_input_{new_token()}.pdf"
    tmp.write_bytes(data)

    try:
        try:
            settings = json.loads(request.form.get("settings", "{}"))
        except (json.JSONDecodeError, TypeError):
            raise ValueError("Invalid page format settings.")

        validate_settings(settings)

        try:
            with fitz.open(str(tmp)) as probe:
                if probe.needs_pass:
                    raise ValueError("Password-protected PDFs are not supported. Please remove the password first.")
                if len(probe) > MAX_PAGES:
                    raise ValueError(f"PDF has too many pages (limit is {MAX_PAGES}).")
        except ValueError:
            raise
        except Exception:
            raise ValueError("The uploaded file could not be read as a PDF.")

        token, out_path, pages = format_pdf(tmp, settings)
        size_kb = round(out_path.stat().st_size / 1024, 1)

        return jsonify(
            success=True,
            token=token,
            pages=pages,
            size_kb=size_kb,
            source_name=original_name,
            download_url=f"/api/page-format/download/{token}",
            preview_url=f"/api/page-format/preview/{token}",
        )
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400
    except Exception:
        current_app.logger.exception("page-format processing failed")
        return jsonify(success=False, error="Something went wrong while formatting the PDF."), 500
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


@page_format_bp.get("/api/page-format/preview/<token>")
def preview(token):
    return send_file(safe_token_path(token), mimetype="application/pdf", as_attachment=False, download_name="page-format-preview.pdf")


@page_format_bp.get("/api/page-format/download/<token>")
def download(token):
    name = request.args.get("name")
    name = secure_filename(name) if name else "page-formatted"
    return send_file(safe_token_path(token), mimetype="application/pdf", as_attachment=True, download_name=f"{name}.pdf")