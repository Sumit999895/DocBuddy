# ============================================================
# DOCBUDDY - EDIT PDF MODULE (Pro feature)
#
# Architecture:
#   - /upload renders every page of the PDF to a PNG so the
#     browser can show a fast, fully client-side canvas editor
#     (text, drawing, shapes, highlights, whiteout, redaction,
#     stamps). Nothing is written to the PDF until export.
#   - /process receives the FINAL page order (including pages
#     from an optionally-merged second PDF, and blank pages) plus
#     every object placed on every page, in PDF point coordinates,
#     and bakes all of it into a brand new PDF in one pass using
#     PyMuPDF (fitz).
#
# Requires: pip install pymupdf
#
# Not included in this version (flagging honestly rather than
# pretending): form-field editing, OCR, digital signatures/
# encryption, table editing, bookmark/outline editing. The
# architecture below (object list -> single export pass) extends
# cleanly to those later if needed.
# ============================================================

import os
import io
import json
import uuid
import time
from datetime import datetime

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

import fitz  # PyMuPDF

from database import get_db_connection
import mysql.connector


# ============================================================
# BLUEPRINT
# ============================================================

edit_pdf_bp = Blueprint(
    "edit_pdf",
    __name__,
    url_prefix="/edit-pdf"
)


# ============================================================
# TEMPORARY DIRECTORY
# ============================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_DIR = os.path.join(BASE_DIR, "temp", "edit_pdf")
os.makedirs(TEMP_DIR, exist_ok=True)


# ============================================================
# SETTINGS
# ============================================================

ALLOWED_PDF_EXTENSIONS = {"pdf"}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

MAX_FILE_SIZE = 40 * 1024 * 1024

# Render scale for page preview images. 150 dpi-equivalent zoom
# (PDF points are 72 per inch) gives a crisp editing surface
# without producing huge PNGs.
RENDER_ZOOM = 150 / 72

FONT_MAP = {
    (False, False): "helv",
    (True, False): "hebo",
    (False, True): "heit",
    (True, True): "hebi",
}


# ============================================================
# LOGIN + PLAN CHECK
#
# This blueprint can't import get_user_plan() from app.py
# (that would create a circular import), so it duplicates the
# same lightweight plan lookup here. If you ever add more
# Pro-gated blueprints, consider moving this into its own
# plan_utils.py that both app.py and the blueprints import from.
# ============================================================

def logged_in():
    return "user" in session


def get_current_plan():
    """Reads the logged-in user's plan straight from the DB so an
    upgrade takes effect immediately, without needing a fresh login."""

    if not logged_in():
        return "free"

    user = session.get("user")
    if not isinstance(user, dict) or "id" not in user:
        return "free"

    db = None
    cursor = None
    plan = "free"

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            "SELECT plan, plan_expires_at FROM Users WHERE id = %s",
            (user["id"],)
        )
        row = cursor.fetchone()

        if row:
            plan = row.get("plan") or "free"
            expires_at = row.get("plan_expires_at")

            if plan != "free" and expires_at and expires_at < datetime.utcnow():
                plan = "free"

    except mysql.connector.Error as e:
        print("edit_pdf get_current_plan error:", e)

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    return plan


def require_pro_page():
    """For normal page routes: returns a redirect Response if access
    should be blocked, or None if the request may proceed."""

    if not logged_in():
        return redirect(url_for("login", next=request.path))

    if get_current_plan() != "pro":
        return redirect(url_for("upgrade"))

    return None


def require_pro_api():
    """For JSON API routes: returns a (response, status) tuple if
    access should be blocked, or None if the request may proceed."""

    if not logged_in():
        return jsonify({"success": False, "error": "Please log in first."}), 401

    if get_current_plan() != "pro":
        return jsonify({
            "success": False,
            "error": "Edit PDF is a Pro feature. Please upgrade your plan."
        }), 403

    return None


# ============================================================
# FILE HELPERS
# ============================================================

def allowed_pdf(filename):
    return bool(filename) and "." in filename and \
        filename.rsplit(".", 1)[1].lower() in ALLOWED_PDF_EXTENSIONS


def allowed_image(filename):
    return bool(filename) and "." in filename and \
        filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def pdf_path(file_id):
    return os.path.join(TEMP_DIR, f"{file_id}.pdf")


def page_image_path(file_id, page_index):
    return os.path.join(TEMP_DIR, f"{file_id}_page_{page_index}.png")


def stamp_path(stamp_id, extension):
    return os.path.join(TEMP_DIR, f"stamp_{stamp_id}.{extension}")


def find_stamp_file(stamp_id):
    for extension in ALLOWED_IMAGE_EXTENSIONS:
        path = stamp_path(stamp_id, extension)
        if os.path.isfile(path):
            return path
    return None


def cleanup_old_files():
    now = time.time()
    try:
        for filename in os.listdir(TEMP_DIR):
            path = os.path.join(TEMP_DIR, filename)
            if not os.path.isfile(path):
                continue
            try:
                if now - os.path.getmtime(path) > 3600:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def hex_to_rgb01(value, fallback=(0, 0, 0)):
    """Converts '#rrggbb' into the 0.0-1.0 float RGB tuples fitz expects."""

    if not value or not isinstance(value, str):
        return fallback

    value = value.strip().lstrip("#")

    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)

    if len(value) != 6:
        return fallback

    try:
        r = int(value[0:2], 16) / 255
        g = int(value[2:4], 16) / 255
        b = int(value[4:6], 16) / 255
        return (r, g, b)
    except ValueError:
        return fallback


def lighten_rgb01(rgb, amount):
    """Blends a color toward white by `amount` (0-1). Used to approximate
    a faint watermark, since arbitrary alpha-transparent text insertion
    needs extended graphics-state handling beyond this tool's scope."""

    amount = max(0, min(1, amount))
    return tuple(channel + (1 - channel) * amount for channel in rgb)


# ============================================================
# PAGE (Edit PDF landing page)
# ============================================================

@edit_pdf_bp.route("/")
def edit_pdf():

    redirect_response = require_pro_page()
    if redirect_response:
        return redirect_response

    cleanup_old_files()

    return render_template("edit_pdf.html", user=session.get("user"))


# ============================================================
# UPLOAD PDF + RENDER PAGE PREVIEWS
# ============================================================

@edit_pdf_bp.route("/upload", methods=["POST"])
def upload_pdf():

    blocked = require_pro_api()
    if blocked:
        return blocked

    if "document" not in request.files:
        return jsonify({"success": False, "error": "No PDF was selected."}), 400

    uploaded = request.files["document"]

    if not uploaded.filename:
        return jsonify({"success": False, "error": "Please select a PDF."}), 400

    if not allowed_pdf(uploaded.filename):
        return jsonify({"success": False, "error": "Only PDF files are supported."}), 400

    # Enforce the 40 MB limit here as well as in the frontend.
    uploaded.stream.seek(0, os.SEEK_END)
    upload_size = uploaded.stream.tell()
    uploaded.stream.seek(0)
    if upload_size > MAX_FILE_SIZE:
        return jsonify({
            "success": False,
            "error": "PDF is too large. Maximum allowed size is 40 MB."
        }), 413

    file_id = uuid.uuid4().hex
    saved_path = pdf_path(file_id)

    try:
        uploaded.save(saved_path)

        doc = fitz.open(saved_path)

        if doc.needs_pass:
            doc.close()
            os.remove(saved_path)
            return jsonify({
                "success": False,
                "error": "Password-protected PDFs are not supported. Please unlock the PDF first."
            }), 400

        if doc.page_count == 0:
            doc.close()
            os.remove(saved_path)
            return jsonify({"success": False, "error": "This PDF has no pages."}), 400

        pages = []

        for index in range(doc.page_count):
            page = doc.load_page(index)

            pixmap = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM))
            pixmap.save(page_image_path(file_id, index))

            pages.append({
                "index": index,
                "width_pt": page.rect.width,
                "height_pt": page.rect.height,
                "width_px": pixmap.width,
                "height_px": pixmap.height,
                "image_url": url_for(
                    "edit_pdf.page_image",
                    file_id=file_id,
                    page_index=index
                )
            })

        page_count = doc.page_count
        doc.close()

        return jsonify({
            "success": True,
            "file_id": file_id,
            "filename": uploaded.filename,
            "page_count": page_count,
            "pages": pages
        })

    except Exception as error:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass

        return jsonify({
            "success": False,
            "error": f"Could not read that PDF: {error}"
        }), 500


# ============================================================
# SERVE A RENDERED PAGE IMAGE
# ============================================================

@edit_pdf_bp.route("/page-image/<file_id>/<int:page_index>")
def page_image(file_id, page_index):

    if not logged_in():
        return "", 401

    path = page_image_path(file_id, page_index)

    if not os.path.isfile(path):
        return "", 404

    return send_file(path, mimetype="image/png")


# ============================================================
# UPLOAD A STAMP / SIGNATURE IMAGE
# ============================================================

@edit_pdf_bp.route("/upload-stamp", methods=["POST"])
def upload_stamp():

    blocked = require_pro_api()
    if blocked:
        return blocked

    if "image" not in request.files:
        return jsonify({"success": False, "error": "No image was selected."}), 400

    uploaded = request.files["image"]

    if not uploaded.filename or not allowed_image(uploaded.filename):
        return jsonify({
            "success": False,
            "error": "Use a JPG, PNG or WEBP image for stamps."
        }), 400

    stamp_id = uuid.uuid4().hex
    extension = uploaded.filename.rsplit(".", 1)[1].lower()

    if extension == "jpeg":
        extension = "jpg"

    saved_path = stamp_path(stamp_id, extension)

    try:
        uploaded.save(saved_path)

        # fitz.Pixmap can read a raster image file directly to get its
        # dimensions, without needing to treat it as a "document".
        pixmap = fitz.Pixmap(saved_path)
        width, height = pixmap.width, pixmap.height
        pixmap = None

        return jsonify({
            "success": True,
            "stamp_id": stamp_id,
            "width": width,
            "height": height,
            "preview_url": url_for("edit_pdf.stamp_image", stamp_id=stamp_id)
        })

    except Exception as error:
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except OSError:
                pass

        return jsonify({"success": False, "error": str(error)}), 500


@edit_pdf_bp.route("/stamp-image/<stamp_id>")
def stamp_image(stamp_id):

    if not logged_in():
        return "", 401

    path = find_stamp_file(stamp_id)

    if not path:
        return "", 404

    return send_file(path)


# ============================================================
# APPLY ONE PAGE'S EDIT OBJECTS
# ============================================================

def apply_objects_to_page(page, objects, skip_redaction=False):

    for obj in objects or []:

        obj_type = obj.get("type")

        try:
            if obj_type == "text":
                _apply_text(page, obj)

            elif obj_type == "edit_text":
                _apply_edit_text(page, obj)

            elif obj_type == "draw":
                _apply_draw(page, obj)

            elif obj_type == "rect":
                _apply_rect(page, obj)

            elif obj_type == "ellipse":
                _apply_ellipse(page, obj)

            elif obj_type == "line":
                _apply_line(page, obj)

            elif obj_type == "highlight":
                _apply_highlight(page, obj)

            elif obj_type == "whiteout":
                _apply_whiteout(page, obj)

            elif obj_type == "redact":
                if not skip_redaction:
                    _apply_redact(page, obj)

            elif obj_type == "stamp":
                _apply_stamp(page, obj)

            elif obj_type == "note":
                _apply_note(page, obj)

        except Exception as error:
            # A single bad object shouldn't sink the whole export -
            # skip it and keep going.
            print("edit_pdf: skipped a", obj_type, "object:", error)


def _safe_fontname(obj):
    requested = str(obj.get("font_family") or "").lower()
    if requested in {"helv", "tiro", "cour"}:
        return requested
    return FONT_MAP.get((bool(obj.get("bold")), bool(obj.get("italic"))), "helv")


def _apply_text(page, obj):
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    color = hex_to_rgb01(obj.get("color"), (0, 0, 0))
    page.insert_textbox(
        rect, obj.get("text", ""),
        fontsize=float(obj.get("font_size", 14)),
        color=color, fontname=_safe_fontname(obj),
        align=int(obj.get("align", 0)), overlay=True
    )


def _apply_edit_text(page, obj):
    # Called after redactions have already been applied.
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    page.insert_textbox(
        rect, obj.get("text", ""),
        fontsize=float(obj.get("font_size", 11)),
        color=hex_to_rgb01(obj.get("color"), (0, 0, 0)),
        fontname=_safe_fontname(obj),
        align=int(obj.get("align", 0)), overlay=True
    )


def _queue_edit_redaction(page, obj):
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    page.add_redact_annot(rect, fill=(1, 1, 1))


def _apply_draw(page, obj):
    points = [fitz.Point(p[0], p[1]) for p in obj.get("points", [])]

    if len(points) < 2:
        return

    color = hex_to_rgb01(obj.get("color"), (0, 0, 0))
    width = float(obj.get("width", 2))

    page.draw_polyline(
        points,
        color=color,
        width=width,
        closePath=False,
        stroke_opacity=_safe_number(obj.get("opacity", 1), 1, 0, 1)
    )


def _apply_rect(page, obj):
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    color = hex_to_rgb01(obj.get("color"), (0, 0, 0))
    fill = hex_to_rgb01(obj.get("fill")) if obj.get("fill") else None

    page.draw_rect(
        rect,
        color=color,
        fill=fill,
        width=float(obj.get("stroke_width", 2)),
        fill_opacity=float(obj.get("opacity", 1)),
        stroke_opacity=float(obj.get("opacity", 1))
    )


def _apply_ellipse(page, obj):
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    color = hex_to_rgb01(obj.get("color"), (0, 0, 0))
    fill = hex_to_rgb01(obj.get("fill")) if obj.get("fill") else None

    page.draw_oval(
        rect,
        color=color,
        fill=fill,
        width=float(obj.get("stroke_width", 2)),
        fill_opacity=float(obj.get("opacity", 1)),
        stroke_opacity=float(obj.get("opacity", 1))
    )


def _apply_line(page, obj):
    p1 = fitz.Point(obj["x1"], obj["y1"])
    p2 = fitz.Point(obj["x2"], obj["y2"])
    color = hex_to_rgb01(obj.get("color"), (0, 0, 0))
    width = float(obj.get("width", 2))

    page.draw_line(p1, p2, color=color, width=width)

    if obj.get("arrow"):
        _draw_arrowhead(page, p1, p2, color, width)


def _draw_arrowhead(page, p1, p2, color, width):
    import math

    angle = math.atan2(p2.y - p1.y, p2.x - p1.x)
    head_length = max(8, width * 4)
    spread = math.radians(28)

    left = fitz.Point(
        p2.x - head_length * math.cos(angle - spread),
        p2.y - head_length * math.sin(angle - spread)
    )
    right = fitz.Point(
        p2.x - head_length * math.cos(angle + spread),
        p2.y - head_length * math.sin(angle + spread)
    )

    page.draw_polyline([left, p2, right], color=color, width=width)


def _apply_highlight(page, obj):
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    color = hex_to_rgb01(obj.get("color"), (1, 0.92, 0.3))

    annot = page.add_highlight_annot(rect)
    annot.set_colors(stroke=color)
    annot.update()


def _apply_whiteout(page, obj):
    """Purely visual cover - draws an opaque box. Underlying text is
    NOT removed and remains selectable/extractable. Use 'redact' for
    a permanent removal."""

    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    color = hex_to_rgb01(obj.get("color"), (1, 1, 1))

    page.draw_rect(rect, color=color, fill=color, width=0)


def _apply_redact(page, obj):
    """Permanently removes text/graphics under the box, then fills it.
    This is real PDF redaction (apply_redactions), not just a visual
    cover."""

    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    color = hex_to_rgb01(obj.get("color"), (0, 0, 0))

    page.add_redact_annot(rect, fill=color)


def _apply_stamp(page, obj):
    path = find_stamp_file(obj.get("stamp_id", ""))
    if not path:
        return
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    page.insert_image(rect, filename=path, keep_proportion=True)


def _apply_note(page, obj):
    rect = fitz.Rect(obj["x"], obj["y"], obj["x"] + obj["width"], obj["y"] + obj["height"])
    fill = hex_to_rgb01(obj.get("fill"), (1, 0.95, 0.5))
    color = hex_to_rgb01(obj.get("color"), (0, 0, 0))
    page.draw_rect(rect, color=(0.75, 0.65, 0.15), fill=fill, width=0.7)
    page.insert_textbox(
        rect + (6, 5, -6, -5), obj.get("text", ""),
        fontsize=float(obj.get("font_size", 12)), color=color, fontname="helv"
    )


# ============================================================
# WATERMARK + PAGE NUMBERS (applied across every final page)
# ============================================================

def apply_watermark(page, settings):
    if not settings or not settings.get("enabled"):
        return

    text = (settings.get("text") or "").strip()
    if not text:
        return

    opacity = float(settings.get("opacity", 0.3))
    fontsize = float(settings.get("font_size", 48))
    base_color = hex_to_rgb01(settings.get("color"), (0.5, 0.5, 0.5))
    color = lighten_rgb01(base_color, 1 - opacity)

    center = fitz.Point(page.rect.width / 2, page.rect.height / 2)
    matrix = fitz.Matrix(1, 1).prerotate(45)

    shape = page.new_shape()
    shape.insert_text(
        center,
        text,
        fontsize=fontsize,
        color=color,
        fontname="helv",
        morph=(center, matrix)
    )
    shape.commit()


def apply_page_numbers(page, page_number, total_pages, settings):
    if not settings or not settings.get("enabled"):
        return

    fmt = settings.get("format") or "Page {n} of {total}"
    label = fmt.replace("{n}", str(page_number)).replace("{total}", str(total_pages))

    fontsize = float(settings.get("font_size", 10))
    color = hex_to_rgb01(settings.get("color"), (0, 0, 0))
    position = settings.get("position", "bottom-center")

    margin = 24
    width = page.rect.width
    height = page.rect.height

    text_width = fitz.get_text_length(label, fontname="helv", fontsize=fontsize)

    positions = {
        "bottom-center": fitz.Point((width - text_width) / 2, height - margin),
        "bottom-right": fitz.Point(width - text_width - margin, height - margin),
        "bottom-left": fitz.Point(margin, height - margin),
        "top-center": fitz.Point((width - text_width) / 2, margin),
        "top-right": fitz.Point(width - text_width - margin, margin),
        "top-left": fitz.Point(margin, margin),
    }

    point = positions.get(position, positions["bottom-center"])

    page.insert_text(point, label, fontsize=fontsize, color=color, fontname="helv")


# ============================================================
# TEXT DETECTION / SEARCH
# ============================================================

def _rgb_hex_from_int(value):
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        value = 0
    return "#{:06x}".format(value & 0xFFFFFF)


def _font_flags(flags):
    flags = int(flags or 0)
    return bool(flags & 16), bool(flags & 2)


@edit_pdf_bp.route("/analyze-text")
def analyze_text():
    blocked = require_pro_api()
    if blocked:
        return blocked
    file_id = request.args.get("file_id", "")
    path = pdf_path(file_id)
    if not os.path.isfile(path):
        return jsonify({"success": False, "error": "Source PDF was not found."}), 404
    doc = None
    try:
        doc = fitz.open(path)
        pages = {}
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            data = page.get_text("dict", sort=True)
            # For scanned/image-only pages, optionally fall back to PyMuPDF OCR.
            # Tesseract must be installed on the machine for this path.
            if not any(
                block.get("type") == 0 and any(
                    (s.get("text") or "").strip()
                    for line in block.get("lines", [])
                    for s in line.get("spans", [])
                )
                for block in data.get("blocks", [])
            ):
                try:
                    textpage = page.get_textpage_ocr(language="eng", dpi=150, full=True)
                    data = page.get_text("dict", textpage=textpage, sort=True)
                except Exception:
                    pass
            spans = []
            span_id = 0
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = (span.get("text") or "").strip()
                        if not text:
                            continue
                        x0, y0, x1, y1 = span.get("bbox", (0,0,0,0))
                        bold, italic = _font_flags(span.get("flags", 0))
                        spans.append({
                            "id": span_id, "text": text,
                            "x": float(x0), "y": float(y0),
                            "width": float(max(1,x1-x0)), "height": float(max(1,y1-y0)),
                            "font": span.get("font") or "Unknown",
                            "font_size": float(span.get("size") or 11),
                            "color": _rgb_hex_from_int(span.get("color",0)),
                            "bold": bold, "italic": italic
                        })
                        span_id += 1
            pages[str(page_index)] = spans
        return jsonify({"success": True, "pages": pages})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if doc:
            doc.close()


@edit_pdf_bp.route("/search-text")
def search_text():
    blocked = require_pro_api()
    if blocked:
        return blocked
    file_id = request.args.get("file_id", "")
    query = (request.args.get("q") or "").strip().lower()
    if not query:
        return jsonify({"success": True, "results": []})
    path = pdf_path(file_id)
    if not os.path.isfile(path):
        return jsonify({"success": False, "error": "Source PDF was not found."}), 404
    doc = None
    try:
        doc = fitz.open(path)
        results = []
        for page_index in range(doc.page_count):
            text = doc.load_page(page_index).get_text("text") or ""
            if query in text.lower():
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                matches = [ln for ln in lines if query in ln.lower()]
                results.append({"page": page_index, "text": (matches[0] if matches else text[:180])[:180]})
        return jsonify({"success": True, "results": results})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 500
    finally:
        if doc:
            doc.close()


# ============================================================
# EXPORT / PROCESS
# ============================================================


def _safe_number(value, default=0.0, minimum=None, maximum=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _sanitize_object(obj):
    """Keep the export endpoint tolerant of malformed client objects."""
    if not isinstance(obj, dict):
        return None

    obj_type = obj.get("type")
    allowed = {
        "text", "edit_text", "draw", "rect", "ellipse", "line",
        "highlight", "whiteout", "redact", "stamp", "note"
    }
    if obj_type not in allowed:
        return None

    clean = dict(obj)

    for key in ("x", "y", "width", "height", "x1", "y1", "x2", "y2",
                "font_size", "stroke_width", "width"):
        if key in clean:
            clean[key] = _safe_number(clean[key], 0)

    if obj_type in {"text", "edit_text", "note"}:
        clean["text"] = str(clean.get("text") or "")[:10000]

    if obj_type == "draw":
        points = []
        for p in clean.get("points", [])[:10000]:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                points.append([
                    _safe_number(p[0]),
                    _safe_number(p[1])
                ])
        clean["points"] = points

    return clean


@edit_pdf_bp.route("/process", methods=["POST"])
def process():

    blocked = require_pro_api()
    if blocked:
        return blocked

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Invalid request."}), 400

    file_id = data.get("file_id")
    if not file_id:
        return jsonify({"success": False, "error": "Document ID is missing."}), 400

    primary_path = pdf_path(file_id)
    if not os.path.isfile(primary_path):
        return jsonify({"success": False, "error": "Source PDF was not found."}), 404

    merged_file_id = data.get("merged_file_id")
    merged_path = pdf_path(merged_file_id) if merged_file_id else None

    if merged_path and not os.path.isfile(merged_path):
        merged_path = None

    page_descriptors = data.get("pages", [])
    if not page_descriptors:
        return jsonify({"success": False, "error": "No pages to export."}), 400

    raw_objects_by_page = data.get("objects", {})
    objects_by_page = {}
    if isinstance(raw_objects_by_page, dict):
        for page_key, raw_objects in raw_objects_by_page.items():
            if not isinstance(raw_objects, list):
                continue
            objects_by_page[str(page_key)] = [
                clean for clean in (_sanitize_object(o) for o in raw_objects)
                if clean is not None
            ]

    watermark_settings = data.get("watermark")
    page_number_settings = data.get("page_numbers")

    primary_doc = None
    merged_doc = None
    out_doc = None

    try:
        primary_doc = fitz.open(primary_path)
        merged_doc = fitz.open(merged_path) if merged_path else None
        out_doc = fitz.open()

        for descriptor in page_descriptors:
            source = descriptor.get("source", "primary")

            if source == "primary":
                index = int(descriptor["index"])
                out_doc.insert_pdf(primary_doc, from_page=index, to_page=index)

            elif source == "merged" and merged_doc is not None:
                index = int(descriptor["index"])
                out_doc.insert_pdf(merged_doc, from_page=index, to_page=index)

            elif source == "blank":
                width = float(descriptor.get("width", 612))
                height = float(descriptor.get("height", 792))
                out_doc.new_page(width=width, height=height)

            else:
                continue

            new_page = out_doc[-1]

            rotation = int(descriptor.get("rotation", 0)) % 360
            if rotation:
                new_page.set_rotation(rotation)

        total_pages = out_doc.page_count

        for position in range(total_pages):
            page = out_doc[position]
            page_objects = objects_by_page.get(str(position), [])

            # Queue every permanent removal first. This guarantees that
            # replacement text is inserted only AFTER the old text is gone.
            for obj in page_objects:
                if obj.get("type") == "redact":
                    _apply_redact(page, obj)
                elif obj.get("type") == "edit_text":
                    _queue_edit_redaction(page, obj)

            if any(o.get("type") in {"redact", "edit_text"} for o in page_objects):
                page.apply_redactions()

            apply_objects_to_page(page, page_objects, skip_redaction=True)

            apply_watermark(page, watermark_settings)
            apply_page_numbers(page, position + 1, total_pages, page_number_settings)

        output_id = uuid.uuid4().hex
        output_path = pdf_path(output_id)

        out_doc.save(output_path, garbage=4, deflate=True)

        return jsonify({
            "success": True,
            "output_id": output_id,
            "page_count": total_pages,
            "download_url": url_for("edit_pdf.download", output_id=output_id)
        })

    except Exception as error:
        return jsonify({"success": False, "error": str(error)}), 500

    finally:
        if primary_doc:
            primary_doc.close()
        if merged_doc:
            merged_doc.close()
        if out_doc:
            out_doc.close()


# ============================================================
# DOWNLOAD
# ============================================================

@edit_pdf_bp.route("/download/<output_id>")
def download(output_id):

    if not logged_in():
        return redirect(url_for("login"))

    path = pdf_path(output_id)

    if not os.path.isfile(path):
        return "File not found.", 404

    return send_file(
        path,
        mimetype="application/pdf",
        as_attachment=(request.args.get("preview") != "1"),
        download_name="DocBuddy_Edited.pdf"
    )


# ============================================================
# DELETE TEMP SOURCE (called when the user starts over)
# ============================================================

@edit_pdf_bp.route("/delete/<file_id>", methods=["POST"])
def delete(file_id):

    if not logged_in():
        return jsonify({"success": False}), 401

    deleted = False

    main_path = pdf_path(file_id)
    if os.path.exists(main_path):
        try:
            os.remove(main_path)
            deleted = True
        except OSError:
            pass

    index = 0
    while True:
        page_path = page_image_path(file_id, index)
        if not os.path.exists(page_path):
            break
        try:
            os.remove(page_path)
        except OSError:
            pass
        index += 1

    return jsonify({"success": True, "deleted": deleted})