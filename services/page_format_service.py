"""
Page Format service
--------------------
Rebuilds a PDF onto a new page size/layout: custom dimensions, margins,
gutter (binding margin), bleed + crop marks, N-up columns, rotation,
rounded borders, header/footer with page-number tokens, and a watermark.

Only the pages inside [page_start, page_end] are touched; every other
page is copied through untouched (as a vector page, not a raster copy),
so quality and file size stay good for documents that are only partly
reformatted.
"""
from pathlib import Path
import os
import time
import math
import fitz

BASE = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "temp")))
OUT = BASE / "page_format"
OUT.mkdir(parents=True, exist_ok=True)

# How long a generated/uploaded temp file is allowed to sit in OUT before
# a cleanup sweep removes it. Called opportunistically on each request.
MAX_AGE_SECONDS = 2 * 60 * 60

# Render-quality presets, expressed as a zoom factor (72 dpi baseline).
# "print" corresponds to roughly 300 dpi.
QUALITY_PRESETS = {"draft": 1.25, "standard": 2.0, "high": 3.0, "print": 4.17}

FONT_MAP = {
    "helvetica": "helv",
    "helvetica-bold": "hebo",
    "times": "tiro",
    "times-bold": "tibo",
    "courier": "cour",
    "courier-bold": "cobo",
}

MAX_PAGES = 3000


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------
def cleanup_old_files(max_age=MAX_AGE_SECONDS):
    """Best-effort removal of stale temp/output files. Never raises."""
    now = time.time()
    try:
        for p in OUT.iterdir():
            try:
                if p.is_file() and now - p.stat().st_mtime > max_age:
                    p.unlink()
            except OSError:
                continue
    except OSError:
        pass


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def rgb(hexv, default=(1, 1, 1)):
    s = str(hexv or "").lstrip("#")
    if len(s) != 6:
        return default
    try:
        return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return default


def mm_to_pt(v):
    return float(v or 0) * 72 / 25.4


def font_code(name):
    return FONT_MAP.get(str(name or "helvetica").lower(), "helv")


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
def page_size(settings):
    """Final output page rect, including bleed on every side."""
    bleed = mm_to_pt(settings.get("bleed", 0))
    w = mm_to_pt(settings["width_mm"]) + bleed * 2
    h = mm_to_pt(settings["height_mm"]) + bleed * 2
    if settings.get("orientation") == "landscape" and w < h:
        w, h = h, w
    if settings.get("orientation") == "portrait" and w > h:
        w, h = h, w
    return fitz.Rect(0, 0, w, h)


def content_area(target, settings, page_number):
    """
    The usable area inside the trimmed page (bleed excluded, margins
    applied). Gutter adds extra room on the binding side; when
    "mirror_margins" is on, the binding side alternates with page parity
    so facing pages mirror each other (like a printed book).
    """
    bleed = mm_to_pt(settings.get("bleed", 0))
    m = settings.get("margins", {})
    ml = mm_to_pt(m.get("ml", 0))
    mr = mm_to_pt(m.get("mr", 0))
    mt = mm_to_pt(m.get("mt", 0))
    mb = mm_to_pt(m.get("mb", 0))
    gutter = mm_to_pt(settings.get("gutter", 0))

    if gutter:
        is_odd = page_number % 2 == 1
        binds_left = is_odd if settings.get("mirror_margins") else True
        if binds_left:
            ml += gutter
        else:
            mr += gutter

    area = fitz.Rect(
        bleed + ml,
        bleed + mt,
        target.width - bleed - mr,
        target.height - bleed - mb,
    )
    if area.width <= 2 or area.height <= 2:
        raise ValueError("Margins (plus gutter/bleed) leave no usable page area.")
    return area


def fit_rect(src_rect, area, fit, center):
    """Scale/position src_rect's size inside `area` per the fit mode."""
    sw, sh = src_rect.width, src_rect.height
    if fit == "contain":
        scale = min(area.width / sw, area.height / sh)
        rw, rh = sw * scale, sh * scale
    elif fit == "cover":
        scale = max(area.width / sw, area.height / sh)
        rw, rh = sw * scale, sh * scale
    elif fit == "stretch":
        rw, rh = area.width, area.height
    else:  # "original" - no scaling
        rw, rh = sw, sh

    if center:
        x = area.x0 + (area.width - rw) / 2
        y = area.y0 + (area.height - rh) / 2
    else:
        x, y = area.x0, area.y0
    return fitz.Rect(x, y, x + rw, y + rh)


def cover_pixmap(sp, cell, quality):
    """
    Render `sp` cropped to the same aspect ratio as `cell`, so it can be
    inserted straight into `cell` with no overflow into neighbouring
    columns - the "cover" equivalent of fit_rect, done at render time
    since PyMuPDF's insert_image() has no clip-to-rect option.
    """
    sw, sh = sp.rect.width, sp.rect.height
    cell_ratio = cell.width / cell.height
    src_ratio = sw / sh
    if src_ratio > cell_ratio:
        new_w = sh * cell_ratio
        x0 = sp.rect.x0 + (sw - new_w) / 2
        clip = fitz.Rect(x0, sp.rect.y0, x0 + new_w, sp.rect.y1)
    else:
        new_h = sw / cell_ratio
        y0 = sp.rect.y0 + (sh - new_h) / 2
        clip = fitz.Rect(sp.rect.x0, y0, sp.rect.x1, y0 + new_h)
    return sp.get_pixmap(matrix=fitz.Matrix(quality, quality), clip=clip, alpha=False)


def split_columns(area, columns, gap_mm):
    """Divide `area` into `columns` equal-width side-by-side cells."""
    columns = max(1, int(columns or 1))
    if columns == 1:
        return [area]
    gap = mm_to_pt(gap_mm)
    total_gap = gap * (columns - 1)
    cell_w = (area.width - total_gap) / columns
    if cell_w <= 2:
        raise ValueError("Too many columns (or too much column gap) for the page width.")
    cells = []
    x = area.x0
    for _ in range(columns):
        cells.append(fitz.Rect(x, area.y0, x + cell_w, area.y1))
        x += cell_w + gap
    return cells


# --------------------------------------------------------------------------
# Decorative extras
# --------------------------------------------------------------------------
def draw_crop_marks(dp, target, bleed_pt, length=18, gap=6):
    if bleed_pt <= 0:
        return
    w, h = target.width, target.height
    shape = dp.new_shape()
    marks = [
        # (x1,y1)-(x2,y2) pairs near each corner, pointing at the trim line
        ((bleed_pt, bleed_pt - gap), (bleed_pt, bleed_pt - gap - length)),
        ((bleed_pt - gap, bleed_pt), (bleed_pt - gap - length, bleed_pt)),
        ((w - bleed_pt, bleed_pt - gap), (w - bleed_pt, bleed_pt - gap - length)),
        ((w - bleed_pt + gap, bleed_pt), (w - bleed_pt + gap + length, bleed_pt)),
        ((bleed_pt, h - bleed_pt + gap), (bleed_pt, h - bleed_pt + gap + length)),
        ((bleed_pt - gap, h - bleed_pt), (bleed_pt - gap - length, h - bleed_pt)),
        ((w - bleed_pt, h - bleed_pt + gap), (w - bleed_pt, h - bleed_pt + gap + length)),
        ((w - bleed_pt + gap, h - bleed_pt), (w - bleed_pt + gap + length, h - bleed_pt)),
    ]
    for p1, p2 in marks:
        shape.draw_line(fitz.Point(*p1), fitz.Point(*p2))
    shape.finish(color=(0, 0, 0), width=0.6)
    shape.commit()


def draw_border(dp, rect, width_pt, color, radius_frac):
    if width_pt <= 0:
        return
    shape = dp.new_shape()
    inset = rect + (width_pt / 2, width_pt / 2, -width_pt / 2, -width_pt / 2)
    try:
        shape.draw_rect(inset, radius=clamp(radius_frac, 0, 0.5))
    except TypeError:
        # Older PyMuPDF without the `radius` kwarg - fall back to a square corner.
        shape.draw_rect(inset)
    shape.finish(color=color, width=width_pt)
    shape.commit()


def draw_watermark(dp, settings):
    text = str(settings.get("watermark_text") or "").strip()
    if not text:
        return
    try:
        fs = clamp(float(settings.get("watermark_size", 48)), 6, 400)
        opacity = clamp(float(settings.get("watermark_opacity", 0.15)), 0.01, 1)
        angle = float(settings.get("watermark_angle", 45))
        color = rgb(settings.get("watermark_color", "#808080"), default=(0.5, 0.5, 0.5))
        font = font_code(settings.get("watermark_font"))
        rad = math.radians(angle)
        mtx = fitz.Matrix(math.cos(rad), -math.sin(rad), math.sin(rad), math.cos(rad), 0, 0)
        tw = fitz.get_text_length(text, fontname=font, fontsize=fs)

        def place(cx, cy):
            pt = fitz.Point(cx - tw / 2, cy)
            dp.insert_text(
                pt, text, fontsize=fs, fontname=font, color=color,
                fill_opacity=opacity, morph=(fitz.Point(cx, cy), mtx),
            )

        if settings.get("watermark_tile"):
            step_x = max(tw * 1.4, fs * 4)
            step_y = fs * 4
            y = step_y / 2
            row = 0
            while y < dp.rect.height + step_y:
                offset = (step_x / 2) if row % 2 else 0
                x = offset
                while x < dp.rect.width + step_x:
                    place(x, y)
                    x += step_x
                y += step_y
                row += 1
        else:
            place(dp.rect.width / 2, dp.rect.height / 2)
    except Exception:
        # A bad font/value in the watermark should never fail the whole job.
        pass


def resolve_text(template, page_number, total_pages):
    return (
        str(template or "")
        .replace("{page}", str(page_number))
        .replace("{total}", str(total_pages))
    )


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def format_pdf(input_path, settings):
    cleanup_old_files()

    src = fitz.open(str(input_path))
    total = len(src)
    if total == 0:
        src.close()
        raise ValueError("The PDF has no pages.")
    if total > MAX_PAGES:
        src.close()
        raise ValueError(f"PDF has too many pages (limit is {MAX_PAGES}).")

    start = clamp(int(settings.get("page_start", 1)), 1, total)
    end = settings.get("page_end")
    end = clamp(int(end), 1, total) if end else total
    if start > end:
        src.close()
        raise ValueError("Invalid page range.")

    page_filter = settings.get("page_filter", "all")  # all | odd | even
    quality = QUALITY_PRESETS.get(settings.get("quality", "standard"), QUALITY_PRESETS["standard"])
    rotation = int(settings.get("rotation", 0)) % 360
    if rotation not in (0, 90, 180, 270):
        rotation = 0

    fit = settings.get("fit", "contain")
    center = bool(settings.get("center_content", True))
    columns = max(1, int(settings.get("columns", 1)))
    column_gap = float(settings.get("column_gap", 0))
    bleed_pt = mm_to_pt(settings.get("bleed", 0))
    bg = rgb(settings.get("background", "#ffffff"))
    border_color = rgb(settings.get("border_color", "#000000"), default=(0, 0, 0))
    border_width_pt = mm_to_pt(settings.get("border_width", 0))
    border_radius = float(settings.get("border_radius", 0))

    out = fitz.open()
    target = page_size(settings)

    def in_range(n):
        if not (start <= n <= end):
            return False
        if page_filter == "odd":
            return n % 2 == 1
        if page_filter == "even":
            return n % 2 == 0
        return True

    for i in range(total):
        page_number = i + 1
        if not in_range(page_number):
            out.insert_pdf(src, from_page=i, to_page=i)
            continue

        sp = src[i]
        if rotation:
            sp.set_rotation((sp.rotation + rotation) % 360)

        dp = out.new_page(width=target.width, height=target.height)

        # Background (fills bleed area too).
        if bg != (1, 1, 1):
            shape = dp.new_shape()
            shape.draw_rect(dp.rect)
            shape.finish(fill=bg, width=0)
            shape.commit()

        area = content_area(target, settings, page_number)

        # insert_image() has no "clip" argument, so for "cover" - where the
        # scaled page is deliberately larger than its cell on one axis - we
        # crop the *source* page to the cell's aspect ratio before
        # rendering, rather than trying to clip the placed image. For every
        # other fit mode the placed rect never exceeds the cell, so a single
        # whole-page render can be reused for every column.
        whole_pix = None
        if fit != "cover":
            whole_pix = sp.get_pixmap(matrix=fitz.Matrix(quality, quality), alpha=False)

        for cell in split_columns(area, columns, column_gap):
            if fit == "cover":
                dp.insert_image(cell, pixmap=cover_pixmap(sp, cell, quality), overlay=True)
            else:
                rect = fit_rect(sp.rect, cell, fit, center)
                dp.insert_image(rect, pixmap=whole_pix, overlay=True)

        draw_watermark(dp, settings)
        draw_crop_marks(dp, target, bleed_pt)
        if border_width_pt > 0:
            trim = fitz.Rect(bleed_pt, bleed_pt, target.width - bleed_pt, target.height - bleed_pt)
            draw_border(dp, trim, border_width_pt, border_color, border_radius)

    # Header/footer/page-numbers pass, done after every page exists so
    # "{total}" can refer to the final output page count.
    out_total = len(out)
    for idx, dp in enumerate(out):
        page_number = idx + 1
        if not in_range(page_number):
            continue

        m = settings.get("margins", {})
        mt = mm_to_pt(m.get("mt", 0)) + bleed_pt
        mb = mm_to_pt(m.get("mb", 0)) + bleed_pt

        header = resolve_text(settings.get("header", ""), page_number, out_total)
        footer = resolve_text(settings.get("footer", ""), page_number, out_total)
        if settings.get("page_numbers"):
            fmt = settings.get("page_number_format") or "Page {page} of {total}"
            numbering = resolve_text(fmt, page_number, out_total)
            footer = f"{footer}  •  {numbering}" if footer else numbering

        text_color = rgb(settings.get("text_color", "#404852"), default=(0.25, 0.28, 0.32))
        font = font_code(settings.get("header_footer_font"))
        align_map = {"left": 0, "center": 1, "right": 2}
        h_align = align_map.get(settings.get("header_align", "center"), 1)
        f_align = align_map.get(settings.get("footer_align", "center"), 1)

        if header:
            dp.insert_textbox(
                fitz.Rect(bleed_pt + 20, bleed_pt + 3, dp.rect.width - bleed_pt - 20, max(bleed_pt + 25, mt - 2)),
                header, fontsize=9, fontname=font, color=text_color, align=h_align,
            )
        if footer:
            dp.insert_textbox(
                fitz.Rect(bleed_pt + 20, dp.rect.height - max(bleed_pt + 25, mb - 2), dp.rect.width - bleed_pt - 20, dp.rect.height - bleed_pt - 3),
                footer, fontsize=9, fontname=font, color=text_color, align=f_align,
            )

    token = uuid_token()
    output = OUT / f"{token}.pdf"
    out.save(str(output), garbage=4, deflate=True, clean=True)
    page_count = len(out)
    out.close()
    src.close()
    return token, output, page_count


def uuid_token():
    import uuid
    return uuid.uuid4().hex