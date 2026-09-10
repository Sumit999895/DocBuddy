import os
import uuid
import shutil
import base64
import tempfile
from pathlib import Path

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    send_file,
    session,
)

from werkzeug.utils import secure_filename

from services.scan_service import (
    scan_document,
    is_supported_file,
)


# ============================================================
# BLUEPRINT
# ============================================================

scan_bp = Blueprint(
    "scan",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


# ============================================================
# CONFIGURATION
# ============================================================

MAX_FILE_SIZE = (
    150 * 1024 * 1024
)

BASE_TEMP_DIR = Path(
    tempfile.gettempdir()
) / "docbuddy_scan"

BASE_TEMP_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# HELPERS
# ============================================================

def get_job_directory(job_id):
    return (
        BASE_TEMP_DIR /
        secure_filename(job_id)
    )


def encode_token(
    job_id,
    filename
):
    raw = (
        f"{job_id}:{filename}"
        .encode("utf-8")
    )

    return base64.urlsafe_b64encode(
        raw
    ).decode("utf-8")


def decode_token(token):

    try:

        raw = base64.urlsafe_b64decode(
            token.encode("utf-8")
        ).decode("utf-8")

        job_id, filename = raw.split(
            ":",
            1
        )

        return (
            job_id,
            filename
        )

    except Exception:

        return (
            None,
            None
        )


def get_file_from_token(
    token
):

    job_id, filename = decode_token(
        token
    )

    if not job_id or not filename:
        return None, None

    safe_job_id = secure_filename(
        job_id
    )

    safe_filename = secure_filename(
        filename
    )

    if (
        not safe_job_id
        or not safe_filename
    ):
        return None, None

    if safe_job_id != job_id:
        return None, None

    job_directory = (
        BASE_TEMP_DIR /
        safe_job_id
    )

    path = (
        job_directory /
        safe_filename
    )

    try:

        path.resolve().relative_to(
            BASE_TEMP_DIR.resolve()
        )

    except ValueError:

        return None, None

    if not path.is_file():
        return None, None

    return (
        path,
        safe_filename
    )


def cleanup_job(
    job_directory
):

    try:

        if job_directory.exists():

            shutil.rmtree(
                job_directory,
                ignore_errors=True
            )

    except Exception as exc:

        print(
            "SCAN CLEANUP ERROR:",
            repr(exc)
        )


# ============================================================
# PAGE
# ============================================================

@scan_bp.route(
    "/scan-document"
)
@scan_bp.route(
    "/scan-document/"
)
def scan_document_page():

    return render_template(
        "scan.html"
    )


# ============================================================
# SCAN API
# ============================================================

@scan_bp.route(
    "/api/scan-document",
    methods=["POST"]
)
def process_scan():

    job_id = uuid.uuid4().hex

    job_directory = (
        BASE_TEMP_DIR /
        job_id
    )

    input_path = None

    try:

        # ----------------------------------------------------
        # Upload
        # ----------------------------------------------------

        uploaded_file = request.files.get(
            "file"
        )

        if not uploaded_file:

            return jsonify({
                "success": False,
                "error":
                    "Please select a document."
            }), 400

        filename = (
            uploaded_file.filename
            or ""
        )

        if not is_supported_file(
            filename
        ):

            return jsonify({
                "success": False,
                "error":
                    "Supported formats: "
                    "PDF, JPG, JPEG, PNG, WEBP, "
                    "BMP, TIFF."
            }), 400

        # ----------------------------------------------------
        # File size
        # ----------------------------------------------------

        uploaded_file.stream.seek(
            0,
            os.SEEK_END
        )

        file_size = (
            uploaded_file.stream.tell()
        )

        uploaded_file.stream.seek(
            0
        )

        if file_size <= 0:

            return jsonify({
                "success": False,
                "error":
                    "The selected file is empty."
            }), 400

        if file_size > MAX_FILE_SIZE:

            return jsonify({
                "success": False,
                "error":
                    "Maximum file size is 150 MB."
            }), 413

        # ----------------------------------------------------
        # Language
        # ----------------------------------------------------

        language = (
            request.form.get(
                "language",
                "eng"
            )
            .strip()
        )

        # Only safe Tesseract language values
        # are accepted here.
        allowed_languages = {
            "eng",
            "hin",
            "eng+hin",
        }

        if language not in allowed_languages:

            language = "eng"

        # ----------------------------------------------------
        # Create directory
        # ----------------------------------------------------

        job_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        safe_filename = secure_filename(
            filename
        )

        if not safe_filename:

            safe_filename = (
                "document"
            )

        input_path = (
            job_directory /
            safe_filename
        )

        uploaded_file.save(
            str(input_path)
        )

        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        def progress_callback(
            current,
            total
        ):
            # Processing is synchronous.
            # Progress is mainly used internally.
            print(
                f"SCAN OCR: "
                f"{current}/{total}"
            )

        # ----------------------------------------------------
        # Scan
        # ----------------------------------------------------

        result = scan_document(
            str(input_path),
            language=language,
            output_directory=str(
                job_directory
            ),
            progress_callback=
                progress_callback
        )

        # ----------------------------------------------------
        # Output filenames
        # ----------------------------------------------------

        pdf_path = Path(
            result["searchable_pdf"]
        )

        txt_path = Path(
            result["text_file"]
        )

        # ----------------------------------------------------
        # Tokens
        # ----------------------------------------------------

        pdf_token = encode_token(
            job_id,
            pdf_path.name
        )

        txt_token = encode_token(
            job_id,
            txt_path.name
        )

        # ----------------------------------------------------
        # Return
        # ----------------------------------------------------

        return jsonify({
            "success": True,

            "original_filename":
                filename,

            "page_count":
                result.get(
                    "page_count",
                    1
                ),

            "word_count":
                result.get(
                    "word_count",
                    len(
                        result["text"].split()
                    )
                ),

            "character_count":
                len(
                    result["text"]
                ),

            "text":
                result["text"],

            "pages":
                result.get(
                    "pages",
                    []
                ),

            "ai":
                result.get(
                    "ai",
                    {}
                ),

            "pdf_url":
                f"/api/scan-document/"
                f"preview/{pdf_token}",

            "pdf_download_url":
                f"/api/scan-document/"
                f"download/{pdf_token}",

            "txt_download_url":
                f"/api/scan-document/"
                f"download/{txt_token}",
        })

    except ValueError as exc:

        cleanup_job(
            job_directory
        )

        return jsonify({
            "success": False,
            "error": str(exc)
        }), 400

    except Exception as exc:

        print(
            "SCAN ERROR:",
            repr(exc)
        )

        cleanup_job(
            job_directory
        )

        return jsonify({
            "success": False,
            "error":
                "Unable to scan this document. "
                "Please make sure the file is valid "
                "and OCR is installed correctly."
        }), 500

    finally:

        # Remove original upload after processing.
        # Output files remain temporarily.
        try:

            if (
                input_path
                and input_path.exists()
            ):

                input_path.unlink()

        except Exception:
            pass


# ============================================================
# PREVIEW SEARCHABLE PDF
# ============================================================


@scan_bp.route(
    "/api/scan-document/preview/<token>",
    endpoint="scan_pdf_preview"
)
def scan_pdf_preview(token):

    path, filename = (
        get_file_from_token(token)
    )

    if not path:

        return jsonify({
            "success": False,
            "error":
                "Preview file not found."
        }), 404

    response = send_file(
        str(path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
        max_age=0
    )

    response.headers[
        "Content-Disposition"
    ] = (
        f'inline; filename="{filename}"'
    )

    response.headers[
        "X-Content-Type-Options"
    ] = "nosniff"

    response.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate"
    )

    return response


# ============================================================
# DOWNLOAD
# ============================================================

@scan_bp.route(
    "/api/scan-document/download/<token>"
)
def scan_download(token):

    path, filename = (
        get_file_from_token(token)
    )

    if not path:

        return jsonify({
            "success": False,
            "error":
                "Download file not found."
        }), 404

    return send_file(
        str(path),
        mimetype=(
            "text/plain"
            if filename.lower().endswith(
                ".txt"
            )
            else "application/pdf"
        ),
        as_attachment=True,
        download_name=filename
    )


# ============================================================
# AI ANALYSIS AGAIN
# ============================================================

@scan_bp.route(
    "/api/scan-document/ai",
    methods=["POST"]
)
def scan_ai_analysis():

    try:

        from services.scan_service import (
            analyze_document_with_ai
        )

        data = request.get_json(
            silent=True
        ) or {}

        text = data.get(
            "text",
            ""
        )

        if not text.strip():

            return jsonify({
                "success": False,
                "error":
                    "No extracted text was supplied."
            }), 400

        result = (
            analyze_document_with_ai(
                text
            )
        )

        return jsonify({
            "success": True,
            "ai": result
        })

    except Exception as exc:

        print(
            "SCAN AI ERROR:",
            repr(exc)
        )

        return jsonify({
            "success": False,
            "error":
                "AI analysis failed."
        }), 500