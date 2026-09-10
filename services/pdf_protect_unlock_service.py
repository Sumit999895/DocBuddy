import os
from pathlib import Path

import fitz


# ============================================================
# PROTECT PDF
# ============================================================

def protect_pdf(
    input_path,
    output_path,
    password
):
    """
    Protect a PDF with AES-256 encryption.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.is_file():
        raise ValueError(
            "Input PDF was not found."
        )

    if not password:
        raise ValueError(
            "A password is required."
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    document = None

    try:

        document = fitz.open(
            str(input_path)
        )

        if document.page_count == 0:
            raise ValueError(
                "The PDF contains no pages."
            )

        # AES-256 encryption
        document.save(
            str(output_path),
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw=password,
            permissions=(
                fitz.PDF_PERM_ACCESSIBILITY
                | fitz.PDF_PERM_PRINT
            ),
            garbage=3,
            deflate=True,
            clean=True
        )

    finally:

        if document is not None:
            document.close()

    if not output_path.is_file():
        raise RuntimeError(
            "Protected PDF was not created."
        )

    if output_path.stat().st_size <= 0:
        raise RuntimeError(
            "Protected PDF is empty."
        )

    return str(output_path)


# ============================================================
# UNLOCK PDF
# ============================================================

def unlock_pdf(
    input_path,
    output_path,
    password
):
    """
    Remove password protection from a PDF.

    The supplied password must be correct.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.is_file():
        raise ValueError(
            "Input PDF was not found."
        )

    if not password:
        raise ValueError(
            "The PDF password is required."
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    document = None

    try:

        document = fitz.open(
            str(input_path)
        )

        # ----------------------------------------------------
        # Authenticate password
        # ----------------------------------------------------

        if document.needs_pass:

            authenticated = document.authenticate(
                password
            )

            if not authenticated:
                raise ValueError(
                    "Incorrect PDF password."
                )

        # ----------------------------------------------------
        # Save without encryption
        # ----------------------------------------------------

        document.save(
            str(output_path),
            encryption=fitz.PDF_ENCRYPT_NONE,
            garbage=4,
            deflate=True,
            clean=True
        )

    finally:

        if document is not None:
            document.close()

    if not output_path.is_file():
        raise RuntimeError(
            "Unlocked PDF was not created."
        )

    if output_path.stat().st_size <= 0:
        raise RuntimeError(
            "Unlocked PDF is empty."
        )

    return str(output_path)