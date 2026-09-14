# ============================================================
# DOCBUDDY - CROP IMAGE MODULE
# ============================================================

import os
import uuid
import time

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    send_file,
    session,
    redirect,
    url_for
)

from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageChops


# ============================================================
# BLUEPRINT
# ============================================================

crop_image_bp = Blueprint(
    "crop_image",
    __name__,
    url_prefix="/crop-image"
)


# ============================================================
# TEMPORARY DIRECTORY
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

TEMP_DIR = os.path.join(
    BASE_DIR,
    "temp",
    "crop_images"
)

os.makedirs(
    TEMP_DIR,
    exist_ok=True
)


# ============================================================
# SETTINGS
# ============================================================

ALLOWED_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp",
    "bmp",
    "gif"
}

MAX_FILE_SIZE = 25 * 1024 * 1024


# ============================================================
# LOGIN CHECK
# ============================================================

def logged_in():

    return "user" in session


# ============================================================
# FILE EXTENSION
# ============================================================

def allowed_file(filename):

    if not filename:
        return False

    if "." not in filename:
        return False

    extension = filename.rsplit(
        ".",
        1
    )[1].lower()

    return extension in ALLOWED_EXTENSIONS


# ============================================================
# FIND TEMP FILE
# ============================================================

def find_file(file_id):

    for extension in ALLOWED_EXTENSIONS:

        path = os.path.join(
            TEMP_DIR,
            f"{file_id}.{extension}"
        )

        if os.path.isfile(path):
            return path

    return None


# ============================================================
# CLEAN OLD TEMP FILES
# ============================================================

def cleanup_old_files():

    now = time.time()

    try:

        for filename in os.listdir(
            TEMP_DIR
        ):

            path = os.path.join(
                TEMP_DIR,
                filename
            )

            if not os.path.isfile(path):
                continue

            try:

                if (
                    now -
                    os.path.getmtime(path)
                    > 3600
                ):

                    os.remove(path)

            except OSError:
                pass

    except OSError:
        pass


# ============================================================
# SMART CONTENT DETECTION (server-side auto-crop)
#
# Much more reliable than sampling a downscaled canvas on the
# client: this runs against the full-resolution image, samples
# the background color from all four corners (averaged, so a
# single noisy corner pixel doesn't throw it off), then finds
# the bounding box of everything that differs from that
# background by more than a noise threshold.
# ============================================================

def detect_content_bbox(image, threshold=28):

    rgb_image = image.convert("RGB")

    width, height = rgb_image.size

    corner_pixels = [
        rgb_image.getpixel((0, 0)),
        rgb_image.getpixel((width - 1, 0)),
        rgb_image.getpixel((0, height - 1)),
        rgb_image.getpixel((width - 1, height - 1)),
    ]

    background_color = tuple(
        sum(channel) // len(corner_pixels)
        for channel in zip(*corner_pixels)
    )

    background_image = Image.new(
        "RGB",
        rgb_image.size,
        background_color
    )

    difference = ImageChops.difference(
        rgb_image,
        background_image
    )

    # Collapse to grayscale, then binarize so small noise /
    # compression artifacts don't create a bounding box that
    # spans the entire image.
    grayscale_diff = difference.convert("L")

    grayscale_diff = grayscale_diff.point(
        lambda pixel: 255 if pixel > threshold else 0
    )

    # A light blur before thresholding again mops up isolated
    # single-pixel noise specks that would otherwise widen the
    # detected box unnecessarily.
    grayscale_diff = grayscale_diff.filter(
        ImageFilter.MedianFilter(size=3)
    )

    bbox = grayscale_diff.getbbox()

    return bbox


# ============================================================
# SEPIA FILTER
# ============================================================

def apply_sepia(image):

    grayscale = ImageOps.grayscale(
        image.convert("RGB")
    )

    sepia = ImageOps.colorize(
        grayscale,
        black="#3a2a1d",
        white="#f4e7d3",
        mid="#a9835b"
    )

    return sepia.convert("RGB")


# ============================================================
# NORMALIZE HEX COLOR (defends the border-fill against bad input)
# ============================================================

def normalize_hex_color(value, fallback="#ffffff"):

    if not value or not isinstance(value, str):
        return fallback

    value = value.strip()

    if not value.startswith("#"):
        value = f"#{value}"

    if len(value) not in (4, 7):
        return fallback

    try:
        int(value[1:], 16)
    except ValueError:
        return fallback

    return value


# ============================================================
# HOME PAGE
# ============================================================

@crop_image_bp.route("/")
def crop_image():

    if not logged_in():

        return redirect(
            url_for("auth.login")
        )

    cleanup_old_files()

    return render_template(
        "crop_image.html",
        user=session.get("user")
    )


# ============================================================
# UPLOAD
# ============================================================

@crop_image_bp.route(
    "/upload",
    methods=["POST"]
)
def upload_image():

    if not logged_in():

        return jsonify({
            "success": False,
            "error": "Please login first."
        }), 401

    if "image" not in request.files:

        return jsonify({
            "success": False,
            "error": "No image was selected."
        }), 400

    uploaded = request.files["image"]

    if not uploaded.filename:

        return jsonify({
            "success": False,
            "error": "Please select an image."
        }), 400

    if not allowed_file(
        uploaded.filename
    ):

        return jsonify({
            "success": False,
            "error": (
                "Unsupported image format. "
                "Use JPG, PNG, WEBP, BMP or GIF."
            )
        }), 400

    try:

        uploaded.seek(0)

        image = Image.open(
            uploaded
        )

        image = ImageOps.exif_transpose(
            image
        )

        image.load()

        if image.width <= 0 or image.height <= 0:

            raise ValueError(
                "Invalid image dimensions."
            )

        file_id = uuid.uuid4().hex

        extension = (
            uploaded.filename
            .rsplit(".", 1)[1]
            .lower()
        )

        if extension == "jpeg":
            extension = "jpg"

        path = os.path.join(
            TEMP_DIR,
            f"{file_id}.{extension}"
        )

        image.save(path)

        return jsonify({

            "success": True,

            "file_id": file_id,

            "filename": uploaded.filename,

            "width": image.width,

            "height": image.height,

            "format": (
                image.format
                or extension.upper()
            ),

            "preview_url": url_for(
                "crop_image.preview",
                file_id=file_id
            )

        })

    except Exception as error:

        return jsonify({

            "success": False,

            "error": (
                "Could not read the image: "
                f"{error}"
            )

        }), 500


# ============================================================
# IMAGE PREVIEW
# ============================================================

@crop_image_bp.route(
    "/preview/<file_id>"
)
def preview(file_id):

    if not logged_in():
        return "", 401

    path = find_file(
        file_id
    )

    if not path:
        return "", 404

    return send_file(path)


# ============================================================
# AUTO-DETECT CONTENT BOUNDS
#
# Runs the server-side smart-crop detector against the
# original, full-resolution image and returns a bounding box
# in natural image coordinates. The frontend converts that to
# on-screen coordinates using its existing display scale.
# ============================================================

@crop_image_bp.route(
    "/auto-detect",
    methods=["POST"]
)
def auto_detect():

    if not logged_in():

        return jsonify({
            "success": False,
            "error": "Please login first."
        }), 401

    data = request.get_json(
        silent=True
    )

    if not data:

        return jsonify({
            "success": False,
            "error": "Invalid request."
        }), 400

    file_id = data.get(
        "file_id"
    )

    if not file_id:

        return jsonify({
            "success": False,
            "error": "Image ID is missing."
        }), 400

    source_path = find_file(
        file_id
    )

    if not source_path:

        return jsonify({
            "success": False,
            "error": "Source image was not found."
        }), 404

    try:

        image = Image.open(
            source_path
        )

        image = ImageOps.exif_transpose(
            image
        )

        image.load()

        bbox = detect_content_bbox(
            image
        )

        if not bbox:

            return jsonify({
                "success": True,
                "found": False
            })

        x0, y0, x1, y1 = bbox

        width = x1 - x0
        height = y1 - y0

        # If the detector effectively found "the whole image",
        # treat that as no meaningful crop to suggest.
        coverage = (width * height) / (image.width * image.height)

        if coverage > 0.98:

            return jsonify({
                "success": True,
                "found": False
            })

        return jsonify({

            "success": True,

            "found": True,

            "x": x0,

            "y": y0,

            "width": width,

            "height": height

        })

    except Exception as error:

        return jsonify({

            "success": False,

            "error": str(error)

        }), 500


# ============================================================
# PROCESS IMAGE
# ============================================================

@crop_image_bp.route(
    "/process",
    methods=["POST"]
)
def process():

    if not logged_in():

        return jsonify({
            "success": False,
            "error": "Please login first."
        }), 401

    data = request.get_json(
        silent=True
    )

    if not data:

        return jsonify({
            "success": False,
            "error": "Invalid request."
        }), 400

    file_id = data.get(
        "file_id"
    )

    if not file_id:

        return jsonify({
            "success": False,
            "error": "Image ID is missing."
        }), 400

    source_path = find_file(
        file_id
    )

    if not source_path:

        return jsonify({
            "success": False,
            "error": "Source image was not found."
        }), 404

    try:

        # ----------------------------------------------------
        # OPEN
        # ----------------------------------------------------

        image = Image.open(
            source_path
        )

        image = ImageOps.exif_transpose(
            image
        )

        image.load()

        # ----------------------------------------------------
        # ROTATION
        # ----------------------------------------------------

        rotation = int(
            data.get(
                "rotation",
                0
            )
        )

        rotation %= 360

        if rotation:

            image = image.rotate(
                -rotation,
                expand=True,
                resample=Image.Resampling.BICUBIC
            )

        # ----------------------------------------------------
        # FLIP
        # ----------------------------------------------------

        if data.get(
            "flip_horizontal",
            False
        ):

            image = ImageOps.mirror(
                image
            )

        if data.get(
            "flip_vertical",
            False
        ):

            image = ImageOps.flip(
                image
            )

        # ----------------------------------------------------
        # CROP
        # ----------------------------------------------------

        crop = data.get(
            "crop"
        )

        if crop:

            x = float(
                crop.get("x", 0)
            )

            y = float(
                crop.get("y", 0)
            )

            width = float(
                crop.get(
                    "width",
                    image.width
                )
            )

            height = float(
                crop.get(
                    "height",
                    image.height
                )
            )

            # Make sure values are valid.

            x = max(
                0,
                min(
                    x,
                    image.width - 1
                )
            )

            y = max(
                0,
                min(
                    y,
                    image.height - 1
                )
            )

            width = max(
                1,
                min(
                    width,
                    image.width - x
                )
            )

            height = max(
                1,
                min(
                    height,
                    image.height - y
                )
            )

            image = image.crop(
                (
                    int(x),
                    int(y),
                    int(x + width),
                    int(y + height)
                )
            )

        # ----------------------------------------------------
        # RESIZE
        # ----------------------------------------------------

        requested_width = data.get(
            "width"
        )

        requested_height = data.get(
            "height"
        )

        keep_ratio = data.get(
            "keep_ratio",
            True
        )

        if requested_width:

            requested_width = int(
                requested_width
            )

        if requested_height:

            requested_height = int(
                requested_height
            )

        if requested_width and requested_height:

            if keep_ratio:

                ratio = min(
                    requested_width /
                    image.width,

                    requested_height /
                    image.height
                )

                new_width = max(
                    1,
                    int(
                        image.width *
                        ratio
                    )
                )

                new_height = max(
                    1,
                    int(
                        image.height *
                        ratio
                    )
                )

            else:

                new_width = requested_width

                new_height = requested_height

            image = image.resize(
                (
                    new_width,
                    new_height
                ),
                Image.Resampling.LANCZOS
            )

        elif requested_width:

            ratio = (
                requested_width /
                image.width
            )

            image = image.resize(
                (
                    requested_width,
                    max(
                        1,
                        int(
                            image.height *
                            ratio
                        )
                    )
                ),
                Image.Resampling.LANCZOS
            )

        elif requested_height:

            ratio = (
                requested_height /
                image.height
            )

            image = image.resize(
                (
                    max(
                        1,
                        int(
                            image.width *
                            ratio
                        )
                    ),
                    requested_height
                ),
                Image.Resampling.LANCZOS
            )

        # ----------------------------------------------------
        # SHARPNESS
        # ----------------------------------------------------

        sharpness = float(
            data.get(
                "sharpness",
                1
            )
        )

        sharpness = max(
            0.1,
            min(
                3,
                sharpness
            )
        )

        if sharpness != 1:

            image = ImageEnhance.Sharpness(
                image
            ).enhance(
                sharpness
            )

        # ----------------------------------------------------
        # COLOR ADJUSTMENTS
        # ----------------------------------------------------

        brightness = float(
            data.get("brightness", 1)
        )

        brightness = max(0.2, min(2.5, brightness))

        if brightness != 1:

            image = ImageEnhance.Brightness(
                image
            ).enhance(brightness)

        contrast = float(
            data.get("contrast", 1)
        )

        contrast = max(0.2, min(2.5, contrast))

        if contrast != 1:

            image = ImageEnhance.Contrast(
                image
            ).enhance(contrast)

        saturation = float(
            data.get("saturation", 1)
        )

        saturation = max(0, min(2.5, saturation))

        if saturation != 1:

            image = ImageEnhance.Color(
                image
            ).enhance(saturation)

        blur_amount = float(
            data.get("blur", 0)
        )

        blur_amount = max(0, min(10, blur_amount))

        if blur_amount > 0:

            image = image.filter(
                ImageFilter.GaussianBlur(radius=blur_amount)
            )

        # ----------------------------------------------------
        # CREATIVE FILTER PRESETS
        # ----------------------------------------------------

        filter_name = str(
            data.get("filter", "none")
        ).lower()

        if filter_name == "grayscale":

            image = ImageOps.grayscale(
                image.convert("RGB")
            ).convert("RGB")

        elif filter_name == "sepia":

            image = apply_sepia(image)

        elif filter_name == "invert":

            image = ImageOps.invert(
                image.convert("RGB")
            )

        elif filter_name == "vivid":

            image = ImageEnhance.Color(
                image.convert("RGB")
            ).enhance(1.4)

            image = ImageEnhance.Contrast(
                image
            ).enhance(1.15)

        elif filter_name == "soft":

            image = image.filter(
                ImageFilter.GaussianBlur(radius=0.6)
            )

            image = ImageEnhance.Brightness(
                image
            ).enhance(1.05)

        # ----------------------------------------------------
        # BORDER / FRAME
        # ----------------------------------------------------

        border_width = int(
            data.get("border_width", 0)
        )

        border_width = max(0, min(60, border_width))

        if border_width > 0:

            border_color = normalize_hex_color(
                data.get("border_color", "#ffffff")
            )

            image = ImageOps.expand(
                image.convert("RGB"),
                border=border_width,
                fill=border_color
            )

        # ----------------------------------------------------
        # OUTPUT FORMAT
        # ----------------------------------------------------

        output_format = str(
            data.get(
                "format",
                "png"
            )
        ).lower()

        if output_format not in {
            "jpg",
            "jpeg",
            "png",
            "webp"
        }:

            output_format = "png"

        if output_format == "jpeg":
            extension = "jpg"
        else:
            extension = output_format

        # ----------------------------------------------------
        # QUALITY
        # ----------------------------------------------------

        quality = int(
            data.get(
                "quality",
                90
            )
        )

        quality = max(
            10,
            min(
                100,
                quality
            )
        )

        # ----------------------------------------------------
        # OUTPUT FILE
        # ----------------------------------------------------

        output_id = uuid.uuid4().hex

        output_path = os.path.join(
            TEMP_DIR,
            f"{output_id}.{extension}"
        )

        # ----------------------------------------------------
        # JPEG
        # ----------------------------------------------------

        if output_format in {
            "jpg",
            "jpeg"
        }:

            if image.mode != "RGB":

                if image.mode == "RGBA":

                    background = Image.new(
                        "RGB",
                        image.size,
                        "white"
                    )

                    background.paste(
                        image,
                        mask=image.getchannel(
                            "A"
                        )
                    )

                    image = background

                else:

                    image = image.convert(
                        "RGB"
                    )

            image.save(
                output_path,
                "JPEG",
                quality=quality,
                optimize=True
            )

        # ----------------------------------------------------
        # WEBP
        # ----------------------------------------------------

        elif output_format == "webp":

            image.save(
                output_path,
                "WEBP",
                quality=quality,
                method=6
            )

        # ----------------------------------------------------
        # PNG
        # ----------------------------------------------------

        else:

            image.save(
                output_path,
                "PNG",
                optimize=True
            )

        return jsonify({

            "success": True,

            "output_id": output_id,

            "width": image.width,

            "height": image.height,

            "format": extension.upper(),

            "download_url": url_for(
                "crop_image.download",
                output_id=output_id,
                extension=extension
            )

        })

    except Exception as error:

        return jsonify({

            "success": False,

            "error": str(error)

        }), 500


# ============================================================
# DOWNLOAD
# ============================================================

@crop_image_bp.route(
    "/download/<output_id>/<extension>"
)
def download(
    output_id,
    extension
):

    if not logged_in():

        return redirect(
            url_for("auth.login")
        )

    extension = extension.lower()

    if extension not in {
        "jpg",
        "png",
        "webp"
    }:

        return "Invalid file format.", 400

    path = os.path.join(
        TEMP_DIR,
        f"{output_id}.{extension}"
    )

    if not os.path.exists(path):

        return "File not found.", 404

    mime_types = {

        "jpg":
            "image/jpeg",

        "png":
            "image/png",

        "webp":
            "image/webp"

    }

    return send_file(

        path,

        mimetype=mime_types[
            extension
        ],

        as_attachment=(
            request.args.get("preview") != "1"
        ),

        download_name=(
            f"DocBuddy_Cropped."
            f"{extension}"
        )
    )


# ============================================================
# DELETE TEMPORARY SOURCE
# ============================================================

@crop_image_bp.route(
    "/delete/<file_id>",
    methods=["POST"]
)
def delete(file_id):

    if not logged_in():

        return jsonify({
            "success": False
        }), 401

    deleted = False

    for extension in ALLOWED_EXTENSIONS:

        path = os.path.join(
            TEMP_DIR,
            f"{file_id}.{extension}"
        )

        if os.path.exists(path):

            try:

                os.remove(path)

                deleted = True

            except OSError:
                pass

    return jsonify({

        "success": True,

        "deleted": deleted

    })