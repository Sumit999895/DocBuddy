import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    send_file,
    flash,
)

from werkzeug.utils import secure_filename

from services.jpg_to_png_service import convert_jpg_to_png


# ============================================================
# BLUEPRINT
# ============================================================

jpg_to_png_bp = Blueprint(
    "jpg_to_png",
    __name__,
    url_prefix="/jpg-to-png",
)


# ============================================================
# TEMPORARY STORAGE
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

TEMP_FOLDER = (
    BASE_DIR
    / "temp"
    / "jpg_to_png"
)

TEMP_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# TEMPORARY FILE SETTINGS
# ============================================================

# Default: 30 minutes
#
# You can change this with an environment variable:
#
# JPG_TO_PNG_TEMP_TTL=1800
#
# 1800 seconds = 30 minutes

TEMP_FILE_TTL = int(
    os.getenv(
        "JPG_TO_PNG_TEMP_TTL",
        30 * 60
    )
)


# Check for expired files every 5 minutes.

CLEANUP_INTERVAL = int(
    os.getenv(
        "JPG_TO_PNG_CLEANUP_INTERVAL",
        5 * 60
    )
)


# ============================================================
# FILE SETTINGS
# ============================================================

ALLOWED_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
}

MAX_FILE_SIZE = 25 * 1024 * 1024
# 25 MB


# ============================================================
# HELPERS
# ============================================================

def allowed_file(filename):
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_EXTENSIONS
    )


def get_current_user_folder():

    # --------------------------------------------------------
    # Use Flask-Login user ID when available.
    # Otherwise use a guest folder.
    # --------------------------------------------------------

    try:

        from flask_login import current_user

        if (
            current_user
            and current_user.is_authenticated
        ):

            user_id = secure_filename(
                str(current_user.id)
            )

        else:

            user_id = "guest"

    except Exception:

        user_id = "guest"

    if not user_id:
        user_id = "guest"

    folder = (
        TEMP_FOLDER
        / user_id
    )

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    return folder


def is_valid_uuid(value):

    try:

        uuid.UUID(
            str(value)
        )

        return True

    except (
        ValueError,
        TypeError,
        AttributeError
    ):

        return False


def delete_conversion_folder(folder):

    try:

        folder = Path(folder)

        if folder.exists() and folder.is_dir():

            shutil.rmtree(
                folder,
                ignore_errors=True
            )

    except Exception as exc:

        print(
            "Could not delete temporary conversion:",
            folder,
            repr(exc)
        )


# ============================================================
# METADATA
# ============================================================

def metadata_path(conversion_folder):

    return (
        conversion_folder
        / ".metadata.json"
    )


def write_metadata(
    conversion_folder,
    original_filename,
    output_filename,
    created_at,
):

    metadata = {
        "original_filename": original_filename,
        "output_filename": output_filename,
        "created_at": created_at,
    }

    with open(
        metadata_path(conversion_folder),
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            metadata,
            file,
            indent=4
        )


def read_metadata(conversion_folder):

    path = metadata_path(
        conversion_folder
    )

    if not path.exists():
        return None

    try:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as exc:

        print(
            "Could not read temporary metadata:",
            repr(exc)
        )

        return None


# ============================================================
# AUTOMATIC CLEANUP
# ============================================================

def cleanup_expired_conversions():

    if not TEMP_FOLDER.exists():
        return

    now = time.time()

    # --------------------------------------------------------
    # User folders
    # --------------------------------------------------------

    for user_folder in TEMP_FOLDER.iterdir():

        if not user_folder.is_dir():
            continue

        # ----------------------------------------------------
        # Conversion folders
        # ----------------------------------------------------

        for conversion_folder in user_folder.iterdir():

            if not conversion_folder.is_dir():
                continue

            try:

                created_timestamp = (
                    conversion_folder.stat().st_mtime
                )

                age = (
                    now
                    - created_timestamp
                )

                if age >= TEMP_FILE_TTL:

                    print(
                        "Deleting expired JPG → PNG conversion:",
                        conversion_folder
                    )

                    delete_conversion_folder(
                        conversion_folder
                    )

            except Exception as exc:

                print(
                    "JPG → PNG cleanup error:",
                    repr(exc)
                )

        # ----------------------------------------------------
        # Remove empty user folder
        # ----------------------------------------------------

        try:

            if (
                user_folder.exists()
                and not any(
                    user_folder.iterdir()
                )
            ):

                user_folder.rmdir()

        except Exception:
            pass


_cleanup_thread_started = False
_cleanup_lock = threading.Lock()


def cleanup_worker():

    while True:

        try:

            cleanup_expired_conversions()

        except Exception as exc:

            print(
                "JPG → PNG background cleanup error:",
                repr(exc)
            )

        time.sleep(
            CLEANUP_INTERVAL
        )


def start_cleanup_worker():

    global _cleanup_thread_started

    with _cleanup_lock:

        if _cleanup_thread_started:
            return

        thread = threading.Thread(
            target=cleanup_worker,
            daemon=True,
            name="jpg-to-png-cleanup"
        )

        thread.start()

        _cleanup_thread_started = True

        print(
            "JPG → PNG temporary cleanup worker started."
        )


start_cleanup_worker()


# ============================================================
# FIND TEMPORARY CONVERSION
# ============================================================

def get_conversion(conversion_id):

    if not is_valid_uuid(
        conversion_id
    ):

        return None

    user_folder = (
        get_current_user_folder()
    )

    conversion_folder = (
        user_folder
        / str(conversion_id)
    )

    # --------------------------------------------------------
    # Prevent path traversal
    # --------------------------------------------------------

    try:

        conversion_folder.resolve().relative_to(
            user_folder.resolve()
        )

    except ValueError:

        return None

    # --------------------------------------------------------
    # Check folder
    # --------------------------------------------------------

    if not conversion_folder.exists():
        return None

    if not conversion_folder.is_dir():
        return None

    # --------------------------------------------------------
    # Check expiration
    # --------------------------------------------------------

    try:

        age = (
            time.time()
            - conversion_folder.stat().st_mtime
        )

        if age >= TEMP_FILE_TTL:

            delete_conversion_folder(
                conversion_folder
            )

            return None

    except Exception:

        return None

    # --------------------------------------------------------
    # Read metadata
    # --------------------------------------------------------

    metadata = read_metadata(
        conversion_folder
    )

    if not metadata:
        return None

    # --------------------------------------------------------
    # Output file
    # --------------------------------------------------------

    output_path = (
        conversion_folder
        / "converted.png"
    )

    if not output_path.exists():
        return None

    if not output_path.is_file():
        return None

    return {
        "id": str(conversion_id),
        "folder": conversion_folder,
        "output_path": output_path,
        "metadata": metadata,
    }


# ============================================================
# TEMPORARY HISTORY
# ============================================================

def get_history():

    cleanup_expired_conversions()

    user_folder = (
        get_current_user_folder()
    )

    history = []

    if not user_folder.exists():
        return history

    for conversion_folder in user_folder.iterdir():

        if not conversion_folder.is_dir():
            continue

        metadata = read_metadata(
            conversion_folder
        )

        if not metadata:
            continue

        output_path = (
            conversion_folder
            / "converted.png"
        )

        if not output_path.exists():
            continue

        try:

            timestamp = (
                conversion_folder.stat().st_mtime
            )

        except Exception:

            timestamp = 0

        history.append({
            "id": conversion_folder.name,

            "original_filename":
                metadata.get(
                    "original_filename",
                    "image"
                ),

            "output_filename":
                metadata.get(
                    "output_filename",
                    "converted.png"
                ),

            "created_at":
                metadata.get(
                    "created_at",
                    ""
                ),

            "_timestamp":
                timestamp,
        })

    history.sort(
        key=lambda item: item["_timestamp"],
        reverse=True
    )

    # --------------------------------------------------------
    # Do not expose internal timestamp
    # --------------------------------------------------------

    for item in history:

        item.pop(
            "_timestamp",
            None
        )

    return history


# ============================================================
# JPG TO PNG PAGE
# ============================================================

@jpg_to_png_bp.route(
    "/",
    methods=["GET"],
)
def jpg_to_png():

    history = get_history()

    return render_template(
        "jpg_to_png.html",
        history=history,
    )


# ============================================================
# CONVERT JPG TO PNG
# ============================================================

@jpg_to_png_bp.route(
    "/convert",
    methods=["POST"],
)
def convert():

    cleanup_expired_conversions()

    uploaded_file = request.files.get(
        "file"
    )

    # --------------------------------------------------------
    # NO FILE
    # --------------------------------------------------------

    if not uploaded_file:

        flash(
            "Please choose an image file.",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # EMPTY FILE
    # --------------------------------------------------------

    if not uploaded_file.filename:

        flash(
            "Please choose an image file.",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # FILE TYPE
    # --------------------------------------------------------

    if not allowed_file(
        uploaded_file.filename
    ):

        flash(
            "Only JPG, JPEG or PNG files are allowed.",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # ORIGINAL FILENAME
    # --------------------------------------------------------

    original_filename = secure_filename(
        uploaded_file.filename
    )

    if not original_filename:

        flash(
            "Invalid file name.",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # CREATE UNIQUE TEMPORARY FOLDER
    # --------------------------------------------------------

    conversion_id = str(
        uuid.uuid4()
    )

    user_folder = (
        get_current_user_folder()
    )

    conversion_folder = (
        user_folder
        / conversion_id
    )

    conversion_folder.mkdir(
        parents=True,
        exist_ok=False
    )

    # --------------------------------------------------------
    # TEMPORARY INPUT
    # --------------------------------------------------------
    #
    # IMPORTANT:
    # Do NOT use:
    #
    # conversion_folder / "_source" + ".jpg"
    #
    # A Path object cannot be added to a string.
    #
    # Correct construction:
    #

    input_path = (
        conversion_folder
        / f"_source{Path(original_filename).suffix.lower()}"
    )

    # --------------------------------------------------------
    # TEMPORARY OUTPUT
    # --------------------------------------------------------

    original_stem = Path(
        original_filename
    ).stem

    output_filename = (
        f"{original_stem}.png"
    )

    output_path = (
        conversion_folder
        / "converted.png"
    )

    # --------------------------------------------------------
    # SAVE INPUT
    # --------------------------------------------------------

    try:

        uploaded_file.save(
            str(input_path)
        )

        if not input_path.exists():

            raise RuntimeError(
                "The uploaded image could not be saved."
            )

        file_size = (
            input_path.stat().st_size
        )

        if file_size == 0:

            raise RuntimeError(
                "The uploaded image is empty."
            )

        if file_size > MAX_FILE_SIZE:

            raise RuntimeError(
                "The image is larger than the allowed 25 MB limit."
            )

    except Exception as exc:

        print("=" * 80)
        print("JPG TO PNG UPLOAD ERROR")
        print(f"Error: {repr(exc)}")
        print("=" * 80)

        delete_conversion_folder(
            conversion_folder
        )

        flash(
            f"Could not save the uploaded image. Error: {exc}",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # CONVERSION
    # --------------------------------------------------------

    try:

        convert_jpg_to_png(
            input_path,
            output_path,
        )

    except Exception as exc:

        print("=" * 80)
        print("JPG TO PNG CONVERSION ERROR")
        print(f"Input : {input_path}")
        print(f"Output: {output_path}")
        print(f"Error : {repr(exc)}")
        print("=" * 80)

        delete_conversion_folder(
            conversion_folder
        )

        flash(
            f"Image could not be converted to PNG. Error: {exc}",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # VERIFY OUTPUT
    # --------------------------------------------------------

    try:

        if not output_path.exists():

            raise RuntimeError(
                "PNG file was not created."
            )

        if output_path.stat().st_size == 0:

            raise RuntimeError(
                "Generated PNG file is empty."
            )

    except Exception as exc:

        print("=" * 80)
        print("JPG TO PNG OUTPUT ERROR")
        print(f"Error: {repr(exc)}")
        print("=" * 80)

        delete_conversion_folder(
            conversion_folder
        )

        flash(
            f"PNG output is invalid. Error: {exc}",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # ORIGINAL INPUT IS NO LONGER NEEDED
    # --------------------------------------------------------
    #
    # The page previews the converted PNG, not the uploaded
    # source image. Therefore the source can be removed
    # immediately after successful conversion.
    #

    try:

        if input_path.exists():

            input_path.unlink()

    except Exception as exc:

        print(
            "Could not remove temporary source image:",
            repr(exc)
        )

    # --------------------------------------------------------
    # WRITE TEMPORARY METADATA
    # --------------------------------------------------------

    created_at = (
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        write_metadata(
            conversion_folder,
            original_filename,
            output_filename,
            created_at,
        )

    except Exception as exc:

        print("=" * 80)
        print("JPG TO PNG METADATA ERROR")
        print(f"Error: {repr(exc)}")
        print("=" * 80)

        delete_conversion_folder(
            conversion_folder
        )

        flash(
            f"Could not create temporary conversion record. Error: {exc}",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    flash(
        "Image converted to PNG successfully!",
        "success",
    )

    return redirect(
        url_for(
            "jpg_to_png.jpg_to_png"
        )
    )


# ============================================================
# PREVIEW PNG
# ============================================================

@jpg_to_png_bp.route(
    "/preview/<conversion_id>",
    methods=["GET"],
)
def preview(conversion_id):

    conversion = get_conversion(
        conversion_id
    )

    if conversion is None:

        flash(
            "This temporary conversion has expired or is no longer available.",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    return send_file(
        str(
            conversion["output_path"]
        ),
        mimetype="image/png",
        as_attachment=False,
    )


# ============================================================
# DOWNLOAD PNG
# ============================================================

@jpg_to_png_bp.route(
    "/download/<conversion_id>",
    methods=["GET"],
)
def download(conversion_id):

    conversion = get_conversion(
        conversion_id
    )

    if conversion is None:

        flash(
            "This converted PNG has expired or is no longer available.",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    return send_file(
        str(
            conversion["output_path"]
        ),
        mimetype="image/png",
        as_attachment=True,
        download_name=(
            conversion["metadata"]
            .get(
                "output_filename",
                "converted.png"
            )
        ),
    )


# ============================================================
# DELETE CONVERSION
# ============================================================

@jpg_to_png_bp.route(
    "/delete/<conversion_id>",
    methods=["POST"],
)
def delete(conversion_id):

    conversion = get_conversion(
        conversion_id
    )

    if conversion is None:

        flash(
            "Conversion not found or already expired.",
            "error",
        )

        return redirect(
            url_for(
                "jpg_to_png.jpg_to_png"
            )
        )

    delete_conversion_folder(
        conversion["folder"]
    )

    flash(
        "Temporary conversion deleted successfully.",
        "success",
    )

    return redirect(
        url_for(
            "jpg_to_png.jpg_to_png"
        )
    )


# ============================================================
# CLEAR TEMPORARY HISTORY
# ============================================================

@jpg_to_png_bp.route(
    "/clear-history",
    methods=["POST", "GET"],
)
def clear_history():

    user_folder = (
        get_current_user_folder()
    )

    if user_folder.exists():

        for conversion_folder in user_folder.iterdir():

            if conversion_folder.is_dir():

                delete_conversion_folder(
                    conversion_folder
                )

        try:

            if (
                user_folder.exists()
                and not any(
                    user_folder.iterdir()
                )
            ):

                user_folder.rmdir()

        except Exception:
            pass

    if request.method == "POST":

        return (
            "",
            204,
        )

    return redirect(
        url_for(
            "dashboard"
        )
    )