from pathlib import Path
from PIL import Image


def convert_jpg_to_pdf(image_paths, output_path):
    """
    Convert one or more JPG/JPEG/PNG images into a single PDF.

    image_paths:
        List of image file paths.

    output_path:
        Path where the PDF should be created.

    Returns:
        The created PDF path.
    """

    if not image_paths:
        raise ValueError("No images were provided.")

    images = []

    try:
        for image_path in image_paths:

            image_path = Path(image_path)

            if not image_path.exists():
                raise FileNotFoundError(
                    f"Image not found: {image_path}"
                )

            image = Image.open(image_path)

            # PDF requires RGB/RGBA-compatible image data.
            if image.mode != "RGB":
                image = image.convert("RGB")

            images.append(image)

        output_path = Path(output_path)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        first_image = images[0]

        remaining_images = images[1:]

        first_image.save(
            output_path,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=remaining_images
        )

        return output_path

    finally:

        for image in images:
            try:
                image.close()
            except Exception:
                pass