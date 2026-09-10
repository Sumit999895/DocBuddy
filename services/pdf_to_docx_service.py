from pathlib import Path

from pdf2docx import Converter


def convert_pdf_to_docx(pdf_path, output_path):
    """
    Convert a PDF file to DOCX.

    The function itself does NOT store anything permanently
    and does NOT use a database.

    Parameters
    ----------
    pdf_path : str or Path
        Path to the temporary source PDF.

    output_path : str or Path
        Path where the temporary DOCX should be created.

    Returns
    -------
    Path
        Path to the generated DOCX file.

    Raises
    ------
    FileNotFoundError
        If the source PDF does not exist.

    RuntimeError
        If conversion fails or the DOCX is not created.
    """

    pdf_path = Path(pdf_path)
    output_path = Path(output_path)

    # ---------------------------------------------------------
    # Validate input PDF
    # ---------------------------------------------------------
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF file does not exist: {pdf_path}"
        )

    if not pdf_path.is_file():
        raise FileNotFoundError(
            f"PDF path is not a file: {pdf_path}"
        )

    # ---------------------------------------------------------
    # Create output directory
    # ---------------------------------------------------------
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    converter = None

    try:
        print("=" * 80)
        print("PDF TO DOCX CONVERSION STARTED")
        print(f"Input : {pdf_path}")
        print(f"Output: {output_path}")
        print("=" * 80)

        # -----------------------------------------------------
        # Create converter
        # -----------------------------------------------------
        converter = Converter(
            str(pdf_path)
        )

        # -----------------------------------------------------
        # Convert PDF → DOCX
        # -----------------------------------------------------
        converter.convert(
            str(output_path),
            start=0,
            end=None
        )

        print("pdf2docx conversion completed.")

    except Exception as exc:

        print("=" * 80)
        print("PDF TO DOCX CONVERSION ERROR")
        print(f"Input : {pdf_path}")
        print(f"Output: {output_path}")
        print(f"Error : {repr(exc)}")
        print("=" * 80)

        raise RuntimeError(
            f"PDF to DOCX conversion failed: {exc}"
        ) from exc

    finally:

        # -----------------------------------------------------
        # Always close pdf2docx converter
        # -----------------------------------------------------
        if converter is not None:
            try:
                converter.close()
            except Exception as close_error:
                print(
                    "Warning: Could not close pdf2docx converter:",
                    repr(close_error)
                )

    # ---------------------------------------------------------
    # Verify output
    # ---------------------------------------------------------
    if not output_path.exists():
        raise RuntimeError(
            "pdf2docx finished, but the DOCX file was not created."
        )

    if not output_path.is_file():
        raise RuntimeError(
            "The generated DOCX path is not a file."
        )

    if output_path.stat().st_size == 0:
        raise RuntimeError(
            "The generated DOCX file is empty."
        )

    print("=" * 80)
    print("PDF TO DOCX CONVERSION SUCCESSFUL")
    print(f"DOCX: {output_path}")
    print(
        f"Size: {output_path.stat().st_size / 1024:.2f} KB"
    )
    print("=" * 80)

    return output_path