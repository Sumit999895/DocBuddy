from pathlib import Path
import logging

from pdf2docx import Converter
from pdf2image import convert_from_path


logger = logging.getLogger(__name__)


# ============================================================
# PDF → DOCX
# ============================================================

def convert_pdf_to_docx(pdf_path, output_path):
    """
    Convert a PDF file to DOCX.

    Parameters:
        pdf_path: Path to input PDF
        output_path: Path where DOCX should be created

    Returns:
        Path to the generated DOCX file
    """

    pdf_path = Path(pdf_path)
    output_path = Path(output_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Input file must be a PDF")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    converter = None

    try:
        logger.info("Converting PDF to DOCX: %s", pdf_path)

        converter = Converter(str(pdf_path))

        converter.convert(
            str(output_path),
            start=0,
            end=None
        )

        if not output_path.exists():
            raise RuntimeError(
                "PDF to DOCX conversion completed but output file was not created."
            )

        if output_path.stat().st_size == 0:
            raise RuntimeError(
                "PDF to DOCX conversion created an empty output file."
            )

        logger.info("PDF to DOCX completed: %s", output_path)

        return output_path

    except Exception as exc:
        logger.exception("PDF to DOCX conversion failed")
        raise RuntimeError(
            f"PDF to DOCX conversion failed: {exc}"
        ) from exc

    finally:
        if converter is not None:
            try:
                converter.close()
            except Exception:
                pass


# ============================================================
# PDF → JPG
# ============================================================

def convert_pdf_to_jpg(pdf_path, output_folder, poppler_path=None):
    """
    Convert every page of a PDF into a JPG image.

    Parameters:
        pdf_path: Path to input PDF
        output_folder: Folder where JPG files will be created
        poppler_path: Optional Poppler bin path

    Returns:
        List of generated JPG file paths
    """

    pdf_path = Path(pdf_path)
    output_folder = Path(output_folder)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Input file must be a PDF")

    output_folder.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("Converting PDF to JPG: %s", pdf_path)

        kwargs = {
            "dpi": 200,
            "fmt": "jpeg",
            "output_folder": str(output_folder),
            "paths_only": True,
        }

        # Only pass poppler_path when one is configured.
        if poppler_path:
            kwargs["poppler_path"] = poppler_path

        image_paths = convert_from_path(
            str(pdf_path),
            **kwargs
        )

        generated_files = []

        for index, image_path in enumerate(image_paths, start=1):
            image_path = Path(image_path)

            # pdf2image normally creates the JPG directly.
            # Rename it to a clean predictable filename.
            final_path = output_folder / f"page_{index}.jpg"

            if image_path != final_path:
                if final_path.exists():
                    final_path.unlink()

                image_path.rename(final_path)

            if not final_path.exists():
                raise RuntimeError(
                    f"JPG output was not created for page {index}."
                )

            if final_path.stat().st_size == 0:
                raise RuntimeError(
                    f"JPG output for page {index} is empty."
                )

            generated_files.append(final_path)

        if not generated_files:
            raise RuntimeError(
                "PDF to JPG conversion produced no images."
            )

        logger.info(
            "PDF to JPG completed: %d page(s)",
            len(generated_files)
        )

        return generated_files

    except Exception as exc:
        logger.exception("PDF to JPG conversion failed")

        # Remove partially generated JPG files.
        try:
            for file in output_folder.glob("*.jpg"):
                file.unlink(missing_ok=True)
        except Exception:
            pass

        raise RuntimeError(
            f"PDF to JPG conversion failed: {exc}"
        ) from exc