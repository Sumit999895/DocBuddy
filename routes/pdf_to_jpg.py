# ============================================================
# PDF TO JPG ROUTES
# Temporary filesystem storage - NO DATABASE
# ============================================================

import os
import shutil
import threading
import time
import uuid

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session,
    flash,
    url_for,
    send_file,
    jsonify,
)

from werkzeug.utils import secure_filename

from config import (
    POPPLER_PATH,
    CONVERSION_FOLDER,
)

from services.pdf_service import convert_pdf_to_jpg


# ============================================================
# CONFIGURATION
# ============================================================

# Converted files are automatically deleted after this many
# seconds. Change this value if you want a different lifetime.
#
# Examples:
#   10 * 60       = 10 minutes
#   30 * 60       = 30 minutes
#   60 * 60       = 1 hour
#   24 * 60 * 60  = 24 hours
TEMP_FILE_TTL = int(os.getenv("PDF_TO_JPG_TEMP_TTL", 30 * 60))

# How often the background cleanup checks for expired folders.
CLEANUP_INTERVAL = int(
    os.getenv("PDF_TO_JPG_CLEANUP_INTERVAL", 5 * 60)
)


# ============================================================
# BLUEPRINT
# ============================================================

pdf_to_jpg_bp = Blueprint(
    "pdf_to_jpg",
    __name__
)


# ============================================================
# HELPER: LOGIN CHECK
# ============================================================

def user_logged_in():
    return "user" in session


# ============================================================
# HELPER: GET USER ID
# ============================================================

def get_current_user_id():
    if "user" not in session:
        return None

    return session["user"].get("id")


# ============================================================
# HELPER: USER TEMP FOLDER
# ============================================================

def get_user_temp_folder(user_id):
    """
    Returns the temporary filesystem folder for the logged-in user.

    Nothing is stored in MySQL. The user ID is only used to keep
    different users' temporary files separated on disk.
    """
    if user_id is None:
        return None

    return os.path.realpath(
        os.path.join(
            CONVERSION_FOLDER,
            str(user_id)
        )
    )


# ============================================================
# HELPER: CLEAN EXPIRED CONVERSIONS
# ============================================================

def cleanup_expired_conversions():
    """
    Delete temporary PDF/JPG conversion folders older than TTL.

    Folder modification time is used as the expiration time.
    The original PDF is also removed immediately after conversion,
    while this cleanup removes the generated JPG files.
    """
    base_folder = os.path.realpath(CONVERSION_FOLDER)

    if not os.path.isdir(base_folder):
        return

    now = time.time()

    try:
        user_folders = os.listdir(base_folder)
    except OSError:
        return

    for user_folder_name in user_folders:
        user_folder = os.path.join(
            base_folder,
            user_folder_name
        )

        if not os.path.isdir(user_folder):
            continue

        try:
            conversion_folders = os.listdir(user_folder)
        except OSError:
            continue

        for conversion_folder_name in conversion_folders:
            conversion_folder = os.path.realpath(
                os.path.join(
                    user_folder,
                    conversion_folder_name
                )
            )

            # Never allow cleanup to escape CONVERSION_FOLDER.
            if not conversion_folder.startswith(
                base_folder + os.sep
            ):
                continue

            if not os.path.isdir(conversion_folder):
                continue

            try:
                age = now - os.path.getmtime(
                    conversion_folder
                )

                if age >= TEMP_FILE_TTL:
                    shutil.rmtree(
                        conversion_folder,
                        ignore_errors=True
                    )

                    print(
                        "Temporary PDF → JPG conversion deleted:",
                        conversion_folder
                    )

            except OSError:
                pass

        # Remove an empty user directory.
        try:
            if os.path.isdir(user_folder) and not os.listdir(
                user_folder
            ):
                os.rmdir(user_folder)
        except OSError:
            pass


# ============================================================
# BACKGROUND CLEANUP WORKER
# ============================================================

def _cleanup_worker():
    """
    Periodically removes expired temporary conversion folders.

    This is best-effort cleanup. The application also calls the
    cleanup function on requests, so files are still cleaned when
    the process has no background worker.
    """
    while True:
        try:
            cleanup_expired_conversions()
        except Exception as e:
            print(
                "Temporary file cleanup error:",
                repr(e)
            )

        time.sleep(
            max(60, CLEANUP_INTERVAL)
        )


def start_cleanup_worker():
    """
    Start one daemon cleanup thread per Python process.

    In Flask's development server, the reloader starts the actual
    application process with WERKZEUG_RUN_MAIN=true, so this avoids
    starting an unnecessary duplicate worker.
    """
    if os.environ.get("WERKZEUG_RUN_MAIN") not in (
        None,
        "true",
    ):
        return

    worker = threading.Thread(
        target=_cleanup_worker,
        name="pdf-to-jpg-cleanup",
        daemon=True,
    )

    worker.start()


# Start cleanup when this module is loaded.
start_cleanup_worker()


# ============================================================
# HELPER: GET CONVERSION PAGES
# ============================================================

def get_conversion_pages(output_folder):
    """
    Return JPG pages currently present in a temporary conversion
    folder.
    """
    pages = []

    if not output_folder:
        return pages

    folder = os.path.realpath(output_folder)

    if not os.path.isdir(folder):
        return pages

    try:
        filenames = os.listdir(folder)
    except OSError:
        return pages

    for filename in filenames:
        if not filename.lower().endswith(".jpg"):
            continue

        if not filename.startswith("page_"):
            continue

        try:
            page_number = int(
                filename[5:-4]
            )
        except ValueError:
            continue

        pages.append({
            "filename": filename,
            "page_number": page_number,
        })

    pages.sort(
        key=lambda page: page["page_number"]
    )

    return pages


# ============================================================
# HELPER: BUILD TEMPORARY CONVERSION OBJECT
# ============================================================

def get_conversion(
    conversion_id,
    user_id,
):
    """
    Find a temporary conversion by UUID.

    There is NO database lookup.

    The conversion is valid only if:
      1. It belongs to the current user's temp folder.
      2. The UUID is valid.
      3. The conversion folder still exists.
      4. It has not expired.
    """
    if not user_id or not conversion_id:
        return None

    try:
        conversion_uuid = uuid.UUID(
            str(conversion_id)
        ).hex
    except (ValueError, AttributeError):
        return None

    user_folder = get_user_temp_folder(
        user_id
    )

    if not user_folder:
        return None

    conversion_folder = os.path.realpath(
        os.path.join(
            user_folder,
            conversion_uuid
        )
    )

    # Prevent path traversal.
    if not conversion_folder.startswith(
        user_folder + os.sep
    ):
        return None

    if not os.path.isdir(
        conversion_folder
    ):
        return None

    try:
        age = time.time() - os.path.getmtime(
            conversion_folder
        )

        if age >= TEMP_FILE_TTL:
            shutil.rmtree(
                conversion_folder,
                ignore_errors=True
            )
            return None
    except OSError:
        return None

    pages = get_conversion_pages(
        conversion_folder
    )

    if not pages:
        return None

    original_filename = "Converted PDF"

    # Store the original filename in a tiny text file.
    # This is temporary filesystem metadata, NOT database storage.
    metadata_file = os.path.join(
        conversion_folder,
        ".original_filename"
    )

    try:
        if os.path.isfile(metadata_file):
            with open(
                metadata_file,
                "r",
                encoding="utf-8"
            ) as f:
                original_filename = (
                    f.read().strip()
                    or original_filename
                )
    except OSError:
        pass

    return {
        "id": conversion_uuid,
        "original_filename": original_filename,
        "conversion_type": "PDF → JPG",
        "output_folder": conversion_folder,
        "page_count": len(pages),
        "created_at": os.path.getmtime(
            conversion_folder
        ),
        "expires_at": (
            os.path.getmtime(conversion_folder)
            + TEMP_FILE_TTL
        ),
    }


# ============================================================
# HELPER: GET TEMPORARY HISTORY
# ============================================================

def get_pdf_to_jpg_history(
    user_id,
    limit=10,
):
    """
    Return currently available temporary conversions.

    This replaces the old ConversionHistory MySQL table.

    IMPORTANT:
    This is NOT permanent history. It is reconstructed from the
    temporary filesystem folders and disappears automatically
    after TEMP_FILE_TTL.
    """
    cleanup_expired_conversions()

    user_folder = get_user_temp_folder(
        user_id
    )

    if not user_folder:
        return []

    if not os.path.isdir(user_folder):
        return []

    history = []

    try:
        folder_names = os.listdir(
            user_folder
        )
    except OSError:
        return []

    for folder_name in folder_names:
        conversion = get_conversion(
            folder_name,
            user_id
        )

        if conversion:
            history.append(
                conversion
            )

    history.sort(
        key=lambda item: item.get(
            "created_at",
            0
        ),
        reverse=True
    )

    return history[:limit]


# ============================================================
# PDF → JPG
# ============================================================

@pdf_to_jpg_bp.route(
    "/pdf-to-jpg",
    methods=["GET", "POST"]
)
def pdf_to_jpg():

    # --------------------------------------------------------
    # CLEAN EXPIRED TEMPORARY FILES
    # --------------------------------------------------------

    cleanup_expired_conversions()

    # --------------------------------------------------------
    # LOGIN CHECK
    # --------------------------------------------------------

    if not user_logged_in():
        return redirect(
            url_for("home")
        )

    user_id = get_current_user_id()

    # --------------------------------------------------------
    # GET TEMPORARY HISTORY
    # --------------------------------------------------------

    history = get_pdf_to_jpg_history(
        user_id
    )

    # --------------------------------------------------------
    # GET REQUEST
    # --------------------------------------------------------

    if request.method == "GET":
        return render_template(
            "pdf_to_jpg.html",
            conversion=None,
            pages=[],
            history=history,
            conversion_success=False,
        )

    # --------------------------------------------------------
    # CHECK FILE
    # --------------------------------------------------------

    if "file" not in request.files:
        flash(
            "Please select a PDF file.",
            "error"
        )

        return render_template(
            "pdf_to_jpg.html",
            conversion=None,
            pages=[],
            history=history,
            conversion_success=False,
        )

    file = request.files["file"]

    if not file or not file.filename:
        flash(
            "Please select a PDF file.",
            "error"
        )

        return render_template(
            "pdf_to_jpg.html",
            conversion=None,
            pages=[],
            history=history,
            conversion_success=False,
        )

    # --------------------------------------------------------
    # ORIGINAL FILENAME
    # --------------------------------------------------------

    original_filename = (
        file.filename.strip()
    )

    # --------------------------------------------------------
    # PDF CHECK
    # --------------------------------------------------------

    if not original_filename.lower().endswith(
        ".pdf"
    ):
        flash(
            "Only PDF files can be converted to JPG.",
            "error"
        )

        return render_template(
            "pdf_to_jpg.html",
            conversion=None,
            pages=[],
            history=history,
            conversion_success=False,
        )

    # --------------------------------------------------------
    # SECURE FILENAME
    # --------------------------------------------------------

    filename = secure_filename(
        original_filename
    )

    if not filename:
        flash(
            "Invalid PDF filename.",
            "error"
        )

        return render_template(
            "pdf_to_jpg.html",
            conversion=None,
            pages=[],
            history=history,
            conversion_success=False,
        )

    # --------------------------------------------------------
    # CREATE UNIQUE TEMPORARY CONVERSION FOLDER
    # --------------------------------------------------------

    conversion_uuid = uuid.uuid4().hex

    user_folder = get_user_temp_folder(
        user_id
    )

    conversion_folder = os.path.realpath(
        os.path.join(
            user_folder,
            conversion_uuid
        )
    )

    # Security check.
    if not conversion_folder.startswith(
        user_folder + os.sep
    ):
        flash(
            "Could not create temporary storage.",
            "error"
        )

        return render_template(
            "pdf_to_jpg.html",
            conversion=None,
            pages=[],
            history=history,
            conversion_success=False,
        )

    os.makedirs(
        conversion_folder,
        exist_ok=True
    )

    # --------------------------------------------------------
    # TEMPORARY PDF
    # --------------------------------------------------------

    temp_pdf = os.path.join(
        conversion_folder,
        "_source.pdf"
    )

    try:

        # ----------------------------------------------------
        # SAVE UPLOADED PDF TEMPORARILY
        # ----------------------------------------------------

        file.save(
            temp_pdf
        )

        print(
            "========================================"
        )

        print(
            "PDF TO JPG TEMPORARY UPLOAD"
        )

        print(
            "Original filename:",
            original_filename
        )

        print(
            "Temporary PDF:",
            temp_pdf
        )

        print(
            "Temporary folder:",
            conversion_folder
        )

        print(
            "Auto-delete after:",
            TEMP_FILE_TTL,
            "seconds"
        )

        print(
            "PDF exists:",
            os.path.isfile(temp_pdf)
        )

        if os.path.isfile(temp_pdf):
            print(
                "PDF size:",
                os.path.getsize(temp_pdf)
            )

        print(
            "Poppler:",
            POPPLER_PATH
        )

        print(
            "pdftoppm:",
            os.path.join(
                POPPLER_PATH,
                "pdftoppm.exe"
            )
        )

        print(
            "========================================"
        )

        # ----------------------------------------------------
        # CHECK POPPLER
        # ----------------------------------------------------

        pdftoppm = os.path.join(
            POPPLER_PATH,
            "pdftoppm.exe"
        )

        if not os.path.isfile(
            pdftoppm
        ):
            raise RuntimeError(
                "Poppler pdftoppm.exe was not found at: "
                + pdftoppm
            )

        # ----------------------------------------------------
        # CHECK SAVED PDF
        # ----------------------------------------------------

        if not os.path.isfile(
            temp_pdf
        ):
            raise RuntimeError(
                "The uploaded PDF could not be saved."
            )

        if os.path.getsize(
            temp_pdf
        ) == 0:
            raise RuntimeError(
                "The uploaded PDF is empty."
            )

        # ----------------------------------------------------
        # CONVERT PDF
        # ----------------------------------------------------

        converted_pages = (
            convert_pdf_to_jpg(
                temp_pdf,
                conversion_folder,
                POPPLER_PATH
            )
        )

        # ----------------------------------------------------
        # PAGE COUNT
        # ----------------------------------------------------

        page_count = len(
            converted_pages
        )

        if page_count == 0:
            raise RuntimeError(
                "No JPG pages were created."
            )

        # ----------------------------------------------------
        # REMOVE SOURCE PDF IMMEDIATELY
        # ----------------------------------------------------

        try:
            os.remove(
                temp_pdf
            )
        except OSError:
            pass

        # ----------------------------------------------------
        # SAVE TEMPORARY METADATA
        # ----------------------------------------------------

        metadata_file = os.path.join(
            conversion_folder,
            ".original_filename"
        )

        with open(
            metadata_file,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(
                original_filename
            )

        # ----------------------------------------------------
        # REFRESH FOLDER MODIFICATION TIME
        # ----------------------------------------------------

        now = time.time()

        os.utime(
            conversion_folder,
            (now, now)
        )

        # ----------------------------------------------------
        # CURRENT TEMPORARY CONVERSION
        # ----------------------------------------------------

        conversion = {
            "id": conversion_uuid,
            "original_filename":
                original_filename,
            "conversion_type":
                "PDF → JPG",
            "output_folder":
                conversion_folder,
            "page_count":
                page_count,
            "created_at":
                now,
            "expires_at":
                now + TEMP_FILE_TTL,
        }

        # ----------------------------------------------------
        # TEMPORARY HISTORY
        # ----------------------------------------------------

        history = (
            get_pdf_to_jpg_history(
                user_id
            )
        )

        print(
            f"PDF converted successfully: "
            f"{original_filename} "
            f"({page_count} pages)"
        )

        print(
            "Temporary conversion ID:",
            conversion_uuid
        )

        print(
            "Expires in:",
            TEMP_FILE_TTL,
            "seconds"
        )

        # ----------------------------------------------------
        # RENDER SAME PAGE
        # ----------------------------------------------------

        return render_template(
            "pdf_to_jpg.html",
            conversion=conversion,
            pages=converted_pages,
            history=history,
            conversion_success=True,
        )

    # ========================================================
    # GENERAL ERROR
    # ========================================================

    except Exception as e:

        print(
            "========================================"
        )

        print(
            "PDF → JPG CONVERSION ERROR"
        )

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR:",
            str(e)
        )

        print(
            "========================================"
        )

        # ----------------------------------------------------
        # CLEAN FAILED TEMPORARY CONVERSION
        # ----------------------------------------------------

        try:
            if os.path.isdir(
                conversion_folder
            ):
                shutil.rmtree(
                    conversion_folder,
                    ignore_errors=True
                )
        except Exception as cleanup_error:
            print(
                "Conversion cleanup error:",
                repr(cleanup_error)
            )

        # ----------------------------------------------------
        # USER-FRIENDLY ERROR
        # ----------------------------------------------------

        error_message = str(
            e
        ).strip()

        if not error_message:
            error_message = (
                "Unknown conversion error."
            )

        if len(
            error_message
        ) > 300:
            error_message = (
                error_message[:300]
                + "..."
            )

        flash(
            "Could not convert the PDF: "
            + error_message,
            "error"
        )

        return render_template(
            "pdf_to_jpg.html",
            conversion=None,
            pages=[],
            history=get_pdf_to_jpg_history(
                user_id
            ),
            conversion_success=False,
        )


# ============================================================
# PDF → JPG RESULT
# ============================================================

@pdf_to_jpg_bp.route(
    "/pdf-to-jpg/result/<conversion_id>"
)
def pdf_to_jpg_result(
    conversion_id
):

    # --------------------------------------------------------
    # CLEAN EXPIRED FILES
    # --------------------------------------------------------

    cleanup_expired_conversions()

    # --------------------------------------------------------
    # LOGIN CHECK
    # --------------------------------------------------------

    if not user_logged_in():
        return redirect(
            url_for("home")
        )

    user_id = get_current_user_id()

    try:

        conversion = get_conversion(
            conversion_id,
            user_id
        )

        if not conversion:
            flash(
                "Conversion not found or has expired.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )

        pages = get_conversion_pages(
            conversion[
                "output_folder"
            ]
        )

        history = (
            get_pdf_to_jpg_history(
                user_id
            )
        )

        return render_template(
            "pdf_to_jpg.html",
            conversion=conversion,
            pages=pages,
            history=history,
            conversion_success=False,
        )

    except Exception as e:

        print(
            "Conversion result error:",
            repr(e)
        )

        flash(
            "Could not load the conversion.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )


# ============================================================
# VIEW CONVERTED JPG
# ============================================================

@pdf_to_jpg_bp.route(
    "/pdf-to-jpg/view/<conversion_id>/<filename>"
)
def view_converted_jpg(
    conversion_id,
    filename
):

    # --------------------------------------------------------
    # LOGIN CHECK
    # --------------------------------------------------------

    if not user_logged_in():
        return redirect(
            url_for("home")
        )

    user_id = get_current_user_id()

    try:

        conversion = get_conversion(
            conversion_id,
            user_id
        )

        if not conversion:
            return (
                "Conversion not found or expired.",
                404
            )

        safe_filename = secure_filename(
            filename
        )

        # ----------------------------------------------------
        # SECURITY CHECK
        # ----------------------------------------------------

        if (
            safe_filename != filename
            or not safe_filename.lower().endswith(
                ".jpg"
            )
        ):
            return (
                "Invalid image.",
                400
            )

        folder = os.path.realpath(
            conversion[
                "output_folder"
            ]
        )

        file_path = os.path.realpath(
            os.path.join(
                folder,
                safe_filename
            )
        )

        # ----------------------------------------------------
        # PREVENT PATH TRAVERSAL
        # ----------------------------------------------------

        if not file_path.startswith(
            folder + os.sep
        ):
            return (
                "Invalid path.",
                403
            )

        # ----------------------------------------------------
        # FILE EXISTS
        # ----------------------------------------------------

        if not os.path.isfile(
            file_path
        ):
            return (
                "Image not found or expired.",
                404
            )

        # ----------------------------------------------------
        # DISPLAY IMAGE IN BROWSER
        # ----------------------------------------------------

        return send_file(
            file_path,
            mimetype="image/jpeg",
            as_attachment=False
        )

    except Exception as e:

        print(
            "View JPG error:",
            repr(e)
        )

        return (
            "Could not display image.",
            500
        )


# ============================================================
# DOWNLOAD INDIVIDUAL JPG
# ============================================================

@pdf_to_jpg_bp.route(
    "/pdf-to-jpg/download/<conversion_id>/<filename>"
)
def download_converted_jpg(
    conversion_id,
    filename
):

    # --------------------------------------------------------
    # LOGIN CHECK
    # --------------------------------------------------------

    if not user_logged_in():
        return redirect(
            url_for("home")
        )

    user_id = get_current_user_id()

    try:

        conversion = get_conversion(
            conversion_id,
            user_id
        )

        if not conversion:
            return (
                "Conversion not found or expired.",
                404
            )

        safe_filename = secure_filename(
            filename
        )

        # ----------------------------------------------------
        # SECURITY CHECK
        # ----------------------------------------------------

        if (
            safe_filename != filename
            or not safe_filename.lower().endswith(
                ".jpg"
            )
        ):
            return (
                "Invalid image.",
                400
            )

        folder = os.path.realpath(
            conversion[
                "output_folder"
            ]
        )

        file_path = os.path.realpath(
            os.path.join(
                folder,
                safe_filename
            )
        )

        if not file_path.startswith(
            folder + os.sep
        ):
            return (
                "Invalid path.",
                403
            )

        if not os.path.isfile(
            file_path
        ):
            return (
                "Image not found or expired.",
                404
            )

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        return send_file(
            file_path,
            mimetype="image/jpeg",
            as_attachment=True,
            download_name=safe_filename
        )

    except Exception as e:

        print(
            "Download JPG error:",
            repr(e)
        )

        return (
            "Could not download image.",
            500
        )


# ============================================================
# DELETE INDIVIDUAL JPG
# ============================================================

@pdf_to_jpg_bp.route(
    "/delete-converted-jpg/<conversion_id>/<filename>",
    methods=["POST"]
)
def delete_converted_jpg(
    conversion_id,
    filename
):

    if not user_logged_in():
        return jsonify({
            "success": False,
            "message":
                "Please log in first."
        }), 401

    user_id = get_current_user_id()

    try:

        # ----------------------------------------------------
        # GET TEMPORARY CONVERSION
        # ----------------------------------------------------

        conversion = get_conversion(
            conversion_id,
            user_id
        )

        if not conversion:
            return jsonify({
                "success": False,
                "message":
                    "Conversion not found or expired."
            }), 404

        # ----------------------------------------------------
        # SAFE FILENAME
        # ----------------------------------------------------

        safe_filename = secure_filename(
            filename
        )

        if (
            safe_filename != filename
            or not safe_filename.lower().endswith(
                ".jpg"
            )
        ):
            return jsonify({
                "success": False,
                "message":
                    "Invalid image."
            }), 400

        # ----------------------------------------------------
        # GET FOLDER
        # ----------------------------------------------------

        folder = os.path.realpath(
            conversion[
                "output_folder"
            ]
        )

        file_path = os.path.realpath(
            os.path.join(
                folder,
                safe_filename
            )
        )

        # ----------------------------------------------------
        # SECURITY CHECK
        # ----------------------------------------------------

        if not file_path.startswith(
            folder + os.sep
        ):
            return jsonify({
                "success": False,
                "message":
                    "Invalid path."
            }), 403

        # ----------------------------------------------------
        # CHECK FILE
        # ----------------------------------------------------

        if not os.path.isfile(
            file_path
        ):
            return jsonify({
                "success": False,
                "message":
                    "Image not found or expired."
            }), 404

        # ----------------------------------------------------
        # DELETE IMAGE
        # ----------------------------------------------------

        os.remove(
            file_path
        )

        # ----------------------------------------------------
        # COUNT REMAINING PAGES
        # ----------------------------------------------------

        remaining_pages = (
            get_conversion_pages(
                folder
            )
        )

        remaining_count = len(
            remaining_pages
        )

        # ----------------------------------------------------
        # DELETE CONVERSION WHEN EMPTY
        # ----------------------------------------------------

        if remaining_count == 0:

            try:
                if os.path.isdir(
                    folder
                ):
                    shutil.rmtree(
                        folder,
                        ignore_errors=True
                    )
            except OSError:
                pass

            message = (
                "Image deleted. "
                "Temporary conversion removed."
            )

        else:
            message = (
                "Image deleted successfully."
            )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        return jsonify({
            "success": True,
            "message": message,
            "remaining_pages":
                remaining_count
        })

    except Exception as e:

        print(
            "JPG delete error:",
            repr(e)
        )

        return jsonify({
            "success": False,
            "message":
                "Could not delete the image."
        }), 500
