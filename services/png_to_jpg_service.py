from pathlib import Path
from PIL import Image


def convert_png_to_jpg(png_path, jpg_path, quality=95):
    """
    Convert a PNG image to JPG.

    Parameters:
        png_path: Input PNG path
        jpg_path: Output JPG path
        quality: JPG quality (1-100)

    Returns:
        Path to the converted JPG file
    """

    png_path = Path(png_path)
    jpg_path = Path(jpg_path)

    jpg_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with Image.open(png_path) as image:

        # JPG does not support transparency.
        # Create a white background for transparent PNGs.
        if image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        ):

            rgba_image = image.convert("RGBA")

            background = Image.new(
                "RGB",
                rgba_image.size,
                "white"
            )

            background.paste(
                rgba_image,
                mask=rgba_image.getchannel("A")
            )

            final_image = background

        else:
            final_image = image.convert("RGB")

        final_image.save(
            jpg_path,
            "JPEG",
            quality=quality,
            optimize=True
        )

    return jpg_path