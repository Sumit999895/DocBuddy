from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
import logging
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger("compress_pdf_service")


class CompressionError(Exception):
    pass


class InvalidPDFError(CompressionError):
    pass


class PasswordRequiredError(CompressionError):
    pass


class EngineUnavailableError(CompressionError):
    pass


class CompressionLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BEST_QUALITY = "best_quality"
    CUSTOM = "custom"


_GS_PROFILE = {
    CompressionLevel.LOW: "/screen",
    CompressionLevel.MEDIUM: "/ebook",
    CompressionLevel.HIGH: "/printer",
    CompressionLevel.BEST_QUALITY: "/prepress",
}

_FALLBACK_PROFILE = {
    CompressionLevel.LOW: (72, 45),
    CompressionLevel.MEDIUM: (150, 65),
    CompressionLevel.HIGH: (220, 78),
    CompressionLevel.BEST_QUALITY: (300, 90),
}


@dataclass
class CompressionOptions:
    level: CompressionLevel = CompressionLevel.MEDIUM
    remove_metadata: bool = False
    linearize: bool = False
    password: Optional[str] = None
    custom_dpi: int = 150
    custom_jpeg_quality: int = 70
    grayscale: bool = False


@dataclass
class CompressionResult:
    job_id: str
    input_filename: str
    output_path: str
    original_size: int
    compressed_size: int
    page_count: int
    engine_used: str
    warnings: list = field(default_factory=list)

    @property
    def bytes_saved(self) -> int:
        return max(self.original_size - self.compressed_size, 0)

    @property
    def percent_reduction(self) -> float:
        if self.original_size <= 0:
            return 0.0
        return round((self.bytes_saved / self.original_size) * 100, 2)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "input_filename": self.input_filename,
            "original_size": self.original_size,
            "compressed_size": self.compressed_size,
            "bytes_saved": self.bytes_saved,
            "percent_reduction": self.percent_reduction,
            "page_count": self.page_count,
            "engine_used": self.engine_used,
            "warnings": self.warnings,
        }


ProgressCallback = Optional[Callable[[str, int], None]]


def _which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def ghostscript_available() -> bool:
    return _which("gs") is not None or _which("gswin64c") is not None or _which("gswin32c") is not None


def qpdf_available() -> bool:
    return _which("qpdf") is not None


def _gs_binary() -> str:
    return _which("gs") or _which("gswin64c") or _which("gswin32c") or "gs"


def pymupdf_available() -> bool:
    try:
        import fitz  # noqa: F401
        return True
    except ImportError:
        return False


def pikepdf_available() -> bool:
    try:
        import pikepdf  # noqa: F401
        return True
    except ImportError:
        return False


def validate_pdf(path: str, password: Optional[str] = None) -> int:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise InvalidPDFError("File is missing or empty.")

    with open(path, "rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise InvalidPDFError("File does not look like a valid PDF.")

    try:
        import fitz
        doc = fitz.open(path)
        try:
            if doc.needs_pass:
                if not password or not doc.authenticate(password):
                    raise PasswordRequiredError("This PDF is password protected. Enter the current password and retry.")
            return doc.page_count
        finally:
            doc.close()
    except PasswordRequiredError:
        raise
    except Exception as exc:
        raise InvalidPDFError(f"Could not read PDF: {exc}") from exc


def _make_decrypted_copy(input_path: str, output_dir: str, password: Optional[str]) -> str:
    """Return a usable path. Encrypted PDFs are rewritten to an unencrypted temp copy."""
    if not password:
        return input_path

    try:
        import fitz
        doc = fitz.open(input_path)
        if not doc.needs_pass:
            doc.close()
            return input_path
        if not doc.authenticate(password):
            doc.close()
            raise PasswordRequiredError("The password provided is incorrect.")
        decrypted = os.path.join(output_dir, f"decrypted_{uuid.uuid4().hex}.pdf")
        doc.save(decrypted, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, encryption=fitz.PDF_ENCRYPT_NONE)
        doc.close()
        return decrypted
    except PasswordRequiredError:
        raise
    except Exception as exc:
        raise CompressionError(f"Could not decrypt the PDF: {exc}") from exc


def _compress_with_ghostscript(input_path: str, output_path: str, options: CompressionOptions, progress: ProgressCallback = None) -> list:
    warnings = []
    profile = _GS_PROFILE.get(options.level, "/ebook")
    if progress:
        progress("Running Ghostscript compression…", 30)

    args = [
        _gs_binary(), "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",
        f"-dPDFSETTINGS={profile}", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        "-dDetectDuplicateImages=true", "-dCompressFonts=true", "-dSubsetFonts=true",
        f"-sOutputFile={output_path}", input_path,
    ]

    if options.level == CompressionLevel.CUSTOM:
        dpi = max(36, min(int(options.custom_dpi), 600))
        args[3:3] = [
            "-dDownsampleColorImages=true", f"-dColorImageResolution={dpi}",
            "-dDownsampleGrayImages=true", f"-dGrayImageResolution={dpi}",
            "-dDownsampleMonoImages=true", f"-dMonoImageResolution={dpi}",
            "-dColorImageDownsampleType=/Bicubic", "-dGrayImageDownsampleType=/Bicubic",
        ]
        args.insert(3, f"-dJPEGQ={max(10, min(int(options.custom_jpeg_quality), 100))}")

    if options.grayscale:
        args += ["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray"]
    if options.remove_metadata:
        args += ["-dPreserveMetadata=false"]

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise CompressionError("Ghostscript timed out while compressing this PDF.") from exc

    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        detail = (proc.stderr or proc.stdout or "unknown Ghostscript error").strip()
        raise CompressionError(f"Ghostscript failed: {detail[:700]}")

    if options.linearize and qpdf_available():
        if progress:
            progress("Linearizing for fast web view…", 85)
        linear_path = output_path + ".linear.pdf"
        proc = subprocess.run(["qpdf", "--linearize", output_path, linear_path], capture_output=True, text=True, timeout=180)
        if proc.returncode == 0 and os.path.exists(linear_path):
            os.replace(linear_path, output_path)
        else:
            warnings.append("Fast web view could not be applied; the compressed PDF is still valid.")
    elif options.linearize:
        warnings.append("qpdf is not installed, so fast web view was skipped.")

    return warnings


def _compress_with_pymupdf(input_path: str, output_path: str, options: CompressionOptions, progress: ProgressCallback = None) -> list:
    import fitz

    warnings = []
    if progress:
        progress("Opening PDF…", 10)

    doc = fitz.open(input_path)
    try:
        if doc.needs_pass:
            if not options.password or not doc.authenticate(options.password):
                raise PasswordRequiredError("This PDF is password protected. Enter the current password and retry.")

        if options.level == CompressionLevel.CUSTOM:
            dpi = max(36, min(int(options.custom_dpi), 600))
            quality = max(10, min(int(options.custom_jpeg_quality), 100))
        else:
            dpi, quality = _FALLBACK_PROFILE.get(options.level, (150, 65))

        if progress:
            progress(f"Re-encoding images at up to {dpi} DPI…", 25)

        # rewrite_images is implemented by modern PyMuPDF and safely rewrites
        # image streams while preserving page geometry and text/vector content.
        doc.rewrite_images(
            dpi_threshold=max(dpi + 1, dpi),
            dpi_target=dpi,
            quality=quality,
            lossy=True,
            lossless=True,
            bitonal=True,
            color=True,
            gray=True,
            set_to_gray=options.grayscale,
        )

        if options.remove_metadata:
            doc.set_metadata({})
            try:
                doc.del_xml_metadata()
            except Exception:
                pass

        if progress:
            progress("Writing optimized PDF…", 85)

        save_kwargs = {
            # Keep the save pass lightweight. garbage=3 removes unused objects
            # without the extra full-document sweep of garbage=4.
            "garbage": 3,
            "clean": True,
            "deflate": True,
            "deflate_images": True,
            "deflate_fonts": True,
            "use_objstms": True,
            "encryption": fitz.PDF_ENCRYPT_NONE,
        }
        if options.linearize:
            # PyMuPDF supports linear=1 on versions that expose it.
            save_kwargs["linear"] = True

        doc.save(output_path, **save_kwargs)
    finally:
        doc.close()

    return warnings


def _compress_with_pikepdf(input_path: str, output_path: str, options: CompressionOptions, progress: ProgressCallback = None) -> list:
    import pikepdf
    warnings = ["Only structural compression was available; images were not aggressively recompressed."]
    if progress:
        progress("Compressing PDF structure…", 45)
    try:
        with pikepdf.open(input_path, password=options.password or "") as pdf:
            if options.remove_metadata:
                try:
                    with pdf.open_metadata() as meta:
                        meta.clear()
                except Exception:
                    pass
                if "/Info" in pdf.trailer:
                    del pdf.trailer["/Info"]
            pdf.save(
                output_path,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                linearize=options.linearize,
            )
    except pikepdf.PasswordError as exc:
        raise PasswordRequiredError("The password provided is incorrect or missing.") from exc
    return warnings


def compress_pdf(input_path: str, output_dir: str, options: Optional[CompressionOptions] = None, progress: ProgressCallback = None, job_id: Optional[str] = None) -> CompressionResult:
    options = options or CompressionOptions()
    job_id = job_id or uuid.uuid4().hex[:12]
    os.makedirs(output_dir, exist_ok=True)

    if progress:
        progress("Validating PDF…", 5)
    page_count = validate_pdf(input_path, options.password)

    original_size = os.path.getsize(input_path)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_compressed_{job_id}.pdf")
    warnings = []
    engine_used = "none"

    # Password-protected files must be decrypted before Ghostscript can read them.
    work_input = _make_decrypted_copy(input_path, output_dir, options.password)

    try:
        # PyMuPDF is intentionally preferred: it avoids spawning an external
        # Ghostscript process and is substantially quicker for ordinary PDFs.
        if pymupdf_available():
            engine_used = "pymupdf"
            warnings = _compress_with_pymupdf(work_input, output_path, options, progress)
        elif ghostscript_available():
            engine_used = "ghostscript"
            warnings = _compress_with_ghostscript(work_input, output_path, options, progress)
        elif pikepdf_available():
            engine_used = "pikepdf"
            warnings = _compress_with_pikepdf(work_input, output_path, options, progress)
        else:
            raise EngineUnavailableError("No compression engine is installed. Install PyMuPDF with: python -m pip install pymupdf")
    except (PasswordRequiredError, InvalidPDFError, EngineUnavailableError):
        raise
    except Exception as exc:
        raise CompressionError(f"Compression failed: {exc}") from exc
    finally:
        if work_input != input_path and os.path.exists(work_input):
            try:
                os.remove(work_input)
            except OSError:
                pass

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise CompressionError("Compression finished without producing a valid output PDF.")

    compressed_size = os.path.getsize(output_path)
    if compressed_size >= original_size:
        # A PDF can already be highly optimized. Never claim that an output is
        # smaller when it is not; return the original bytes under the output name.
        try:
            shutil.copy2(input_path, output_path)
            compressed_size = original_size
            warnings.append("This PDF was already highly optimized, so the original size was kept.")
        except OSError:
            warnings.append("The optimized PDF was not smaller than the original.")

    if progress:
        progress("Done!", 100)

    return CompressionResult(
        job_id=job_id,
        input_filename=os.path.basename(input_path),
        output_path=output_path,
        original_size=original_size,
        compressed_size=compressed_size,
        page_count=page_count,
        engine_used=engine_used,
        warnings=warnings,
    )


def compress_pdf_batch(input_paths: list, output_dir: str, options: Optional[CompressionOptions] = None, progress: ProgressCallback = None) -> list:
    results = []
    total = max(len(input_paths), 1)
    for index, path in enumerate(input_paths):
        def wrapped(message, percent, idx=index, filename=os.path.basename(path)):
            if progress:
                overall = int(((idx + (percent / 100.0)) / total) * 100)
                progress(f"{filename}: {message}", min(99, overall))
        results.append(compress_pdf(path, output_dir, options, wrapped))
    return results


def make_zip_of_results(results: list, zip_path: str) -> str:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            archive.write(result.output_path, os.path.basename(result.output_path))
    return zip_path


def new_temp_workspace() -> str:
    return tempfile.mkdtemp(prefix="pdf_compress_")


def cleanup_workspace(path: str) -> None:
    if path:
        shutil.rmtree(path, ignore_errors=True)


def available_engines() -> dict:
    return {
        "ghostscript": ghostscript_available(),
        "pymupdf": pymupdf_available(),
        "pikepdf": pikepdf_available(),
        "qpdf_linearize": qpdf_available(),
        "recommended_engine": (
            "ghostscript" if ghostscript_available()
            else "pymupdf" if pymupdf_available()
            else "pikepdf" if pikepdf_available()
            else None
        ),
    }
