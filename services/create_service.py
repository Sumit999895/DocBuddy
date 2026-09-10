import os
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.text import WD_BREAK
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_LINE_SPACING
from docx.enum.text import WD_COLOR_INDEX
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


# ============================================================
# PAGE SIZE
# ============================================================

PAGE_SIZES = {
    "A4": {
        "width": Inches(8.27),
        "height": Inches(11.69),
    },
    "Letter": {
        "width": Inches(8.5),
        "height": Inches(11),
    },
    "Legal": {
        "width": Inches(8.5),
        "height": Inches(14),
    },
}


# ============================================================
# MARGINS
# ============================================================

MARGIN_SIZES = {
    "normal": {
        "top": Inches(1.0),
        "bottom": Inches(1.0),
        "left": Inches(1.0),
        "right": Inches(1.0),
    },
    "narrow": {
        "top": Inches(0.5),
        "bottom": Inches(0.5),
        "left": Inches(0.5),
        "right": Inches(0.5),
    },
    "wide": {
        "top": Inches(1.25),
        "bottom": Inches(1.25),
        "left": Inches(1.5),
        "right": Inches(1.5),
    },
}


# ============================================================
# SAFE LIMITS
# ============================================================

MAX_HTML_LENGTH = 5_000_000
MAX_IMAGE_SIZE = 10 * 1024 * 1024


# ============================================================
# COLOR HELPERS
# ============================================================

def parse_color(value):
    """
    Convert common CSS colors to RGBColor.

    Supports:
    - #RRGGBB
    - #RGB
    - rgb(r,g,b)
    - named basic colors
    """

    if not value:
        return None

    value = str(value).strip().lower()

    named_colors = {
        "black": "000000",
        "white": "FFFFFF",
        "red": "FF0000",
        "green": "008000",
        "blue": "0000FF",
        "yellow": "FFFF00",
        "orange": "FFA500",
        "purple": "800080",
        "gray": "808080",
        "grey": "808080",
        "brown": "A52A2A",
        "pink": "FFC0CB",
        "navy": "000080",
        "teal": "008080",
    }

    if value in named_colors:
        value = named_colors[value]

    # #RGB
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = (
            "#"
            + value[1] * 2
            + value[2] * 2
            + value[3] * 2
        )

    # #RRGGBB
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        value = value[1:]

        return RGBColor(
            int(value[0:2], 16),
            int(value[2:4], 16),
            int(value[4:6], 16),
        )

    # rgb(...)
    match = re.fullmatch(
        r"rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
        value,
    )

    if match:
        return RGBColor(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )

    return None


# ============================================================
# CSS STYLE PARSER
# ============================================================

def parse_inline_style(tag):
    """
    Convert inline CSS into a dictionary.
    """

    styles = {}

    if not isinstance(tag, Tag):
        return styles

    style_text = tag.get("style", "")

    if not style_text:
        return styles

    for item in style_text.split(";"):
        if ":" not in item:
            continue

        key, value = item.split(":", 1)

        key = key.strip().lower()
        value = value.strip()

        if key and value:
            styles[key] = value

    return styles


# ============================================================
# FONT SIZE
# ============================================================

def css_font_size_to_pt(value, default=11):
    """
    Convert CSS font-size into points.
    """

    if not value:
        return default

    value = str(value).strip().lower()

    match = re.match(
        r"([0-9]+(?:\.[0-9]+)?)\s*(px|pt|em|rem)?",
        value,
    )

    if not match:
        return default

    number = float(match.group(1))
    unit = match.group(2)

    if unit == "px":
        return number * 0.75

    if unit in ("em", "rem"):
        return number * 11

    return number


# ============================================================
# FONT FAMILY
# ============================================================

def clean_font_family(value):
    if not value:
        return None

    value = str(value)

    first = value.split(",")[0].strip()

    first = first.strip("'\"")

    return first or None


# ============================================================
# TEXT EXTRACTION
# ============================================================

def get_text_from_node(node):
    if node is None:
        return ""

    return node.get_text(
        " ",
        strip=False,
    )


# ============================================================
# REMOVE UNSAFE / UNWANTED HTML
# ============================================================

def sanitize_html(html):
    """
    Clean HTML before converting it to DOCX.

    This is not intended to be a full HTML security sanitizer,
    but removes scripts, styles, forms and other dangerous tags.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    dangerous_tags = [
        "script",
        "iframe",
        "object",
        "embed",
        "form",
        "meta",
        "link",
        "style",
    ]

    for tag_name in dangerous_tags:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    return soup


# ============================================================
# RUN FORMATTING
# ============================================================

def apply_run_formatting(
    run,
    tag=None,
    inherited=None,
):
    """
    Apply HTML formatting to a python-docx run.
    """

    inherited = inherited or {}

    styles = {}

    if tag is not None:
        styles.update(inherited)
        styles.update(parse_inline_style(tag))
    else:
        styles.update(inherited)

    # --------------------------------------------------------
    # Bold
    # --------------------------------------------------------

    tag_name = tag.name.lower() if isinstance(tag, Tag) else ""

    if tag_name in ("strong", "b"):
        run.bold = True

    if styles.get("font-weight", "").lower() in (
        "bold",
        "700",
        "800",
        "900",
    ):
        run.bold = True

    # --------------------------------------------------------
    # Italic
    # --------------------------------------------------------

    if tag_name in ("em", "i"):
        run.italic = True

    if styles.get("font-style", "").lower() == "italic":
        run.italic = True

    # --------------------------------------------------------
    # Underline
    # --------------------------------------------------------

    if tag_name == "u":
        run.underline = True

    text_decoration = styles.get(
        "text-decoration",
        "",
    ).lower()

    if "underline" in text_decoration:
        run.underline = True

    # --------------------------------------------------------
    # Strike
    # --------------------------------------------------------

    if tag_name in ("s", "strike", "del"):
        run.font.strike = True

    if "line-through" in text_decoration:
        run.font.strike = True

    # --------------------------------------------------------
    # Font family
    # --------------------------------------------------------

    font_family = styles.get(
        "font-family"
    )

    font_family = clean_font_family(
        font_family
    )

    if font_family:
        run.font.name = font_family

        run._element.rPr.rFonts.set(
            qn("w:ascii"),
            font_family,
        )

        run._element.rPr.rFonts.set(
            qn("w:hAnsi"),
            font_family,
        )

    # --------------------------------------------------------
    # Font size
    # --------------------------------------------------------

    if "font-size" in styles:
        size = css_font_size_to_pt(
            styles["font-size"],
            11,
        )

        run.font.size = Pt(
            max(6, min(size, 96))
        )

    # --------------------------------------------------------
    # Text color
    # --------------------------------------------------------

    color = (
        styles.get("color")
        or styles.get("foreground-color")
    )

    rgb = parse_color(color)

    if rgb:
        run.font.color.rgb = rgb

    # --------------------------------------------------------
    # Highlight
    # --------------------------------------------------------

    background = (
        styles.get("background-color")
        or styles.get("background")
    )

    highlight = parse_color(background)

    if highlight:
        # DOCX highlight has a limited color palette.
        # Use yellow for light/highlight backgrounds.
        r = highlight[0]
        g = highlight[1]
        b = highlight[2]

        if (
            r > 220
            and g > 180
            and b < 180
        ):
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW

    return run


# ============================================================
# ADD TEXT NODE
# ============================================================

def add_text_run(
    paragraph,
    text,
    tag=None,
    inherited=None,
):
    """
    Add a text node as a formatted Word run.
    """

    if text is None:
        return None

    text = str(text)

    if not text:
        return None

    run = paragraph.add_run(text)

    apply_run_formatting(
        run,
        tag=tag,
        inherited=inherited,
    )

    return run


# ============================================================
# INLINE HTML → DOCX
# ============================================================

def add_inline_content(
    paragraph,
    node,
    inherited=None,
):
    """
    Recursively convert inline HTML content into DOCX runs.
    """

    inherited = inherited or {}

    if isinstance(node, NavigableString):
        add_text_run(
            paragraph,
            str(node),
            inherited=inherited,
        )

        return

    if not isinstance(node, Tag):
        return

    tag_name = node.name.lower()

    # --------------------------------------------------------
    # Ignore unsupported / dangerous content
    # --------------------------------------------------------

    if tag_name in (
        "script",
        "style",
        "iframe",
        "object",
        "embed",
    ):
        return

    # --------------------------------------------------------
    # BR
    # --------------------------------------------------------

    if tag_name == "br":
        paragraph.add_run().add_break()
        return

    # --------------------------------------------------------
    # Link
    # --------------------------------------------------------

    if tag_name == "a":
        text = node.get_text(
            " ",
            strip=False,
        )

        run = paragraph.add_run(
            text
        )

        apply_run_formatting(
            run,
            tag=node,
            inherited=inherited,
        )

        run.underline = True

        color = parse_color(
            "#1267B1"
        )

        if color:
            run.font.color.rgb = color

        return

    # --------------------------------------------------------
    # Inline style inheritance
    # --------------------------------------------------------

    current_styles = dict(inherited)

    current_styles.update(
        parse_inline_style(node)
    )

    # --------------------------------------------------------
    # Inline formatting tags
    # --------------------------------------------------------

    if tag_name in (
        "b",
        "strong",
    ):
        current_styles["font-weight"] = "bold"

    if tag_name in (
        "i",
        "em",
    ):
        current_styles["font-style"] = "italic"

    if tag_name == "u":
        current_styles["text-decoration"] = "underline"

    if tag_name in (
        "s",
        "strike",
        "del",
    ):
        current_styles["text-decoration"] = (
            "line-through"
        )

    # --------------------------------------------------------
    # Nested children
    # --------------------------------------------------------

    for child in node.children:

        if isinstance(
            child,
            NavigableString,
        ):

            add_text_run(
                paragraph,
                str(child),
                tag=node,
                inherited=current_styles,
            )

        elif isinstance(
            child,
            Tag,
        ):

            add_inline_content(
                paragraph,
                child,
                inherited=current_styles,
            )


# ============================================================
# PARAGRAPH ALIGNMENT
# ============================================================

def apply_paragraph_style(
    paragraph,
    tag,
):
    styles = parse_inline_style(tag)

    alignment = (
        styles.get("text-align")
        or styles.get("align")
    )

    if alignment:
        alignment = alignment.lower()

        mapping = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
            "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }

        if alignment in mapping:
            paragraph.alignment = mapping[
                alignment
            ]


# ============================================================
# PARAGRAPH SPACING
# ============================================================

def apply_paragraph_spacing(
    paragraph,
    tag,
):
    styles = parse_inline_style(tag)

    line_height = styles.get(
        "line-height"
    )

    if line_height:

        value = str(
            line_height
        ).strip().lower()

        match = re.match(
            r"([0-9]+(?:\.[0-9]+)?)",
            value,
        )

        if match:
            number = float(
                match.group(1)
            )

            if value.endswith(
                ("pt", "px")
            ):
                paragraph.paragraph_format.line_spacing = Pt(
                    number
                    if value.endswith("pt")
                    else number * 0.75
                )
            else:
                paragraph.paragraph_format.line_spacing = number


# ============================================================
# IMAGE HANDLING
# ============================================================

def add_image_to_paragraph(
    paragraph,
    img_tag,
):
    """
    Add an image from a local/file data source.

    Supports normal local file paths and data:image URLs.
    """

    src = img_tag.get("src", "")

    if not src:
        return False

    src = str(src).strip()

    # --------------------------------------------------------
    # Data URL
    # --------------------------------------------------------

    if src.startswith("data:image/"):

        try:
            import base64
            import tempfile

            header, encoded = src.split(
                ",",
                1,
            )

            image_data = base64.b64decode(
                encoded
            )

            if len(image_data) > MAX_IMAGE_SIZE:
                return False

            suffix = ".png"

            if "jpeg" in header or "jpg" in header:
                suffix = ".jpg"

            temp_file = tempfile.NamedTemporaryFile(
                suffix=suffix,
                delete=False,
            )

            temp_file.write(
                image_data
            )

            temp_file.close()

            path = temp_file.name

            try:
                add_picture_with_dimensions(
                    paragraph,
                    path,
                    img_tag,
                )
                return True
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        except Exception:
            return False

    # --------------------------------------------------------
    # Local file
    # --------------------------------------------------------

    if src.startswith(
        ("http://", "https://")
    ):
        # Remote downloads are intentionally disabled.
        return False

    try:

        path = Path(src)

        if not path.exists():
            return False

        if not path.is_file():
            return False

        if path.stat().st_size > MAX_IMAGE_SIZE:
            return False

        add_picture_with_dimensions(
            paragraph,
            str(path),
            img_tag,
        )

        return True

    except Exception:
        return False


# ============================================================
# IMAGE DIMENSIONS
# ============================================================

def add_picture_with_dimensions(
    paragraph,
    image_path,
    img_tag,
):
    """
    Add image while respecting HTML width/height when possible.
    """

    styles = parse_inline_style(
        img_tag
    )

    width_value = (
        img_tag.get("width")
        or styles.get("width")
    )

    height_value = (
        img_tag.get("height")
        or styles.get("height")
    )

    width = None
    height = None

    if width_value:

        match = re.match(
            r"([0-9]+(?:\.[0-9]+)?)",
            str(width_value),
        )

        if match:
            px = float(
                match.group(1)
            )

            width = Inches(
                min(
                    px / 96,
                    6.5,
                )
            )

    if height_value:

        match = re.match(
            r"([0-9]+(?:\.[0-9]+)?)",
            str(height_value),
        )

        if match:
            px = float(
                match.group(1)
            )

            height = Inches(
                min(
                    px / 96,
                    9.0,
                )
            )

    if width is not None:
        if height is not None:
            paragraph.add_run().add_picture(
                image_path,
                width=width,
                height=height,
            )
        else:
            paragraph.add_run().add_picture(
                image_path,
                width=width,
            )
    elif height is not None:
        paragraph.add_run().add_picture(
            image_path,
            height=height,
        )
    else:
        paragraph.add_run().add_picture(
            image_path,
            width=Inches(5.5),
        )


# ============================================================
# TABLE
# ============================================================

def set_cell_shading(
    cell,
    fill="EEEEEE",
):
    tc_pr = cell._tc.get_or_add_tcPr()

    shd = tc_pr.find(
        qn("w:shd")
    )

    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)

    shd.set(
        qn("w:fill"),
        fill,
    )


def set_cell_text(
    cell,
    text,
    bold=False,
):
    cell.text = ""

    paragraph = cell.paragraphs[0]

    run = paragraph.add_run(
        str(text or "")
    )

    run.bold = bold

    run.font.size = Pt(10)

    cell.vertical_alignment = (
        WD_CELL_VERTICAL_ALIGNMENT.TOP
    )


def convert_table(
    document,
    table_tag,
):
    rows = table_tag.find_all(
        "tr",
        recursive=False,
    )

    if not rows:
        return

    row_count = len(rows)

    col_count = max(
        [
            len(
                row.find_all(
                    ["td", "th"],
                    recursive=False,
                )
            )
            for row in rows
        ],
        default=1,
    )

    col_count = max(
        1,
        min(col_count, 30),
    )

    word_table = document.add_table(
        rows=row_count,
        cols=col_count,
    )

    word_table.alignment = (
        WD_TABLE_ALIGNMENT.CENTER
    )

    word_table.style = (
        "Table Grid"
    )

    for row_index, html_row in enumerate(rows):

        cells = html_row.find_all(
            ["td", "th"],
            recursive=False,
        )

        for col_index in range(
            min(
                len(cells),
                col_count,
            )
        ):

            html_cell = cells[
                col_index
            ]

            word_cell = word_table.cell(
                row_index,
                col_index,
            )

            text = html_cell.get_text(
                " ",
                strip=True,
            )

            is_header = (
                html_cell.name.lower()
                == "th"
            )

            set_cell_text(
                word_cell,
                text,
                bold=is_header,
            )

            if is_header:
                set_cell_shading(
                    word_cell,
                    "EEEEEE",
                )

    document.add_paragraph()


# ============================================================
# LIST HANDLING
# ============================================================

def convert_list(
    document,
    list_tag,
    level=0,
):
    """
    Convert UL / OL into Word list paragraphs.
    """

    tag_name = list_tag.name.lower()

    ordered = tag_name == "ol"

    for item in list_tag.find_all(
        "li",
        recursive=False,
    ):

        paragraph = document.add_paragraph()

        if ordered:
            paragraph.style = (
                "List Number"
            )
        else:
            paragraph.style = (
                "List Bullet"
            )

        if level > 0:
            paragraph.paragraph_format.left_indent = Inches(
                0.25 * level
            )

        # ----------------------------------------------------
        # Add inline content
        # ----------------------------------------------------

        for child in item.children:

            if isinstance(
                child,
                NavigableString,
            ):

                add_text_run(
                    paragraph,
                    str(child),
                )

            elif isinstance(
                child,
                Tag,
            ):

                if child.name.lower() in (
                    "ul",
                    "ol",
                ):
                    convert_list(
                        document,
                        child,
                        level + 1,
                    )
                else:
                    add_inline_content(
                        paragraph,
                        child,
                    )


# ============================================================
# HTML → DOCX BLOCK CONVERTER
# ============================================================

def convert_html_to_docx(
    document,
    soup,
):
    """
    Convert document editor HTML into Word.
    """

    root = soup.body or soup

    for node in root.children:

        if isinstance(
            node,
            NavigableString,
        ):

            text = str(node).strip()

            if text:
                paragraph = document.add_paragraph()
                paragraph.add_run(text)

            continue

        if not isinstance(
            node,
            Tag,
        ):
            continue

        tag_name = node.name.lower()

        # ----------------------------------------------------
        # Ignore
        # ----------------------------------------------------

        if tag_name in (
            "script",
            "style",
            "header",
            "footer",
        ):
            continue

        # ----------------------------------------------------
        # Headings
        # ----------------------------------------------------

        if tag_name in (
            "h1",
            "h2",
            "h3",
        ):

            level = int(
                tag_name[1]
            )

            paragraph = document.add_paragraph()

            paragraph.style = (
                f"Heading {level}"
            )

            add_inline_content(
                paragraph,
                node,
            )

            apply_paragraph_style(
                paragraph,
                node,
            )

            continue

        # ----------------------------------------------------
        # Paragraph
        # ----------------------------------------------------

        if tag_name == "p":

            paragraph = document.add_paragraph()

            add_inline_content(
                paragraph,
                node,
            )

            apply_paragraph_style(
                paragraph,
                node,
            )

            apply_paragraph_spacing(
                paragraph,
                node,
            )

            continue

        # ----------------------------------------------------
        # Blockquote
        # ----------------------------------------------------

        if tag_name == "blockquote":

            paragraph = document.add_paragraph()

            paragraph.style = (
                "Intense Quote"
            )

            add_inline_content(
                paragraph,
                node,
            )

            continue

        # ----------------------------------------------------
        # Lists
        # ----------------------------------------------------

        if tag_name in (
            "ul",
            "ol",
        ):

            convert_list(
                document,
                node,
            )

            continue

        # ----------------------------------------------------
        # Table
        # ----------------------------------------------------

        if tag_name == "table":

            convert_table(
                document,
                node,
            )

            continue

        # ----------------------------------------------------
        # Image
        # ----------------------------------------------------

        if tag_name == "img":

            paragraph = document.add_paragraph()

            paragraph.alignment = (
                WD_ALIGN_PARAGRAPH.CENTER
            )

            add_image_to_paragraph(
                paragraph,
                node,
            )

            continue

        # ----------------------------------------------------
        # Horizontal rule
        # ----------------------------------------------------

        if tag_name == "hr":

            paragraph = document.add_paragraph()

            p_pr = (
                paragraph._p
                .get_or_add_pPr()
            )

            p_bdr = OxmlElement(
                "w:pBdr"
            )

            bottom = OxmlElement(
                "w:bottom"
            )

            bottom.set(
                qn("w:val"),
                "single",
            )

            bottom.set(
                qn("w:sz"),
                "8",
            )

            bottom.set(
                qn("w:space"),
                "1",
            )

            bottom.set(
                qn("w:color"),
                "999999",
            )

            p_bdr.append(bottom)
            p_pr.append(p_bdr)

            continue

        # ----------------------------------------------------
        # DIV / SECTION
        # ----------------------------------------------------

        if tag_name in (
            "div",
            "section",
            "article",
            "main",
        ):

            # Process nested block elements.
            has_block = node.find(
                [
                    "p",
                    "h1",
                    "h2",
                    "h3",
                    "ul",
                    "ol",
                    "table",
                    "blockquote",
                    "img",
                    "hr",
                ]
            )

            if has_block:

                nested_soup = BeautifulSoup(
                    str(node),
                    "html.parser",
                )

                convert_html_to_docx(
                    document,
                    nested_soup,
                )

            else:

                text = node.get_text(
                    " ",
                    strip=True,
                )

                if text:

                    paragraph = document.add_paragraph()

                    add_inline_content(
                        paragraph,
                        node,
                    )

            continue

        # ----------------------------------------------------
        # Generic fallback
        # ----------------------------------------------------

        text = node.get_text(
            " ",
            strip=True,
        )

        if text:

            paragraph = document.add_paragraph()

            add_inline_content(
                paragraph,
                node,
            )


# ============================================================
# DOCUMENT CORE PROPERTIES
# ============================================================

def set_core_properties(
    document,
    title,
    author,
    subject,
):
    properties = document.core_properties

    properties.title = (
        title or "DocBuddy Document"
    )

    properties.author = (
        author or "DocBuddy"
    )

    properties.subject = (
        subject or ""
    )

    properties.comments = (
        "Created with DocBuddy"
    )


# ============================================================
# HEADER
# ============================================================

def configure_header(
    section,
    enabled,
    text,
):
    if not enabled:
        return

    header = section.header

    paragraph = (
        header.paragraphs[0]
    )

    paragraph.alignment = (
        WD_ALIGN_PARAGRAPH.RIGHT
    )

    paragraph.text = ""

    run = paragraph.add_run(
        text or ""
    )

    run.font.name = "Arial"
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(
        100,
        100,
        100,
    )


# ============================================================
# FOOTER
# ============================================================

def configure_footer(
    section,
    enabled,
    text,
    page_numbers=True,
):
    if not enabled and not page_numbers:
        return

    footer = section.footer

    paragraph = (
        footer.paragraphs[0]
    )

    paragraph.alignment = (
        WD_ALIGN_PARAGRAPH.CENTER
    )

    paragraph.text = ""

    if enabled and text:
        run = paragraph.add_run(
            text
        )

        run.font.name = "Arial"
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(
            100,
            100,
            100,
        )

    if enabled and text and page_numbers:
        paragraph.add_run("    |    ")

    if page_numbers:

        run = paragraph.add_run(
            "Page "
        )

        run.font.name = "Arial"
        run.font.size = Pt(9)

        # PAGE FIELD
        fld_char_begin = OxmlElement(
            "w:fldChar"
        )

        fld_char_begin.set(
            qn("w:fldCharType"),
            "begin",
        )

        instr_text = OxmlElement(
            "w:instrText"
        )

        instr_text.set(
            qn("xml:space"),
            "preserve",
        )

        instr_text.text = " PAGE "

        fld_char_end = OxmlElement(
            "w:fldChar"
        )

        fld_char_end.set(
            qn("w:fldCharType"),
            "end",
        )

        run._r.append(
            fld_char_begin
        )

        run._r.append(
            instr_text
        )

        run._r.append(
            fld_char_end
        )


# ============================================================
# DEFAULT FONT
# ============================================================

def configure_default_font(
    document,
):
    styles = document.styles

    normal = styles["Normal"]

    normal.font.name = "Arial"
    normal.font.size = Pt(11)

    normal._element.rPr.rFonts.set(
        qn("w:ascii"),
        "Arial",
    )

    normal._element.rPr.rFonts.set(
        qn("w:hAnsi"),
        "Arial",
    )


# ============================================================
# PAGE CONFIGURATION
# ============================================================

def configure_page(
    document,
    page_size,
    orientation,
    margins,
):
    page_size = (
        page_size
        if page_size in PAGE_SIZES
        else "A4"
    )

    orientation = (
        str(orientation)
        .lower()
    )

    margins = (
        margins
        if margins in MARGIN_SIZES
        else "normal"
    )

    section = document.sections[0]

    size = PAGE_SIZES[
        page_size
    ]

    section.page_width = (
        size["width"]
    )

    section.page_height = (
        size["height"]
    )

    if orientation == "landscape":

        section.orientation = (
            WD_ORIENT.LANDSCAPE
        )

        # Swap width and height
        section.page_width = (
            size["height"]
        )

        section.page_height = (
            size["width"]
        )

    else:

        section.orientation = (
            WD_ORIENT.PORTRAIT
        )

    margin = MARGIN_SIZES[
        margins
    ]

    section.top_margin = (
        margin["top"]
    )

    section.bottom_margin = (
        margin["bottom"]
    )

    section.left_margin = (
        margin["left"]
    )

    section.right_margin = (
        margin["right"]
    )


# ============================================================
# REMOVE EMPTY FIRST PARAGRAPH
# ============================================================

def remove_unwanted_empty_paragraphs(
    document,
):
    """
    Remove unnecessary empty paragraphs while
    keeping the document valid.
    """

    body = document._element.body

    paragraphs = list(
        body.iterchildren()
    )

    for element in paragraphs:

        if element.tag != qn("w:p"):
            continue

        text = "".join(
            element.itertext()
        ).strip()

        if text:
            continue

        # Keep at least one paragraph
        # when the document is otherwise empty.
        remaining = [
            item
            for item in body.iterchildren()
            if item.tag == qn("w:p")
        ]

        if len(remaining) <= 1:
            continue

        body.remove(element)


# ============================================================
# MAIN DOCX CREATOR
# ============================================================

def create_docx(
    output_path,
    title="My Document",
    author="",
    subject="",
    document_type="General Document",
    page_size="A4",
    orientation="portrait",
    margins="normal",
    header_enabled=False,
    header_text="",
    footer_enabled=False,
    footer_text="",
    page_numbers=True,
    content_html="",
):
    """
    Main function called by routes/create.py.

    Creates a fully formatted DOCX file from the
    contenteditable HTML generated by create.html.
    """

    if not output_path:
        raise ValueError(
            "Output path is required."
        )

    if not content_html:
        raise ValueError(
            "Document content cannot be empty."
        )

    if len(content_html) > MAX_HTML_LENGTH:
        raise ValueError(
            "Document content is too large."
        )

    # --------------------------------------------------------
    # Normalize values
    # --------------------------------------------------------

    title = (
        str(title).strip()
        or "My Document"
    )

    author = str(
        author or ""
    ).strip()

    subject = str(
        subject or ""
    ).strip()

    document_type = str(
        document_type
        or "General Document"
    ).strip()

    # --------------------------------------------------------
    # Create Word document
    # --------------------------------------------------------

    document = Document()

    configure_default_font(
        document
    )

    configure_page(
        document,
        page_size,
        orientation,
        margins,
    )

    set_core_properties(
        document,
        title,
        author,
        subject,
    )

    section = document.sections[0]

    # --------------------------------------------------------
    # Header
    # --------------------------------------------------------

    configure_header(
        section,
        header_enabled,
        header_text,
    )

    # --------------------------------------------------------
    # Footer
    # --------------------------------------------------------

    configure_footer(
        section,
        footer_enabled,
        footer_text,
        page_numbers,
    )

    # --------------------------------------------------------
    # HTML sanitization
    # --------------------------------------------------------

    soup = sanitize_html(
        content_html
    )

    # --------------------------------------------------------
    # Convert editor content
    # --------------------------------------------------------

    convert_html_to_docx(
        document,
        soup,
    )

    # --------------------------------------------------------
    # Ensure something exists
    # --------------------------------------------------------

    if not document.paragraphs and not document.tables:

        paragraph = document.add_paragraph()

        paragraph.add_run(
            " "
        )

    # --------------------------------------------------------
    # Remove unnecessary empty paragraphs
    # --------------------------------------------------------

    remove_unwanted_empty_paragraphs(
        document
    )

    # --------------------------------------------------------
    # Ensure output directory
    # --------------------------------------------------------

    output = Path(
        output_path
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    document.save(
        str(output)
    )

    # --------------------------------------------------------
    # Verify
    # --------------------------------------------------------

    if not output.exists():
        raise RuntimeError(
            "DOCX file was not created."
        )

    if output.stat().st_size <= 0:
        raise RuntimeError(
            "Created DOCX file is empty."
        )

    return str(output)