"""
DocBuddy - Resize Image Blueprint

Save as:
    routes/resize.py

Register in app.py:
    from routes.resize import resize_bp
    app.register_blueprint(resize_bp)

The browser sends the selected dimensions and this server performs the
actual Pillow resize. The processed image is returned directly as a blob,
so the existing preview/download UI works without a second result page.
"""

from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename


resize_bp = Blueprint(
    "resize",
    __name__,
    url_prefix="/resize",
)

ALLOWED_INPUT_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp",
    "bmp",
    "gif",
}

OUTPUT_FORMATS = {
    "jpeg": ("JPEG", "jpg", "image/jpeg"),
    "jpg": ("JPEG", "jpg", "image/jpeg"),
    "png": ("PNG", "png", "image/png"),
    "webp": ("WEBP", "webp", "image/webp"),
    "bmp": ("BMP", "bmp", "image/bmp"),
}

MAX_OUTPUT_SIDE = 20000


def allowed_file(filename):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_INPUT_EXTENSIONS


def output_folder():
    configured = current_app.config.get("RESIZE_OUTPUT_FOLDER")

    if configured:
        folder = Path(configured)
    else:
        folder = Path(current_app.root_path) / "outputs" / "resized"

    folder.mkdir(parents=True, exist_ok=True)
    return folder


def parse_positive_float(value, field_name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid number.")

    if number <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")

    return number


def convert_dimensions(
    original_width,
    original_height,
    width,
    height,
    unit,
    dpi,
):
    """
    Convert the four UI units to actual output pixels.

    Percent:
        original pixels × percentage / 100

    Pixels:
        exact pixel value

    Centimeters:
        centimeters / 2.54 × DPI

    Inches:
        inches × DPI
    """
    width = parse_positive_float(width, "Width")
    height = parse_positive_float(height, "Height")

    if unit == "percent":
        target_width = round(original_width * width / 100)
        target_height = round(original_height * height / 100)

    elif unit == "px":
        target_width = round(width)
        target_height = round(height)

    elif unit == "cm":
        target_width = round((width / 2.54) * dpi)
        target_height = round((height / 2.54) * dpi)

    elif unit == "in":
        target_width = round(width * dpi)
        target_height = round(height * dpi)

    else:
        raise ValueError(
            "Invalid unit. Use percent, pixels, centimeters or inches."
        )

    target_width = max(1, target_width)
    target_height = max(1, target_height)

    if target_width > MAX_OUTPUT_SIDE or target_height > MAX_OUTPUT_SIDE:
        raise ValueError(
            f"Output dimensions cannot exceed "
            f"{MAX_OUTPUT_SIDE:,} × {MAX_OUTPUT_SIDE:,} pixels."
        )

    return target_width, target_height


def resize_image(image, width, height, mode):
    if mode == "contain":
        return ImageOps.contain(
            image,
            (width, height),
            method=Image.Resampling.LANCZOS,
        )

    if mode == "cover":
        return ImageOps.fit(
            image,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    # Exact dimensions.
    return image.resize(
        (width, height),
        Image.Resampling.LANCZOS,
    )


def flatten_background(image, background, output_format):
    """
    PNG/WEBP can keep transparency.
    JPEG/BMP are flattened using the selected background.
    """
    output_format = output_format.upper()

    if (
        background == "transparent"
        and output_format in {"PNG", "WEBP"}
    ):
        return image.convert("RGBA")

    if background == "black":
        background_color = (0, 0, 0, 255)
    else:
        background_color = (255, 255, 255, 255)

    if "A" in image.getbands():
        canvas = Image.new(
            "RGBA",
            image.size,
            background_color,
        )
        canvas.alpha_composite(image.convert("RGBA"))
        image = canvas

    if output_format in {"JPEG", "BMP"}:
        return image.convert("RGB")

    if output_format == "PNG":
        return image.convert("RGBA" if "A" in image.getbands() else "RGB")

    if output_format == "WEBP":
        return image.convert("RGBA" if "A" in image.getbands() else "RGB")

    return image.convert("RGB")


def save_image(image, path, output_format, quality, dpi):
    if output_format == "JPEG":
        image.save(
            path,
            format="JPEG",
            quality=quality,
            optimize=True,
            dpi=(dpi, dpi),
        )
        return

    if output_format == "PNG":
        image.save(
            path,
            format="PNG",
            optimize=True,
            compress_level=6,
            dpi=(dpi, dpi),
        )
        return

    if output_format == "WEBP":
        image.save(
            path,
            format="WEBP",
            quality=quality,
            method=6,
            dpi=(dpi, dpi),
        )
        return

    if output_format == "BMP":
        image.save(
            path,
            format="BMP",
            dpi=(dpi, dpi),
        )
        return

    raise ValueError("Unsupported output format.")


@resize_bp.route("/", methods=["GET"])
def resize_page():
    """Resize page."""
    return render_template("resize.html")


@resize_bp.route("/process", methods=["POST"])
def process_resize():
    """Resize one uploaded image and return the generated image blob."""

    uploaded = request.files.get("image")

    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Please select an image first."}), 400

    if not allowed_file(uploaded.filename):
        return jsonify({
            "error": "Unsupported image type. Use JPG, PNG, WEBP, BMP or GIF."
        }), 400

    try:
        width = request.form.get("width", "70")
        height = request.form.get("height", "70")
        unit = request.form.get("unit", "percent").strip().lower()

        dpi = int(request.form.get("dpi", "72"))
        quality = int(request.form.get("quality", "90"))

        output_key = request.form.get("format", "jpeg").strip().lower()
        fit_mode = request.form.get("fit", "stretch").strip().lower()
        background = request.form.get(
            "background",
            "transparent",
        ).strip().lower()

        if dpi < 1 or dpi > 2400:
            raise ValueError("DPI must be between 1 and 2400.")

        if quality < 1 or quality > 100:
            raise ValueError("Quality must be between 1 and 100.")

        if unit == "pixels":
            unit = "px"
        elif unit == "centimeters":
            unit = "cm"
        elif unit == "inches":
            unit = "in"

        if unit not in {"percent", "px", "cm", "in"}:
            raise ValueError(
                "Invalid unit. Use Percent, Pixels, Centimeters or Inches."
            )

        if output_key not in OUTPUT_FORMATS:
            raise ValueError("Invalid output format.")

        if fit_mode not in {"stretch", "contain", "cover"}:
            raise ValueError("Invalid resize mode.")

        if background not in {"white", "black", "transparent"}:
            raise ValueError("Invalid background option.")

        output_format, extension, mimetype = OUTPUT_FORMATS[output_key]

        with Image.open(uploaded) as opened:
            image = ImageOps.exif_transpose(opened)

            # Animated GIFs are processed as their first frame.
            if getattr(image, "is_animated", False):
                image.seek(0)

            image = image.copy()

        original_width, original_height = image.size

        target_width, target_height = convert_dimensions(
            original_width,
            original_height,
            width,
            height,
            unit,
            dpi,
        )

        processed = resize_image(
            image,
            target_width,
            target_height,
            fit_mode,
        )

        processed = flatten_background(
            processed,
            background,
            output_format,
        )

        folder = output_folder()

        unique_name = (
            f"resized_{uuid4().hex}.{extension}"
        )
        output_path = folder / unique_name

        save_image(
            processed,
            output_path,
            output_format,
            quality,
            dpi,
        )

        original_filename = (
            secure_filename(uploaded.filename) or "image"
        )

        download_name = (
            f"{Path(original_filename).stem}_resized.{extension}"
        )

        response = send_file(
            output_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=download_name,
            max_age=0,
        )

        response.headers["X-New-Width"] = str(target_width)
        response.headers["X-New-Height"] = str(target_height)
        response.headers["X-New-Size"] = str(output_path.stat().st_size)
        response.headers["X-Cropped"] = (
            "1" if fit_mode == "cover" else "0"
        )
        response.headers["Cache-Control"] = "no-store, max-age=0"

        return response

    except Exception as exc:
        current_app.logger.exception(
            "Resize image failed: %s",
            exc,
        )
        return jsonify({
            "error": str(exc) or "Could not resize the image."
        }), 400


@resize_bp.route("/download/<path:filename>", methods=["GET"])
def download(filename):
    """
    Optional direct download route for files generated by this blueprint.
    The current browser UI downloads directly from its object URL.
    """
    safe_filename = secure_filename(filename)

    if not safe_filename or safe_filename != filename:
        return jsonify({"error": "Invalid output file."}), 400

    folder = output_folder()
    file_path = folder / safe_filename

    if not file_path.is_file():
        return jsonify({
            "error": "The resized image is no longer available."
        }), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=safe_filename,
        max_age=0,
    )
