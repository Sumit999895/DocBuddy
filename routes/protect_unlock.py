import os
import uuid
import base64
import shutil
from pathlib import Path

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    session,
    send_file,
    redirect,
    url_for,
)
from werkzeug.utils import secure_filename

from database import get_db_connection

from services.pdf_protect_unlock_service import (
    protect_pdf,
    unlock_pdf,
)


protect_unlock_bp = Blueprint(
    "protect_unlock",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


# ============================================================
# CONFIGURATION
# ============================================================

MAX_FILE_SIZE = 150 * 1024 * 1024

BASE_TEMP_DIR = Path(os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "temp"
    )
))

OUTPUT_DIR = BASE_TEMP_DIR / "docbuddy_protect_unlock"

ALLOWED_EXTENSIONS = {".pdf"}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _get_user_id():
    """Return logged-in user ID."""
    return session.get("user_id")


def _is_pro_user():
    """
    Check whether the currently logged-in user has an
    active Pro plan and active account.
    """

    user_id = _get_user_id()

    if not user_id:
        return False

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                plan,
                plan_expires_at,
                account_status
            FROM Users
            WHERE id = %s
            LIMIT 1
            """,
            (user_id,)
        )

        user = cursor.fetchone()

        if not user:
            return False

        # Account must be active
        account_status = user.get("account_status", "active")

        if account_status != "active":
            return False

        # Plan must be Pro
        if str(user.get("plan", "")).lower() != "pro":
            return False

        # Check expiration
        expires_at = user.get("plan_expires_at")

        if expires_at is not None:
            from datetime import datetime

            if expires_at <= datetime.now():
                return False

        return True

    except Exception as e:
        print("PRO CHECK ERROR:", e)
        return False

    finally:
        if cursor:
            cursor.close()

        if connection:
            connection.close()


def _require_pro_page():
    """
    Used for normal page requests.
    Redirect non-Pro users to upgrade page.
    """

    user_id = _get_user_id()

    if not user_id:
        return redirect(url_for("auth.login"))

    if not _is_pro_user():
        return redirect("/upgrade")

    return None


def _require_pro_api():
    """
    Used for API requests.
    """

    user_id = _get_user_id()

    if not user_id:
        return jsonify({
            "success": False,
            "error": "Please log in first."
        }), 401

    if not _is_pro_user():
        return jsonify({
            "success": False,
            "error": "This is a Pro feature. Please upgrade your plan."
        }), 403

    return None


def _allowed_file(filename):
    """
    Check whether the uploaded file is a PDF.
    """

    if not filename:
        return False

    extension = Path(filename).suffix.lower()

    return extension in ALLOWED_EXTENSIONS


def _encode_token(job_id, filename):
    """
    Create a URL-safe token containing job ID and filename.
    """

    raw = f"{job_id}:{filename}".encode("utf-8")

    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _decode_token(token):
    """
    Decode a preview/download token.

    Returns:
        (job_id, filename)
    """

    try:
        raw = base64.urlsafe_b64decode(
            token.encode("utf-8")
        ).decode("utf-8")

        job_id, filename = raw.split(":", 1)

        return job_id, filename

    except Exception:
        return None, None


def _get_processed_pdf(token):
    """
    Safely resolve a processed PDF from its token.

    Returns:
        (Path, filename)
        or
        (None, None)
    """

    job_id, filename = _decode_token(token)

    if not job_id or not filename:
        return None, None

    # Security: job ID must be a UUID-like generated ID
    safe_job_id = secure_filename(job_id)

    if safe_job_id != job_id:
        return None, None

    safe_filename = secure_filename(filename)

    if not safe_filename:
        return None, None

    job_directory = OUTPUT_DIR / safe_job_id

    output_path = job_directory / safe_filename

    # Prevent path traversal
    try:
        output_path.resolve().relative_to(
            OUTPUT_DIR.resolve()
        )
    except ValueError:
        return None, None

    return output_path, safe_filename


def _cleanup_directory(path):
    """
    Remove a temporary job directory.
    """

    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        print("CLEANUP ERROR:", e)


# ============================================================
# PAGE
# ============================================================

@protect_unlock_bp.route("/protect-unlock-pdf")
@protect_unlock_bp.route("/protect-unlock-pdf/")
def protect_unlock_page():
    """
    Protect / Unlock PDF page.
    Pro users only.
    """

    denied = _require_pro_page()

    if denied:
        return denied

    return render_template(
        "protect_unlock.html"
    )


# ============================================================
# PROCESS PDF
# ============================================================

@protect_unlock_bp.route(
    "/api/protect-unlock-pdf/process",
    methods=["POST"]
)
def process_protect_unlock():
    """
    Protect or unlock a PDF.
    """

    denied = _require_pro_api()

    if denied:
        return denied

    temp_input = None
    job_directory = None

    try:
        # ----------------------------------------------------
        # Check upload
        # ----------------------------------------------------

        uploaded_file = request.files.get("file")

        if not uploaded_file:
            return jsonify({
                "success": False,
                "error": "Please select a PDF file."
            }), 400

        original_filename = uploaded_file.filename or ""

        if not _allowed_file(original_filename):
            return jsonify({
                "success": False,
                "error": "Only PDF files are allowed."
            }), 400

        # ----------------------------------------------------
        # Check size
        # ----------------------------------------------------

        uploaded_file.stream.seek(0, os.SEEK_END)

        file_size = uploaded_file.stream.tell()

        uploaded_file.stream.seek(0)

        if file_size > MAX_FILE_SIZE:
            return jsonify({
                "success": False,
                "error": "File size cannot exceed 150 MB."
            }), 413

        if file_size <= 0:
            return jsonify({
                "success": False,
                "error": "The uploaded PDF is empty."
            }), 400

        # ----------------------------------------------------
        # Read mode
        # ----------------------------------------------------

        mode = (
            request.form.get("mode", "protect")
            .strip()
            .lower()
        )

        if mode not in {"protect", "unlock"}:
            return jsonify({
                "success": False,
                "error": "Invalid operation."
            }), 400

        # ----------------------------------------------------
        # Passwords
        # ----------------------------------------------------

        password = request.form.get(
            "password",
            ""
        )

        confirm_password = request.form.get(
            "confirm_password",
            ""
        )

        unlock_password = request.form.get(
            "unlock_password",
            ""
        )

        # ----------------------------------------------------
        # Protect validation
        # ----------------------------------------------------

        if mode == "protect":

            if not password:
                return jsonify({
                    "success": False,
                    "error": "Please enter a password."
                }), 400

            if len(password) < 4:
                return jsonify({
                    "success": False,
                    "error": "Password must contain at least 4 characters."
                }), 400

            if password != confirm_password:
                return jsonify({
                    "success": False,
                    "error": "Passwords do not match."
                }), 400

        # ----------------------------------------------------
        # Unlock validation
        # ----------------------------------------------------

        if mode == "unlock":

            if not unlock_password:
                return jsonify({
                    "success": False,
                    "error": "Please enter the PDF password."
                }), 400

        # ----------------------------------------------------
        # Create job directory
        # ----------------------------------------------------

        job_id = uuid.uuid4().hex

        job_directory = OUTPUT_DIR / job_id

        job_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Save uploaded file
        # ----------------------------------------------------

        safe_original_name = secure_filename(
            original_filename
        )

        if not safe_original_name:
            safe_original_name = "document.pdf"

        input_filename = (
            f"input_{uuid.uuid4().hex}.pdf"
        )

        temp_input = job_directory / input_filename

        uploaded_file.save(str(temp_input))

        # ----------------------------------------------------
        # Output filename
        # ----------------------------------------------------

        original_stem = Path(
            safe_original_name
        ).stem

        if mode == "protect":
            output_filename = (
                f"{original_stem}-protected.pdf"
            )
        else:
            output_filename = (
                f"{original_stem}-unlocked.pdf"
            )

        output_path = job_directory / output_filename

        # ----------------------------------------------------
        # Process
        # ----------------------------------------------------

        if mode == "protect":

            protect_pdf(
                input_path=str(temp_input),
                output_path=str(output_path),
                password=password
            )

        else:

            unlock_pdf(
                input_path=str(temp_input),
                output_path=str(output_path),
                password=unlock_password
            )

        # ----------------------------------------------------
        # Verify output
        # ----------------------------------------------------

        if not output_path.exists():
            raise RuntimeError(
                "PDF processing failed."
            )

        output_size = output_path.stat().st_size

        if output_size <= 0:
            raise RuntimeError(
                "The generated PDF is empty."
            )

        # ----------------------------------------------------
        # Remove input file
        # ----------------------------------------------------

        try:
            if temp_input.exists():
                temp_input.unlink()
        except Exception:
            pass

        # ----------------------------------------------------
        # Create token
        # ----------------------------------------------------

        token = _encode_token(
            job_id,
            output_filename
        )

        preview_url = url_for(
            "protect_unlock.preview_protect_unlock",
            token=token
        )

        download_url = url_for(
            "protect_unlock.download_protect_unlock",
            token=token
        )

        return jsonify({
            "success": True,
            "mode": mode,
            "filename": output_filename,
            "size": output_size,
            "token": token,
            "preview_url": preview_url,
            "download_url": download_url
        })

    except ValueError as e:

        if job_directory:
            _cleanup_directory(job_directory)

        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except Exception as e:

        print(
            "PROTECT / UNLOCK ERROR:",
            repr(e)
        )

        if job_directory:
            _cleanup_directory(job_directory)

        return jsonify({
            "success": False,
            "error": (
                "Unable to process the PDF. "
                "Please check the password and try again."
            )
        }), 500


# ============================================================
# PREVIEW PDF
# ============================================================

@protect_unlock_bp.route(
    "/api/protect-unlock-pdf/preview/<token>",
    methods=["GET"]
)
def preview_protect_unlock(token):
    """
    Preview PDF in browser.

    IMPORTANT:
    as_attachment=False
    Content-Disposition=inline

    Therefore this endpoint does NOT download the PDF.
    """

    denied = _require_pro_api()

    if denied:
        return denied

    try:

        output_path, filename = _get_processed_pdf(
            token
        )

        if not output_path:

            return jsonify({
                "success": False,
                "error": "Invalid preview link."
            }), 400

        if not output_path.is_file():

            return jsonify({
                "success": False,
                "error": "Processed PDF is no longer available."
            }), 404

        response = send_file(
            str(output_path),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=filename,
            max_age=0
        )

        # Force browser inline preview
        response.headers["Content-Disposition"] = (
            f'inline; filename="{filename}"'
        )

        response.headers[
            "X-Content-Type-Options"
        ] = "nosniff"

        response.headers[
            "Cache-Control"
        ] = "no-store, no-cache, must-revalidate"

        return response

    except Exception as e:

        print(
            "PREVIEW ERROR:",
            repr(e)
        )

        return jsonify({
            "success": False,
            "error": "Unable to preview PDF."
        }), 500


# ============================================================
# DOWNLOAD PDF
# ============================================================

@protect_unlock_bp.route(
    "/api/protect-unlock-pdf/download/<token>",
    methods=["GET"]
)
def download_protect_unlock(token):
    """
    Download processed PDF.

    IMPORTANT:
    as_attachment=True

    This is the ONLY endpoint that forces downloading.
    """

    denied = _require_pro_api()

    if denied:
        return denied

    try:

        output_path, filename = _get_processed_pdf(
            token
        )

        if not output_path:

            return jsonify({
                "success": False,
                "error": "Invalid download link."
            }), 400

        if not output_path.is_file():

            return jsonify({
                "success": False,
                "error": "Processed PDF is no longer available."
            }), 404

        return send_file(
            str(output_path),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:

        print(
            "DOWNLOAD ERROR:",
            repr(e)
        )

        return jsonify({
            "success": False,
            "error": "Unable to download PDF."
        }), 500