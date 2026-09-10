import io
import os
import re
import json
import base64
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional

import fitz
import pytesseract

from PIL import (
    Image,
    ImageOps,
    ImageFilter,
    ImageEnhance,
)

import shutil

tesseract_path = shutil.which("tesseract")

if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

# ============================================================
# CONFIGURATION
# ============================================================

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

DEFAULT_DPI = 200
MAX_PAGES = 100


# ============================================================
# FILE HELPERS
# ============================================================

def is_supported_file(filename: str) -> bool:
    if not filename:
        return False

    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def safe_stem(filename: str) -> str:
    stem = Path(filename).stem

    stem = re.sub(
        r"[^A-Za-z0-9_\-]+",
        "_",
        stem
    )

    stem = stem.strip("_")

    return stem or "scanned_document"


# ============================================================
# IMAGE PROCESSING
# ============================================================

def load_image(image_source) -> Image.Image:
    """
    Load an image from a PIL object, path or bytes.
    """

    if isinstance(image_source, Image.Image):
        return image_source.copy()

    if isinstance(image_source, bytes):
        image = Image.open(
            io.BytesIO(image_source)
        )

    else:
        image = Image.open(
            image_source
        )

    return image.copy()


def enhance_document_image(
    image: Image.Image
) -> Image.Image:
    """
    Improve document image quality before OCR.
    """

    image = ImageOps.exif_transpose(image)

    # Convert uncommon modes
    if image.mode not in (
        "RGB",
        "L"
    ):
        image = image.convert("RGB")

    # Avoid extremely large OCR images
    max_dimension = 5000

    width, height = image.size

    if max(width, height) > max_dimension:

        scale = (
            max_dimension /
            float(max(width, height))
        )

        image = image.resize(
            (
                int(width * scale),
                int(height * scale)
            ),
            Image.Resampling.LANCZOS
        )

    # Grayscale
    gray = ImageOps.grayscale(
        image
    )

    # Improve contrast
    gray = ImageOps.autocontrast(
        gray,
        cutoff=1
    )

    gray = ImageEnhance.Contrast(
        gray
    ).enhance(1.25)

    # Slight sharpening
    gray = gray.filter(
        ImageFilter.SHARPEN
    )

    return gray


# ============================================================
# OCR
# ============================================================

def detect_orientation(
    image: Image.Image
) -> int:
    """
    Try to detect document orientation.

    Returns:
        0, 90, 180 or 270
    """

    try:

        data = pytesseract.image_to_osd(
            image,
            config="--psm 0"
        )

        match = re.search(
            r"Rotate:\s+(\d+)",
            data
        )

        if match:

            rotation = int(
                match.group(1)
            )

            if rotation in (
                0,
                90,
                180,
                270
            ):
                return rotation

    except Exception:
        pass

    return 0


def rotate_for_ocr(
    image: Image.Image
) -> Image.Image:

    rotation = detect_orientation(
        image
    )

    if rotation == 90:
        return image.rotate(
            90,
            expand=True
        )

    if rotation == 180:
        return image.rotate(
            180,
            expand=True
        )

    if rotation == 270:
        return image.rotate(
            270,
            expand=True
        )

    return image


def perform_ocr(
    image: Image.Image,
    language: str = "eng"
) -> Dict[str, Any]:
    """
    Perform OCR and return text plus basic statistics.
    """

    image = rotate_for_ocr(
        image
    )

    # OCR configuration
    config = (
        "--oem 3 "
        "--psm 3"
    )

    try:

        text = pytesseract.image_to_string(
            image,
            lang=language,
            config=config
        )

    except Exception as exc:

        raise RuntimeError(
            "OCR failed. Make sure Tesseract OCR "
            "is installed correctly."
        ) from exc

    text = clean_ocr_text(
        text
    )

    words = [
        word
        for word in text.split()
        if word.strip()
    ]

    return {
        "text": text,
        "word_count": len(words),
        "character_count": len(text),
        "line_count": len(
            [
                line
                for line in text.splitlines()
                if line.strip()
            ]
        ),
    }


def clean_ocr_text(text: str) -> str:
    """
    Clean common OCR whitespace problems.
    """

    if not text:
        return ""

    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    # Remove excessive spaces
    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    # Remove excessive blank lines
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text
    )

    lines = []

    for line in text.splitlines():

        line = line.strip()

        if line:
            lines.append(line)

    return "\n".join(lines).strip()


# ============================================================
# PDF PAGE RENDERING
# ============================================================

def render_pdf_page(
    page,
    dpi: int = DEFAULT_DPI
) -> Image.Image:

    scale = dpi / 72.0

    matrix = fitz.Matrix(
        scale,
        scale
    )

    pixmap = page.get_pixmap(
        matrix=matrix,
        alpha=False
    )

    image_bytes = pixmap.tobytes(
        "png"
    )

    return Image.open(
        io.BytesIO(image_bytes)
    ).convert("RGB")


# ============================================================
# OCR PDF
# ============================================================

def scan_pdf(
    input_path: str,
    language: str = "eng",
    progress_callback=None
) -> Dict[str, Any]:

    input_path = Path(
        input_path
    )

    if not input_path.is_file():
        raise FileNotFoundError(
            "Input PDF was not found."
        )

    document = fitz.open(
        str(input_path)
    )

    try:

        page_count = document.page_count

        if page_count == 0:
            raise ValueError(
                "The PDF contains no pages."
            )

        if page_count > MAX_PAGES:
            raise ValueError(
                f"PDF cannot contain more than "
                f"{MAX_PAGES} pages."
            )

        pages = []

        all_text = []

        for index in range(page_count):

            image = render_pdf_page(
                document[index]
            )

            enhanced = enhance_document_image(
                image
            )

            ocr_result = perform_ocr(
                enhanced,
                language
            )

            page_text = ocr_result["text"]

            pages.append({
                "page": index + 1,
                "text": page_text,
                "word_count": ocr_result[
                    "word_count"
                ],
            })

            all_text.append(
                page_text
            )

            if progress_callback:

                progress_callback(
                    index + 1,
                    page_count
                )

        combined_text = "\n\n".join(
            all_text
        ).strip()

        return {
            "text": combined_text,
            "pages": pages,
            "page_count": page_count,
        }

    finally:

        document.close()


# ============================================================
# IMAGE SCANNING
# ============================================================

def scan_image(
    input_path: str,
    language: str = "eng"
) -> Dict[str, Any]:

    image = load_image(
        input_path
    )

    enhanced = enhance_document_image(
        image
    )

    result = perform_ocr(
        enhanced,
        language
    )

    return {
        "text": result["text"],
        "pages": [
            {
                "page": 1,
                "text": result["text"],
                "word_count": result[
                    "word_count"
                ],
            }
        ],
        "page_count": 1,
        "word_count": result[
            "word_count"
        ],
    }


# ============================================================
# CREATE SEARCHABLE PDF
# ============================================================

def _insert_hidden_text(
    page,
    text: str,
    width: float,
    height: float
):
    """
    Add an invisible OCR text layer.

    render_mode=3 makes the text invisible while
    keeping it searchable/selectable in supported
    PDF viewers.
    """

    if not text:
        return

    # Split into manageable lines
    lines = text.splitlines()

    if not lines:
        return

    line_height = max(
        8,
        min(
            14,
            height / max(
                len(lines),
                1
            )
        )
    )

    # Use a small invisible font
    font_size = 8

    y = 5

    for line in lines:

        if y > height - 5:
            break

        line = line.strip()

        if not line:
            y += line_height
            continue

        try:

            page.insert_textbox(
                fitz.Rect(
                    2,
                    y,
                    width - 2,
                    y + line_height + 3
                ),
                line,
                fontsize=font_size,
                fontname="helv",
                color=(0, 0, 0),
                render_mode=3,
                overlay=True,
            )

        except Exception:
            pass

        y += line_height


def create_searchable_pdf_from_images(
    images: List[Image.Image],
    page_texts: List[str],
    output_path: str
):
    """
    Create a PDF from scanned images and add an
    invisible OCR text layer.
    """

    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output_document = fitz.open()

    try:

        for index, image in enumerate(
            images
        ):

            image = image.convert(
                "RGB"
            )

            width_px, height_px = (
                image.size
            )

            # PDF uses points.
            # 72 DPI equivalent.
            width = float(
                width_px
            )

            height = float(
                height_px
            )

            page = output_document.new_page(
                width=width,
                height=height
            )

            image_buffer = io.BytesIO()

            image.save(
                image_buffer,
                format="JPEG",
                quality=90,
                optimize=True
            )

            page.insert_image(
                page.rect,
                stream=image_buffer.getvalue()
            )

            text = ""

            if index < len(page_texts):
                text = page_texts[index]

            _insert_hidden_text(
                page,
                text,
                width,
                height
            )

        output_document.save(
            str(output_path),
            garbage=4,
            deflate=True,
            clean=True
        )

    finally:

        output_document.close()

    return str(output_path)


def create_searchable_pdf_from_file(
    input_path: str,
    page_results: List[Dict[str, Any]],
    output_path: str,
    dpi: int = DEFAULT_DPI
):

    input_path = Path(
        input_path
    )

    source = fitz.open(
        str(input_path)
    )

    output = fitz.open()

    try:

        for index, result in enumerate(
            page_results
        ):

            source_page = source[index]

            image = render_pdf_page(
                source_page,
                dpi=dpi
            )

            width_px, height_px = (
                image.size
            )

            page = output.new_page(
                width=float(width_px),
                height=float(height_px)
            )

            image_buffer = io.BytesIO()

            image.save(
                image_buffer,
                format="JPEG",
                quality=90,
                optimize=True
            )

            page.insert_image(
                page.rect,
                stream=image_buffer.getvalue()
            )

            _insert_hidden_text(
                page,
                result.get("text", ""),
                float(width_px),
                float(height_px)
            )

        output.save(
            str(output_path),
            garbage=4,
            deflate=True,
            clean=True
        )

    finally:

        source.close()
        output.close()

    return str(output_path)


# ============================================================
# TXT EXPORT
# ============================================================

def save_text_file(
    text: str,
    output_path: str
):

    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output_path.write_text(
        text or "",
        encoding="utf-8"
    )

    return str(output_path)


# ============================================================
# LOCAL AI FALLBACK
# ============================================================

def local_ai_analysis(
    text: str
) -> Dict[str, Any]:
    """
    Lightweight AI-style analysis that works without
    an external API.
    """

    text = text or ""

    lower = text.lower()

    # --------------------------------------------------------
    # Document classification
    # --------------------------------------------------------

    categories = [
        (
            "Invoice",
            [
                "invoice",
                "invoice no",
                "bill to",
                "amount due",
                "subtotal"
            ]
        ),
        (
            "Receipt",
            [
                "receipt",
                "total",
                "cash",
                "change"
            ]
        ),
        (
            "Resume / CV",
            [
                "resume",
                "curriculum vitae",
                "experience",
                "education",
                "skills"
            ]
        ),
        (
            "Identity Document",
            [
                "date of birth",
                "nationality",
                "passport",
                "identity",
                "id number"
            ]
        ),
        (
            "Contract / Agreement",
            [
                "agreement",
                "contract",
                "terms and conditions",
                "party",
                "hereinafter"
            ]
        ),
        (
            "Letter",
            [
                "dear",
                "sincerely",
                "regards"
            ]
        ),
        (
            "Report",
            [
                "report",
                "executive summary",
                "findings",
                "conclusion"
            ]
        ),
    ]

    best_category = "General Document"
    best_score = 0

    for category, keywords in categories:

        score = sum(
            1
            for keyword in keywords
            if keyword in lower
        )

        if score > best_score:
            best_score = score
            best_category = category

    # --------------------------------------------------------
    # Dates
    # --------------------------------------------------------

    date_patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    ]

    dates = []

    for pattern in date_patterns:

        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        for match in matches:

            if match not in dates:
                dates.append(match)

    # --------------------------------------------------------
    # Email addresses
    # --------------------------------------------------------

    emails = re.findall(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        text
    )

    # --------------------------------------------------------
    # Phone numbers
    # --------------------------------------------------------

    phones = re.findall(
        r"(?:\+?\d[\d\s().-]{7,}\d)",
        text
    )

    phones = [
        phone.strip()
        for phone in phones[:10]
    ]

    # --------------------------------------------------------
    # Money
    # --------------------------------------------------------

    amounts = re.findall(
        r"(?:₹|\$|€|£)\s?\d[\d,]*(?:\.\d{1,2})?",
        text
    )

    # --------------------------------------------------------
    # Keywords
    # --------------------------------------------------------

    words = re.findall(
        r"\b[a-zA-Z]{4,}\b",
        lower
    )

    stop_words = {
        "this",
        "that",
        "with",
        "from",
        "your",
        "have",
        "will",
        "there",
        "their",
        "about",
        "which",
        "would",
        "could",
        "should",
        "document",
        "page",
        "please",
        "into",
        "been",
        "were",
        "they",
        "them",
        "then",
        "than",
        "also",
    }

    frequency = {}

    for word in words:

        if word in stop_words:
            continue

        frequency[word] = (
            frequency.get(word, 0) + 1
        )

    keywords = [
        word
        for word, count in sorted(
            frequency.items(),
            key=lambda item: item[1],
            reverse=True
        )[:8]
    ]

    # --------------------------------------------------------
    # Basic summary
    # --------------------------------------------------------

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text
    )

    sentences = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]

    summary_sentences = sentences[:3]

    summary = " ".join(
        summary_sentences
    )

    if not summary:

        summary = (
            "The scanned document contains "
            f"{len(words)} words."
        )

    # --------------------------------------------------------
    # Action items
    # --------------------------------------------------------

    action_items = []

    action_patterns = [
        r"(?i)\bplease\s+([^.!\n]+)",
        r"(?i)\bmust\s+([^.!\n]+)",
        r"(?i)\brequired\s+to\s+([^.!\n]+)",
        r"(?i)\bneed\s+to\s+([^.!\n]+)",
        r"(?i)\bsubmit\s+([^.!\n]+)",
    ]

    for pattern in action_patterns:

        matches = re.findall(
            pattern,
            text
        )

        for match in matches:

            clean = (
                match.strip()
                .rstrip(".")
            )

            if clean and clean not in action_items:
                action_items.append(clean)

    return {
        "provider": "local",
        "document_type": best_category,
        "confidence": (
            "high"
            if best_score >= 3
            else "medium"
            if best_score >= 1
            else "low"
        ),
        "summary": summary,
        "key_information": {
            "dates": dates[:10],
            "emails": list(
                dict.fromkeys(emails)
            )[:10],
            "phone_numbers": list(
                dict.fromkeys(phones)
            )[:10],
            "amounts": list(
                dict.fromkeys(amounts)
            )[:10],
        },
        "action_items": action_items[:8],
        "keywords": keywords,
    }


# ============================================================
# OPENAI AI ANALYSIS
# ============================================================

def openai_ai_analysis(
    text: str
) -> Optional[Dict[str, Any]]:

    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        return None

    text = text[:120000]

    prompt = f"""
You are an intelligent document analysis assistant.

Analyze the OCR text below.

Return ONLY valid JSON with this exact structure:

{{
  "document_type": "string",
  "confidence": "high|medium|low",
  "summary": "string",
  "key_information": {{
    "dates": [],
    "emails": [],
    "phone_numbers": [],
    "amounts": [],
    "people": [],
    "organizations": []
  }},
  "action_items": [],
  "keywords": []
}}

Rules:

1. Do not invent information.
2. Only extract information actually present.
3. Keep the summary concise.
4. Identify the likely document type.
5. Extract important dates, contact details,
   people, organizations and monetary amounts.
6. Identify explicit action items.
7. Return JSON only.

OCR TEXT:

{text}
"""

    try:

        import requests

        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization":
                    f"Bearer {api_key}",
                "Content-Type":
                    "application/json",
            },
            json={
                "model": os.getenv(
                    "SCAN_AI_MODEL",
                    "gpt-5.6-luna"
                ),
                "input": prompt,
            },
            timeout=60,
        )

        response.raise_for_status()

        data = response.json()

        output_text = extract_openai_text(
            data
        )

        if not output_text:
            return None

        # Remove markdown code fences if present
        output_text = re.sub(
            r"^```(?:json)?\s*",
            "",
            output_text.strip(),
            flags=re.IGNORECASE
        )

        output_text = re.sub(
            r"\s*```$",
            "",
            output_text.strip()
        )

        parsed = json.loads(
            output_text
        )

        parsed["provider"] = "openai"

        return parsed

    except Exception as exc:

        print(
            "OPENAI SCAN AI ERROR:",
            repr(exc)
        )

        return None


def extract_openai_text(
    data: Dict[str, Any]
) -> str:

    # Responses API SDK-like output may expose
    # output_text, but raw HTTP responses can vary.
    if isinstance(
        data.get("output_text"),
        str
    ):
        return data["output_text"]

    output = data.get(
        "output",
        []
    )

    chunks = []

    for item in output:

        if not isinstance(
            item,
            dict
        ):
            continue

        content = item.get(
            "content",
            []
        )

        for part in content:

            if not isinstance(
                part,
                dict
            ):
                continue

            text = part.get(
                "text"
            )

            if isinstance(
                text,
                str
            ):
                chunks.append(text)

    return "\n".join(
        chunks
    ).strip()


# ============================================================
# PUBLIC AI FUNCTION
# ============================================================

def analyze_document_with_ai(
    text: str
) -> Dict[str, Any]:

    text = clean_ocr_text(
        text or ""
    )

    if not text:

        return {
            "provider": "local",
            "document_type": "Unknown",
            "confidence": "low",
            "summary": (
                "No readable text was detected."
            ),
            "key_information": {},
            "action_items": [],
            "keywords": [],
        }

    # Try external AI first
    ai_result = openai_ai_analysis(
        text
    )

    if ai_result:
        return ai_result

    # Reliable offline fallback
    return local_ai_analysis(
        text
    )


# ============================================================
# COMPLETE SCAN PIPELINE
# ============================================================

def scan_document(
    input_path: str,
    language: str = "eng",
    output_directory: Optional[str] = None,
    progress_callback=None
) -> Dict[str, Any]:

    input_path = Path(
        input_path
    )

    if not input_path.is_file():
        raise FileNotFoundError(
            "Document was not found."
        )

    extension = (
        input_path.suffix.lower()
    )

    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(
            "Unsupported document format."
        )

    if output_directory is None:

        output_directory = (
            input_path.parent
        )

    output_directory = Path(
        output_directory
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True
    )

    stem = safe_stem(
        input_path.name
    )

    searchable_pdf = (
        output_directory /
        f"{stem}-scanned.pdf"
    )

    text_file = (
        output_directory /
        f"{stem}-ocr.txt"
    )

    # --------------------------------------------------------
    # PDF
    # --------------------------------------------------------

    if extension == ".pdf":

        result = scan_pdf(
            str(input_path),
            language=language,
            progress_callback=progress_callback
        )

        create_searchable_pdf_from_file(
            str(input_path),
            result["pages"],
            str(searchable_pdf)
        )

    # --------------------------------------------------------
    # Image
    # --------------------------------------------------------

    else:

        image = load_image(
            str(input_path)
        )

        enhanced = enhance_document_image(
            image
        )

        ocr_result = perform_ocr(
            enhanced,
            language
        )

        result = {
            "text": ocr_result["text"],
            "pages": [
                {
                    "page": 1,
                    "text": ocr_result["text"],
                    "word_count": ocr_result[
                        "word_count"
                    ],
                }
            ],
            "page_count": 1,
            "word_count": ocr_result[
                "word_count"
            ],
        }

        create_searchable_pdf_from_images(
            [enhanced],
            [ocr_result["text"]],
            str(searchable_pdf)
        )

    # --------------------------------------------------------
    # Save TXT
    # --------------------------------------------------------

    save_text_file(
        result["text"],
        str(text_file)
    )

    # --------------------------------------------------------
    # AI
    # --------------------------------------------------------

    ai_result = analyze_document_with_ai(
        result["text"]
    )

    return {
        **result,
        "searchable_pdf": str(
            searchable_pdf
        ),
        "text_file": str(
            text_file
        ),
        "ai": ai_result,
    }