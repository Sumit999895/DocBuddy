import os
import time
import uuid
import threading
from pathlib import Path

from PIL import Image, ImageOps

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
    abort,
)

from werkzeug.utils import secure_filename


# ============================================================
# BLUEPRINT
# ============================================================

jpg_to_pdf_bp = Blueprint(
    "jpg_to_pdf",
    __name__,
    url_prefix="/jpg-to-pdf"
)


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

TEMP_ROOT = (
    BASE_DIR
    / "temp"
    / "jpg_to_pdf"
)

TEMP_ROOT.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# SETTINGS
# ============================================================

# How long a converted PDF remains on the server.
# 15 minutes = 900 seconds.
TEMP_FILE_LIFETIME = 15 * 60

# Maximum number of images in one conversion.
MAX_IMAGES = 50

# Maximum size of each image: 25 MB.
MAX_IMAGE_SIZE = 25 * 1024 * 1024

# Maximum total upload size: 100 MB.
MAX_TOTAL_SIZE = 100 * 1024 * 1024


# ============================================================
# ALLOWED IMAGE TYPES
# ============================================================

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
}


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def allowed_image(filename):
    """
    Check whether the uploaded filename has an allowed extension.
    """

    if not filename:
        return False

    extension = Path(filename).suffix.lower()

    return extension in ALLOWED_EXTENSIONS


def safe_token(token):
    """
    Validate a temporary conversion token.

    Tokens are UUID hex strings, so only 32 hexadecimal
    characters are accepted.
    """

    if not token:
        abort(404)

    if len(token) != 32:
        abort(404)

    if any(
        character not in "0123456789abcdef"
        for character in token.lower()
    ):
        abort(404)

    return token.lower()


def conversion_folder(token):
    """
    Return the temporary folder belonging to a conversion.
    """

    token = safe_token(token)

    folder = TEMP_ROOT / token

    # Security check.
    try:
        folder.resolve().relative_to(
            TEMP_ROOT.resolve()
        )
    except ValueError:
        abort(404)

    return folder


def delete_conversion(token):
    """
    Delete an entire temporary conversion folder.
    """

    try:
        folder = conversion_folder(token)

        if not folder.exists():
            return

        for item in folder.iterdir():

            try:

                if item.is_file() or item.is_symlink():
                    item.unlink()

                elif item.is_dir():
                    import shutil

                    shutil.rmtree(
                        item,
                        ignore_errors=True
                    )

            except Exception:
                pass

        try:
            folder.rmdir()
        except Exception:
            pass

    except Exception:
        pass


def schedule_auto_delete(token):
    """
    Schedule automatic deletion after TEMP_FILE_LIFETIME.

    This means the PDF does not remain permanently on the server.
    """

    def remove_later():
        delete_conversion(token)

    timer = threading.Timer(
        TEMP_FILE_LIFETIME,
        remove_later
    )

    timer.daemon = True

    timer.start()


def cleanup_old_conversions():
    """
    Remove old temporary conversions.

    This is also useful if the Flask application was restarted
    before a scheduled timer could run.
    """

    try:

        if not TEMP_ROOT.exists():
            return

        current_time = time.time()

        for folder in TEMP_ROOT.iterdir():

            if not folder.is_dir():
                continue

            try:

                modified_time = folder.stat().st_mtime

                age = current_time - modified_time

                if age > TEMP_FILE_LIFETIME:

                    for item in folder.iterdir():

                        try:

                            if item.is_file():
                                item.unlink()

                            elif item.is_dir():

                                import shutil

                                shutil.rmtree(
                                    item,
                                    ignore_errors=True
                                )

                        except Exception:
                            pass

                    try:
                        folder.rmdir()
                    except Exception:
                        pass

            except Exception:
                pass

    except Exception:
        pass


# ============================================================
# IMAGE VALIDATION
# ============================================================

def validate_image(image_path):
    """
    Open and verify that the file is actually a valid image.

    This prevents someone from simply renaming another file
    to .jpg or .png.
    """

    try:

        with Image.open(image_path) as image:

            image.verify()

        return True

    except Exception:

        return False


# ============================================================
# CORE JPG TO PDF CONVERSION
# ============================================================

def convert_jpg_to_pdf(image_paths, output_path):
    """
    Convert one or more JPG/JPEG/PNG images into one PDF.

    No database is used.

    Parameters
    ----------
    image_paths:
        List of image paths.

    output_path:
        Path where the temporary PDF should be created.

    Returns
    -------
    Path
        Created PDF path.
    """

    if not image_paths:
        raise ValueError(
            "No images were provided."
        )

    images = []

    try:

        # ----------------------------------------------------
        # OPEN IMAGES
        # ----------------------------------------------------

        for image_path in image_paths:

            image_path = Path(
                image_path
            )

            if not image_path.exists():

                raise FileNotFoundError(
                    f"Image not found: {image_path}"
                )

            # Make sure the image is actually valid.
            if not validate_image(image_path):

                raise ValueError(
                    f"Invalid or corrupted image: "
                    f"{image_path.name}"
                )

            image = Image.open(
                image_path
            )

            # ------------------------------------------------
            # APPLY EXIF ORIENTATION
            # ------------------------------------------------

            image = ImageOps.exif_transpose(
                image
            )

            # ------------------------------------------------
            # CONVERT TO RGB
            # ------------------------------------------------

            if image.mode != "RGB":

                image = image.convert(
                    "RGB"
                )

            # Copy the image so the original file can be
            # closed safely.
            image_copy = image.copy()

            image.close()

            images.append(
                image_copy
            )

        # ----------------------------------------------------
        # OUTPUT PATH
        # ----------------------------------------------------

        output_path = Path(
            output_path
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # ----------------------------------------------------
        # SAVE PDF
        # ----------------------------------------------------

        first_image = images[0]

        remaining_images = images[1:]

        first_image.save(
            output_path,
            format="PDF",
            resolution=150.0,
            save_all=True,
            append_images=remaining_images
        )

        # Make sure PDF was actually created.
        if not output_path.exists():

            raise RuntimeError(
                "PDF was not created."
            )

        if output_path.stat().st_size == 0:

            raise RuntimeError(
                "Created PDF is empty."
            )

        return output_path

    finally:

        for image in images:

            try:
                image.close()

            except Exception:
                pass


# ============================================================
# PAGE
# ============================================================

@jpg_to_pdf_bp.route(
    "/",
    methods=["GET"]
)
def jpg_to_pdf():

    # Authentication remains.
    # The conversion itself is NOT stored in the database.

    if "user" not in session:

        return redirect(
            url_for("login")
        )

    # Clean old temporary files whenever this page is opened.
    cleanup_old_conversions()

    return render_template(
        "jpg_to_pdf.html",
        result=None
    )


# ============================================================
# CONVERT
# ============================================================

@jpg_to_pdf_bp.route(
    "/convert",
    methods=["POST"]
)
def convert_jpg_to_pdf_route():

    if "user" not in session:

        return redirect(
            url_for("login")
        )

    # Clean old temporary conversions first.
    cleanup_old_conversions()

    files = request.files.getlist(
        "files"
    )

    # --------------------------------------------------------
    # BASIC VALIDATION
    # --------------------------------------------------------

    if not files:

        flash(
            "Please select at least one image.",
            "error"
        )

        return redirect(
            url_for(
                "jpg_to_pdf.jpg_to_pdf"
            )
        )

    # Remove empty file entries.
    valid_files = [
        file
        for file in files
        if file and file.filename
    ]

    if not valid_files:

        flash(
            "Please select at least one valid image.",
            "error"
        )

        return redirect(
            url_for(
                "jpg_to_pdf.jpg_to_pdf"
            )
        )

    # --------------------------------------------------------
    # MAX IMAGE COUNT
    # --------------------------------------------------------

    if len(valid_files) > MAX_IMAGES:

        flash(
            f"You can convert up to "
            f"{MAX_IMAGES} images at once.",
            "error"
        )

        return redirect(
            url_for(
                "jpg_to_pdf.jpg_to_pdf"
            )
        )

    # --------------------------------------------------------
    # VALIDATE EXTENSIONS
    # --------------------------------------------------------

    for file in valid_files:

        if not allowed_image(
            file.filename
        ):

            flash(
                "Only JPG, JPEG and PNG images are allowed.",
                "error"
            )

            return redirect(
                url_for(
                    "jpg_to_pdf.jpg_to_pdf"
                )
            )

    # --------------------------------------------------------
    # UNIQUE TEMPORARY CONVERSION
    # --------------------------------------------------------

    conversion_id = uuid.uuid4().hex

    folder = (
        TEMP_ROOT
        / conversion_id
    )

    folder.mkdir(
        parents=True,
        exist_ok=False
    )

    image_paths = []

    pdf_path = None

    try:

        # ----------------------------------------------------
        # SAVE UPLOADED IMAGES TEMPORARILY
        # ----------------------------------------------------

        total_size = 0

        for index, file in enumerate(
            valid_files,
            start=1
        ):

            original_name = secure_filename(
                file.filename
            )

            if not original_name:

                raise ValueError(
                    "Invalid image filename."
                )

            extension = (
                Path(original_name)
                .suffix
                .lower()
            )

            image_name = (
                f"image_{index}{extension}"
            )

            image_path = (
                folder
                / image_name
            )

            # Save only temporarily.
            file.save(
                str(image_path)
            )

            if not image_path.exists():

                raise RuntimeError(
                    "Could not save uploaded image."
                )

            image_size = (
                image_path.stat().st_size
            )

            if image_size > MAX_IMAGE_SIZE:

                raise ValueError(
                    f"{original_name} is larger "
                    f"than 25 MB."
                )

            total_size += image_size

            if total_size > MAX_TOTAL_SIZE:

                raise ValueError(
                    "Total upload size cannot "
                    "exceed 100 MB."
                )

            image_paths.append(
                image_path
            )

        # ----------------------------------------------------
        # CREATE TEMPORARY PDF
        # ----------------------------------------------------

        pdf_filename = (
            "converted.pdf"
        )

        pdf_path = (
            folder
            / pdf_filename
        )

        convert_jpg_to_pdf(
            image_paths,
            pdf_path
        )

        # ----------------------------------------------------
        # INFORMATION FOR WEB PAGE
        # ----------------------------------------------------

        if len(valid_files) == 1:

            display_name = secure_filename(
                valid_files[0].filename
            )

        else:

            display_name = (
                f"{len(valid_files)} images"
            )

        pdf_size = (
            pdf_path.stat().st_size
        )

        # ----------------------------------------------------
        # SCHEDULE AUTOMATIC DELETION
        # ----------------------------------------------------

        schedule_auto_delete(
            conversion_id
        )

        # ----------------------------------------------------
        # REDIRECT TO TEMPORARY RESULT PAGE
        # ----------------------------------------------------

        return redirect(
            url_for(
                "jpg_to_pdf.result_jpg_to_pdf",
                token=conversion_id,
                name=display_name
            )
        )

    except Exception as e:

        print(
            "JPG to PDF conversion error:",
            repr(e)
        )

        # Delete everything immediately if
        # conversion fails.
        delete_conversion(
            conversion_id
        )

        flash(
            str(e)
            if isinstance(e, ValueError)
            else "Could not convert the images into a PDF.",
            "error"
        )

        return redirect(
            url_for(
                "jpg_to_pdf.jpg_to_pdf"
            )
        )


# ============================================================
# TEMPORARY RESULT PAGE
# ============================================================

@jpg_to_pdf_bp.route(
    "/result/<token>"
)
def result_jpg_to_pdf(token):

    if "user" not in session:

        return redirect(
            url_for("login")
        )

    cleanup_old_conversions()

    token = safe_token(
        token
    )

    folder = conversion_folder(
        token
    )

    pdf_path = (
        folder
        / "converted.pdf"
    )

    # --------------------------------------------------------
    # CHECK FILE
    # --------------------------------------------------------

    if not pdf_path.exists():

        flash(
            "This temporary PDF has expired.",
            "error"
        )

        return redirect(
            url_for(
                "jpg_to_pdf.jpg_to_pdf"
            )
        )

    # --------------------------------------------------------
    # EXPIRATION INFORMATION
    # --------------------------------------------------------

    created_time = (
        folder.stat().st_mtime
    )

    expires_at = (
        created_time
        + TEMP_FILE_LIFETIME
    )

    remaining_seconds = max(
        0,
        int(
            expires_at
            - time.time()
        )
    )

    display_name = request.args.get(
        "name",
        "converted.pdf"
    )

    display_name = secure_filename(
        display_name
    ) or "converted.pdf"

    # --------------------------------------------------------
    # SHOW RESULT ON WEBSITE
    # --------------------------------------------------------

    return render_template(
        "jpg_to_pdf.html",
        result={
            "token": token,
            "filename": display_name,
            "pdf_size": pdf_path.stat().st_size,
            "remaining_seconds": remaining_seconds,
            "preview_url": url_for(
                "jpg_to_pdf.preview_jpg_to_pdf",
                token=token
            ),
            "download_url": url_for(
                "jpg_to_pdf.download_jpg_to_pdf",
                token=token
            ),
        }
    )


# ============================================================
# PREVIEW TEMPORARY PDF
# ============================================================

@jpg_to_pdf_bp.route(
    "/preview/<token>"
)
def preview_jpg_to_pdf(token):

    if "user" not in session:

        return redirect(
            url_for("login")
        )

    token = safe_token(
        token
    )

    folder = conversion_folder(
        token
    )

    pdf_path = (
        folder
        / "converted.pdf"
    )

    if not pdf_path.exists():

        return (
            "This temporary PDF has expired.",
            404
        )

    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=False
    )


# ============================================================
# DOWNLOAD TEMPORARY PDF
# ============================================================

@jpg_to_pdf_bp.route(
    "/download/<token>"
)
def download_jpg_to_pdf(token):

    if "user" not in session:

        return redirect(
            url_for("login")
        )

    token = safe_token(
        token
    )

    folder = conversion_folder(
        token
    )

    pdf_path = (
        folder
        / "converted.pdf"
    )

    if not pdf_path.exists():

        return (
            "This temporary PDF has expired.",
            404
        )

    filename = request.args.get(
        "name",
        "converted.pdf"
    )

    filename = secure_filename(
        filename
    )

    if not filename:

        filename = "converted.pdf"

    if not filename.lower().endswith(
        ".pdf"
    ):

        filename += ".pdf"

    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )


# ============================================================
# DELETE TEMPORARY PDF NOW
# ============================================================

@jpg_to_pdf_bp.route(
    "/delete/<token>",
    methods=["POST"]
)
def delete_jpg_to_pdf(token):

    if "user" not in session:

        return redirect(
            url_for("login")
        )

    token = safe_token(
        token
    )

    delete_conversion(
        token
    )

    flash(
        "Temporary PDF deleted.",
        "success"
    )

    return redirect(
        url_for(
            "jpg_to_pdf.jpg_to_pdf"
        )
    )