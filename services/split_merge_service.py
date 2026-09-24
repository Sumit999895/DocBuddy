# services/split_merge_service.py

import os
import re
import tempfile
from pathlib import Path
from typing import List, Tuple

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class PDFValidationError(Exception):
    pass


class PDFSplitMergeService:
    ALLOWED_EXTENSION = ".pdf"

    # ---------------------------------------------------------
    # Validate uploaded PDF
    # ---------------------------------------------------------
    @staticmethod
    def _validate_pdf(file_storage):
        if not file_storage:
            raise PDFValidationError("Please upload a PDF file.")

        filename = file_storage.filename or ""

        if not filename.lower().endswith(
            PDFSplitMergeService.ALLOWED_EXTENSION
        ):
            raise PDFValidationError("Only PDF files are supported.")

        try:
            reader = PdfReader(file_storage)

            if reader.is_encrypted:
                raise PDFValidationError(
                    "Password-protected PDFs are not supported."
                )

            total_pages = len(reader)

            if total_pages == 0:
                raise PDFValidationError(
                    "The uploaded PDF has no pages."
                )

            return reader

        except PDFValidationError:
            raise

        except Exception as exc:
            raise PDFValidationError(
                f"Could not read the PDF: {exc}"
            )

    # ---------------------------------------------------------
    # Parse page ranges
    # Example:
    # 1-3, 5, 8-10
    # ---------------------------------------------------------
    @staticmethod
    def _parse_page_ranges(
        ranges_str: str,
        total_pages: int
    ) -> List[Tuple[int, int]]:

        if not ranges_str or not ranges_str.strip():
            raise PDFValidationError(
                "Please enter at least one page range."
            )

        ranges = []

        for part in ranges_str.split(","):
            part = part.strip()

            if not part:
                continue

            # Range: 1-3
            if "-" in part:
                pieces = part.split("-", 1)

                if len(pieces) != 2:
                    raise PDFValidationError(
                        f"Invalid range: {part}"
                    )

                try:
                    start = int(pieces[0].strip())
                    end = int(pieces[1].strip())
                except ValueError:
                    raise PDFValidationError(
                        f"Invalid range: {part}"
                    )

            # Single page: 5
            else:
                try:
                    start = int(part)
                    end = start
                except ValueError:
                    raise PDFValidationError(
                        f"Invalid page number: {part}"
                    )

            if start < 1 or end < 1:
                raise PDFValidationError(
                    "Page numbers must start from 1."
                )

            if start > end:
                start, end = end, start

            if end > total_pages:
                raise PDFValidationError(
                    f"Page {end} does not exist. "
                    f"This PDF has {total_pages} pages."
                )

            ranges.append((start, end))

        if not ranges:
            raise PDFValidationError(
                "No valid page ranges were provided."
            )

        return ranges

    # ---------------------------------------------------------
    # Create one PDF for a page range
    # ---------------------------------------------------------
    @staticmethod
    def create_pdf_for_range(
        reader: PdfReader,
        start_page: int,
        end_page: int,
        output_path: str
    ):

        writer = PdfWriter()

        for page_number in range(
            start_page - 1,
            end_page
        ):
            writer.add_page(reader.pages[page_number])

        with open(output_path, "wb") as output_file:
            writer.write(output_file)

    # ---------------------------------------------------------
    # Split ALL pages
    # Each page becomes its own PDF
    # ---------------------------------------------------------
    @staticmethod
    def split_all_pages(
        reader: PdfReader,
        output_dir: str,
        original_name: str
    ):

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = Path(original_name).stem

        results = []

        for index in range(len(reader.pages)):

            page_number = index + 1

            filename = (
                f"{base_name}_page_{page_number}.pdf"
            )

            output_path = output_dir / filename

            writer = PdfWriter()
            writer.add_page(reader.pages[index])

            with open(output_path, "wb") as output_file:
                writer.write(output_file)

            results.append({
                "filename": filename,
                "path": str(output_path),
                "label": f"Page {page_number}",
                "start_page": page_number,
                "end_page": page_number,
            })

        return results

    # ---------------------------------------------------------
    # Split by ranges
    #
    # Example:
    # 1-3,5,8-10
    #
    # Creates:
    # document_pages_1-3.pdf
    # document_page_5.pdf
    # document_pages_8-10.pdf
    # ---------------------------------------------------------
    @staticmethod
    def split_by_ranges(
        reader: PdfReader,
        ranges_str: str,
        output_dir: str,
        original_name: str
    ):

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = Path(original_name).stem

        ranges = PDFSplitMergeService._parse_page_ranges(
            ranges_str,
            len(reader.pages)
        )

        results = []

        for start_page, end_page in ranges:

            if start_page == end_page:
                filename = (
                    f"{base_name}_page_{start_page}.pdf"
                )
                label = f"Page {start_page}"
            else:
                filename = (
                    f"{base_name}_pages_"
                    f"{start_page}-{end_page}.pdf"
                )
                label = (
                    f"Pages {start_page}-{end_page}"
                )

            output_path = output_dir / filename

            PDFSplitMergeService.create_pdf_for_range(
                reader,
                start_page,
                end_page,
                str(output_path)
            )

            results.append({
                "filename": filename,
                "path": str(output_path),
                "label": label,
                "start_page": start_page,
                "end_page": end_page,
            })

        return results

    # ---------------------------------------------------------
    # Page count
    # ---------------------------------------------------------
    @staticmethod
    def get_page_count(file_storage):

        reader = PDFSplitMergeService._validate_pdf(
            file_storage
        )

        return len(reader)

    # ---------------------------------------------------------
    # Merge PDFs
    # ---------------------------------------------------------
    @staticmethod
    def merge_pdfs(
        files,
        output_path: str,
        output_name: str = "merged"
    ):

        if not files or len(files) < 2:
            raise PDFValidationError(
                "Please select at least two PDF files."
            )

        writer = PdfWriter()

        safe_name = re.sub(
            r"[^a-zA-Z0-9_\- ]",
            "",
            output_name
        ).strip()

        if not safe_name:
            safe_name = "merged"

        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"

        for file_storage in files:

            if not file_storage:
                continue

            filename = file_storage.filename or ""

            if not filename.lower().endswith(".pdf"):
                raise PDFValidationError(
                    f"{filename} is not a PDF file."
                )

            try:
                reader = PdfReader(file_storage)

                if reader.is_encrypted:
                    raise PDFValidationError(
                        f"{filename} is password protected."
                    )

                for page in reader.pages:
                    writer.add_page(page)

            except PDFValidationError:
                raise

            except Exception as exc:
                raise PDFValidationError(
                    f"Could not read {filename}: {exc}"
                )

        with open(output_path, "wb") as output_file:
            writer.write(output_file)

        return {
            "filename": safe_name,
            "path": output_path,
        }