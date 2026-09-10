import uuid
from pathlib import Path

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    send_file,
    flash,
    session
)

from werkzeug.utils import secure_filename

from services.png_to_jpg_service import convert_png_to_jpg


# ============================================================
# BLUEPRINT
# ============================================================

png_to_jpg_bp = Blueprint(
    "png_to_jpg",
    __name__,
    url_prefix="/png-to-jpg"
)


# ============================================================
# FOLDERS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

UPLOAD_FOLDER = (
    BASE_DIR
    / "uploads"
    / "png_to_jpg"
)

OUTPUT_FOLDER = (
    BASE_DIR
    / "converted"
    / "png_to_jpg"
)

UPLOAD_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# ALLOWED EXTENSIONS
# ============================================================

ALLOWED_EXTENSIONS = {
    "png"
}


def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# ============================================================
# PAGE
# ============================================================

@png_to_jpg_bp.route("/", methods=["GET"])
def png_to_jpg():

    # Login is NOT required.
    # This converter works without login.

    conversions = session.get(
        "png_to_jpg_conversions",
        []
    )

    return render_template(
        "png_to_jpg.html",
        conversions=conversions
    )


# ============================================================
# CONVERT PNG TO JPG
# ============================================================

@png_to_jpg_bp.route(
    "/convert",
    methods=["POST"]
)
def convert():

    uploaded_files = request.files.getlist("files")

    if not uploaded_files:
        flash(
            "Please choose PNG images.",
            "error"
        )

        return redirect(
            url_for("png_to_jpg.png_to_jpg")
        )

    valid_files = [
        file
        for file in uploaded_files
        if file and file.filename
    ]

    if not valid_files:
        flash(
            "Please choose at least one PNG image.",
            "error"
        )

        return redirect(
            url_for("png_to_jpg.png_to_jpg")
        )

    conversions = session.get(
        "png_to_jpg_conversions",
        []
    )

    successful_count = 0

    for uploaded_file in valid_files:

        # ----------------------------------------------------
        # FILE TYPE
        # ----------------------------------------------------

        if not allowed_file(
            uploaded_file.filename
        ):

            flash(
                f"Only PNG files are allowed: "
                f"{uploaded_file.filename}",
                "error"
            )

            continue

        # ----------------------------------------------------
        # ORIGINAL NAME
        # ----------------------------------------------------

        original_filename = secure_filename(
            uploaded_file.filename
        )

        if not original_filename:
            continue

        # ----------------------------------------------------
        # UNIQUE ID
        # ----------------------------------------------------

        conversion_id = uuid.uuid4().hex

        # ----------------------------------------------------
        # STORED PNG
        # ----------------------------------------------------

        stored_png_name = (
            f"{conversion_id}_{original_filename}"
        )

        png_path = (
            UPLOAD_FOLDER
            / stored_png_name
        )

        # ----------------------------------------------------
        # JPG NAME
        # ----------------------------------------------------

        png_stem = Path(
            original_filename
        ).stem

        jpg_filename = (
            f"{png_stem}.jpg"
        )

        stored_jpg_name = (
            f"{conversion_id}_{jpg_filename}"
        )

        jpg_path = (
            OUTPUT_FOLDER
            / stored_jpg_name
        )

        # ----------------------------------------------------
        # SAVE PNG
        # ----------------------------------------------------

        try:

            uploaded_file.save(
                str(png_path)
            )

        except Exception as e:

            print(
                "PNG save error:",
                e
            )

            continue

        # ----------------------------------------------------
        # CONVERT
        # ----------------------------------------------------

        try:

            convert_png_to_jpg(
                png_path,
                jpg_path
            )

        except Exception as e:

            print(
                "PNG to JPG conversion error:",
                e
            )

            try:
                if png_path.exists():
                    png_path.unlink()
            except Exception:
                pass

            try:
                if jpg_path.exists():
                    jpg_path.unlink()
            except Exception:
                pass

            continue

        # ----------------------------------------------------
        # TEMPORARY HISTORY
        # ----------------------------------------------------

        conversions.append({
            "id": conversion_id,
            "original_filename": original_filename,
            "output_filename": jpg_filename,
            "input_path": str(png_path),
            "output_path": str(jpg_path)
        })

        successful_count += 1

    # --------------------------------------------------------
    # SAVE SESSION HISTORY
    # --------------------------------------------------------

    session["png_to_jpg_conversions"] = conversions

    session.modified = True

    if successful_count == 0:

        flash(
            "No PNG images could be converted.",
            "error"
        )

    else:

        flash(
            f"{successful_count} image"
            f"{'s' if successful_count != 1 else ''}"
            " converted to JPG successfully.",
            "success"
        )

    return redirect(
        url_for("png_to_jpg.png_to_jpg")
    )


# ============================================================
# PREVIEW JPG
# ============================================================

@png_to_jpg_bp.route(
    "/preview/<conversion_id>",
    methods=["GET"]
)
def preview(conversion_id):

    conversions = session.get(
        "png_to_jpg_conversions",
        []
    )

    conversion = next(
        (
            item
            for item in conversions
            if item["id"] == conversion_id
        ),
        None
    )

    if not conversion:

        flash(
            "Converted image was not found.",
            "error"
        )

        return redirect(
            url_for("png_to_jpg.png_to_jpg")
        )

    file_path = Path(
        conversion["output_path"]
    )

    if not file_path.exists():

        flash(
            "Converted image no longer exists.",
            "error"
        )

        return redirect(
            url_for("png_to_jpg.png_to_jpg")
        )

    return send_file(
        str(file_path),
        mimetype="image/jpeg"
    )


# ============================================================
# DOWNLOAD JPG
# ============================================================

@png_to_jpg_bp.route(
    "/download/<conversion_id>",
    methods=["GET"]
)
def download(conversion_id):

    conversions = session.get(
        "png_to_jpg_conversions",
        []
    )

    conversion = next(
        (
            item
            for item in conversions
            if item["id"] == conversion_id
        ),
        None
    )

    if not conversion:

        flash(
            "Converted image was not found.",
            "error"
        )

        return redirect(
            url_for("png_to_jpg.png_to_jpg")
        )

    file_path = Path(
        conversion["output_path"]
    )

    if not file_path.exists():

        flash(
            "Converted JPG file no longer exists.",
            "error"
        )

        return redirect(
            url_for("png_to_jpg.png_to_jpg")
        )

    return send_file(
        str(file_path),
        as_attachment=True,
        download_name=conversion["output_filename"]
    )


# ============================================================
# DELETE CONVERSION
# ============================================================

@png_to_jpg_bp.route(
    "/delete/<conversion_id>",
    methods=["POST"]
)
def delete(conversion_id):

    conversions = session.get(
        "png_to_jpg_conversions",
        []
    )

    conversion = next(
        (
            item
            for item in conversions
            if item["id"] == conversion_id
        ),
        None
    )

    if not conversion:

        flash(
            "Conversion was not found.",
            "error"
        )

        return redirect(
            url_for("png_to_jpg.png_to_jpg")
        )

    # --------------------------------------------------------
    # DELETE PNG
    # --------------------------------------------------------

    try:

        input_path = Path(
            conversion["input_path"]
        )

        if input_path.exists():
            input_path.unlink()

    except Exception as e:

        print(
            "Input delete error:",
            e
        )

    # --------------------------------------------------------
    # DELETE JPG
    # --------------------------------------------------------

    try:

        output_path = Path(
            conversion["output_path"]
        )

        if output_path.exists():
            output_path.unlink()

    except Exception as e:

        print(
            "Output delete error:",
            e
        )

    # --------------------------------------------------------
    # REMOVE FROM SESSION
    # --------------------------------------------------------

    conversions = [
        item
        for item in conversions
        if item["id"] != conversion_id
    ]

    session["png_to_jpg_conversions"] = conversions

    session.modified = True

    flash(
        "Converted image deleted.",
        "success"
    )

    return redirect(
        url_for("png_to_jpg.png_to_jpg")
    )