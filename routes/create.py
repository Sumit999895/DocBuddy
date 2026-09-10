import json
import re
import tempfile
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    send_file,
    Response,
)

from services.create_service import (
    create_docx,
    sanitize_html,
)


create_bp = Blueprint("create", __name__)

CREATE_TEMP_DIR = Path(tempfile.gettempdir()) / "docbuddy_create"
CREATE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

DOCX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)


def valid_job_id(job_id):
    return bool(
        job_id
        and re.fullmatch(r"[a-fA-F0-9]{32}", str(job_id))
    )


def get_job_file(job_id):
    if not valid_job_id(job_id):
        return None

    path = CREATE_TEMP_DIR / f"{job_id}.docx"

    if not path.exists() or not path.is_file():
        return None

    return path


def get_preview_file(job_id):
    if not valid_job_id(job_id):
        return None

    path = CREATE_TEMP_DIR / f"{job_id}.html"

    if not path.exists() or not path.is_file():
        return None

    return path


def get_metadata_file(job_id):
    if not valid_job_id(job_id):
        return None

    path = CREATE_TEMP_DIR / f"{job_id}.json"

    if not path.exists() or not path.is_file():
        return None

    return path


def safe_download_name(title):
    name = re.sub(
        r"[^A-Za-z0-9._ -]+",
        "",
        str(title or "DocBuddy_Document"),
    ).strip()

    name = name[:180] or "DocBuddy_Document"

    if name.lower().endswith(".docx"):
        return name

    return f"{name}.docx"


@create_bp.route("/create-document", methods=["GET"])
@create_bp.route("/create-document/", methods=["GET"])
def create_document_page():
    return render_template("create.html")


@create_bp.route("/api/create-document", methods=["POST"])
def create_document():
    output_path = None
    preview_path = None
    metadata_path = None

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "success": False,
                "error": "Invalid document data.",
            }), 400

        title = str(data.get("title", "My Document")).strip()
        title = title or "My Document"

        content_html = str(data.get("content_html", ""))

        if not content_html.strip():
            return jsonify({
                "success": False,
                "error": "Document content cannot be empty.",
            }), 400

        if len(content_html) > 5_000_000:
            return jsonify({
                "success": False,
                "error": "Document content is too large.",
            }), 413

        job_id = uuid.uuid4().hex

        output_path = CREATE_TEMP_DIR / f"{job_id}.docx"
        preview_path = CREATE_TEMP_DIR / f"{job_id}.html"
        metadata_path = CREATE_TEMP_DIR / f"{job_id}.json"

        author = str(data.get("author", "")).strip()
        subject = str(data.get("subject", "")).strip()
        document_type = str(
            data.get("document_type", "General Document")
        ).strip()

        page_size = str(data.get("page_size", "A4")).strip()
        orientation = str(
            data.get("orientation", "portrait")
        ).strip().lower()
        margins = str(data.get("margins", "normal")).strip()

        header_enabled = bool(data.get("header_enabled", False))
        header_text = str(data.get("header_text", "")).strip()

        footer_enabled = bool(data.get("footer_enabled", False))
        footer_text = str(data.get("footer_text", "")).strip()

        page_numbers = bool(data.get("page_numbers", True))

        create_docx(
            output_path=str(output_path),
            title=title,
            author=author,
            subject=subject,
            document_type=document_type,
            page_size=page_size,
            orientation=orientation,
            margins=margins,
            header_enabled=header_enabled,
            header_text=header_text,
            footer_enabled=footer_enabled,
            footer_text=footer_text,
            page_numbers=page_numbers,
            content_html=content_html,
        )

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("DOCX file was not created.")

        # Store a browser-viewable preview separately.
        clean_html = str(sanitize_html(content_html))

        preview_document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#202226;font-family:Arial,sans-serif;color:#222}}
.paper{{width:{'11.69in' if orientation == 'landscape' else '8.27in'};
min-height:{'8.27in' if orientation == 'landscape' else '11.69in'};
margin:30px auto;background:#fff;padding:{'0.5in' if margins == 'narrow' else '1.25in' if margins == 'wide' else '1in'};
box-shadow:0 20px 70px rgba(0,0,0,.45)}}
.header{{padding-bottom:8px;margin-bottom:20px;border-bottom:1px solid #ddd;text-align:right;color:#666;font-size:10px}}
.footer{{display:flex;justify-content:space-between;margin-top:30px;padding-top:8px;border-top:1px solid #ddd;color:#666;font-size:10px}}
table{{width:100%;border-collapse:collapse;margin:16px 0}}
th,td{{border:1px solid #999;padding:8px;text-align:left;vertical-align:top}}
th{{background:#eee}}
img{{max-width:100%;height:auto}}
blockquote{{border-left:4px solid #ff8a1f;background:#f5f5f5;padding:10px 16px;color:#555}}
a{{color:#1267b1;text-decoration:underline}}
</style>
</head>
<body>
<div class="paper">
{f'<div class="header">{_escape(header_text)}</div>' if header_enabled and header_text else ''}
{clean_html}
{f'<div class="footer"><span>{_escape(footer_text) if footer_enabled else ""}</span><span>{"Page 1" if page_numbers else ""}</span></div>' if footer_enabled or page_numbers else ''}
</div>
</body>
</html>"""

        preview_path.write_text(
            preview_document,
            encoding="utf-8",
        )

        filename = safe_download_name(title)

        metadata_path.write_text(
            json.dumps({
                "filename": filename,
                "title": title,
            }),
            encoding="utf-8",
        )

        return jsonify({
            "success": True,
            "job_id": job_id,
            "filename": filename,
            "download_url": f"/api/create-document/download/{job_id}",
            "preview_url": f"/api/create-document/preview/{job_id}",
        })

    except Exception as exc:
        for path in (output_path, preview_path, metadata_path):
            if path is not None:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    pass

        return jsonify({
            "success": False,
            "error": f"Document creation failed: {exc}",
        }), 500


def _escape(value):
    import html
    return html.escape(str(value or ""))


@create_bp.route("/api/create-document/preview/<job_id>", methods=["GET"])
def preview_document(job_id):
    preview_path = get_preview_file(job_id)

    if preview_path is None:
        return jsonify({
            "success": False,
            "error": "Preview not found or expired.",
        }), 404

    response = send_file(
        str(preview_path),
        mimetype="text/html; charset=utf-8",
        as_attachment=False,
        download_name="preview.html",
    )

    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "no-store, max-age=0"

    return response


@create_bp.route("/api/create-document/download/<job_id>", methods=["GET"])
def download_document(job_id):
    file_path = get_job_file(job_id)

    if file_path is None:
        return jsonify({
            "success": False,
            "error": "Document not found or expired.",
        }), 404

    filename = "DocBuddy_Document.docx"

    metadata_path = get_metadata_file(job_id)

    if metadata_path is not None:
        try:
            metadata = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            filename = safe_download_name(
                metadata.get("filename", filename)
            )
        except Exception:
            pass

    return send_file(
        str(file_path),
        mimetype=DOCX_MIMETYPE,
        as_attachment=True,
        download_name=filename,
    )
