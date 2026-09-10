from pathlib import Path

from PIL import Image, ImageOps


def convert_jpg_to_png(input_path, output_path):
    """
    Convert a JPG/JPEG/PNG image to PNG.

    Temporary storage and automatic deletion are handled
    by the Flask route. This function only performs conversion.

    Parameters:
        input_path: Input image path.
        output_path: Output PNG path.

    Returns:
        Path: Path to the generated PNG file.

    Raises:
        FileNotFoundError: If the input file does not exist.
        RuntimeError: If conversion fails or output is invalid.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    # ---------------------------------------------------------
    # VALIDATE INPUT
    # ---------------------------------------------------------

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input image does not exist: {input_path}"
        )

    if not input_path.is_file():
        raise FileNotFoundError(
            f"Input image is not a file: {input_path}"
        )

    # ---------------------------------------------------------
    # CREATE OUTPUT DIRECTORY
    # ---------------------------------------------------------

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # ---------------------------------------------------------
    # CONVERT IMAGE
    # ---------------------------------------------------------

    try:
        with Image.open(input_path) as image:

            # Apply camera EXIF orientation before saving.
            image = ImageOps.exif_transpose(image)

            # PNG supports RGB/RGBA. Preserve transparency when
            # the source image has an alpha channel.
            if "A" in image.getbands():
                converted_image = image.convert("RGBA")
            else:
                converted_image = image.convert("RGB")

            try:
                converted_image.save(
                    output_path,
                    format="PNG"
                )
            finally:
                converted_image.close()

    except Exception as exc:
        print("=" * 80)
        print("JPG TO PNG SERVICE ERROR")
        print(f"Input : {input_path}")
        print(f"Output: {output_path}")
        print(f"Error : {repr(exc)}")
        print("=" * 80)

        # Remove incomplete output if conversion failed.
        try:
            if output_path.exists():
                output_path.unlink()
        except OSError:
            pass

        raise RuntimeError(
            f"Image conversion failed: {exc}"
        ) from exc

    # ---------------------------------------------------------
    # VERIFY OUTPUT
    # ---------------------------------------------------------

    if not output_path.exists():
        raise RuntimeError(
            "Conversion finished, but the PNG file was not created."
        )

    if not output_path.is_file():
        raise RuntimeError(
            "The generated PNG path is not a file."
        )

    if output_path.stat().st_size == 0:
        raise RuntimeError(
            "The generated PNG file is empty."
        )

    # Verify that Pillow can open the generated PNG.
    try:
        with Image.open(output_path) as result:
            result.verify()
    except Exception as exc:
        try:
            output_path.unlink()
        except OSError:
            pass

        raise RuntimeError(
            f"The generated PNG file is invalid: {exc}"
        ) from exc

    return output_path
