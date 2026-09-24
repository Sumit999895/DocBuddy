# routes/split_merge.py

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    render_template,
    request,
    send_file,
    jsonify,
)

from services.split_merge_service import (
    PDFSplitMergeService,
    PDFValidationError,
)


split_merge_bp = Blueprint(
    "split_merge",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


# ============================================================
# TEMPORARY SPLIT FILE STORAGE
# ============================================================

SPLIT_JOBS = {}


def create_split_job():
    job_id = uuid.uuid4().hex

    job_dir = Path(
        tempfile.gettempdir()
    ) / "docbuddy_split" / job_id

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    SPLIT_JOBS[job_id] = {
        "directory": str(job_dir),
        "files": {},
    }

    return job_id, job_dir


def get_split_file(job_id, filename):

    job = SPLIT_JOBS.get(job_id)

    if not job:
        return None

    file_info = job["files"].get(filename)

    if not file_info:
        return None

    path = Path(file_info["path"])

    if not path.exists():
        return None

    return path


def cleanup_split_job(job_id):

    job = SPLIT_JOBS.pop(job_id, None)

    if not job:
        return

    try:
        shutil.rmtree(
            job["directory"],
            ignore_errors=True
        )
    except Exception:
        pass


# ============================================================
# PAGE
# ============================================================

@split_merge_bp.route(
    "/split-merge",
    methods=["GET"]
)
def split_merge():

    return render_template(
        "split_merge.html"
    )


# ============================================================
# PAGE COUNT
# ============================================================

@split_merge_bp.route(
    "/split-merge/page-count",
    methods=["POST"]
)
def page_count():

    try:

        file = request.files.get("file")

        count = PDFSplitMergeService.get_page_count(
            file
        )

        return jsonify({
            "success": True,
            "page_count": count,
        })

    except PDFValidationError as exc:

        return jsonify({
            "success": False,
            "error": str(exc),
        }), 400

    except Exception as exc:

        print(
            "PDF page count error:",
            repr(exc)
        )

        return jsonify({
            "success": False,
            "error": "Could not read this PDF.",
        }), 500


# ============================================================
# SPLIT PDF
# ============================================================

@split_merge_bp.route(
    "/split-merge/split",
    methods=["POST"]
)
def split_pdf():

    job_id = None

    try:

        file = request.files.get("file")

        if not file:
            raise PDFValidationError(
                "Please select a PDF."
            )

        mode = (
            request.form.get("mode")
            or "all"
        ).strip().lower()

        # -----------------------------------------------
        # Create temporary job directory
        # -----------------------------------------------

        job_id, job_dir = create_split_job()

        # -----------------------------------------------
        # Save uploaded PDF temporarily
        # -----------------------------------------------

        original_name = (
            file.filename
            or "document.pdf"
        )

        input_path = (
            job_dir /
            "original.pdf"
        )

        file.save(
            str(input_path)
        )

        # -----------------------------------------------
        # Read PDF
        # -----------------------------------------------

        with open(
            input_path,
            "rb"
        ) as pdf_file:

            from pypdf import PdfReader

            reader = PdfReader(
                pdf_file
            )

            if reader.is_encrypted:

                raise PDFValidationError(
                    "Password-protected PDFs "
                    "are not supported."
                )

            if len(reader.pages) == 0:

                raise PDFValidationError(
                    "The PDF has no pages."
                )

            # -------------------------------------------
            # Split all pages
            # -------------------------------------------

            if mode == "all":

                results = (
                    PDFSplitMergeService
                    .split_all_pages(
                        reader,
                        str(job_dir),
                        original_name,
                    )
                )

            # -------------------------------------------
            # Split by ranges
            # -------------------------------------------

            elif mode == "ranges":

                ranges = (
                    request.form.get(
                        "ranges",
                        ""
                    ).strip()
                )

                results = (
                    PDFSplitMergeService
                    .split_by_ranges(
                        reader,
                        ranges,
                        str(job_dir),
                        original_name,
                    )
                )

            else:

                raise PDFValidationError(
                    "Invalid split mode."
                )

        # -----------------------------------------------
        # Delete uploaded original
        # -----------------------------------------------

        try:
            input_path.unlink()
        except Exception:
            pass

        # -----------------------------------------------
        # Build response
        # -----------------------------------------------

        response_files = []

        for result in results:

            filename = result["filename"]

            SPLIT_JOBS[job_id]["files"][
                filename
            ] = {
                "path": result["path"]
            }

            response_files.append({

                "filename": filename,

                "label": result["label"],

                "start_page":
                    result["start_page"],

                "end_page":
                    result["end_page"],

                "preview_url":
                    f"/split-merge/preview/"
                    f"{job_id}/"
                    f"{filename}",

                "download_url":
                    f"/split-merge/download/"
                    f"{job_id}/"
                    f"{filename}",
            })

        return jsonify({

            "success": True,

            "job_id": job_id,

            "files": response_files,

        })

    except PDFValidationError as exc:

        if job_id:
            cleanup_split_job(job_id)

        return jsonify({
            "success": False,
            "error": str(exc),
        }), 400

    except Exception as exc:

        print(
            "Split PDF error:",
            repr(exc)
        )

        if job_id:
            cleanup_split_job(job_id)

        return jsonify({
            "success": False,
            "error":
                "Unable to split this PDF. "
                "Please try again.",
        }), 500


# ============================================================
# PREVIEW SPLIT PDF
# ============================================================

@split_merge_bp.route(
    "/split-merge/preview/<job_id>/<path:filename>",
    methods=["GET"]
)
def preview_split_pdf(
    job_id,
    filename
):

    path = get_split_file(
        job_id,
        filename
    )

    if not path:

        return jsonify({
            "success": False,
            "error": "Preview file not found.",
        }), 404

    response = send_file(
        str(path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
        max_age=0,
    )

    response.headers[
        "Content-Disposition"
    ] = (
        f'inline; filename="{filename}"'
    )

    return response


# ============================================================
# DOWNLOAD ONE SPLIT PDF
# ============================================================

@split_merge_bp.route(
    "/split-merge/download/<job_id>/<path:filename>",
    methods=["GET"]
)
def download_split_pdf(
    job_id,
    filename
):

    path = get_split_file(
        job_id,
        filename
    )

    if not path:

        return jsonify({
            "success": False,
            "error": "Download file not found.",
        }), 404

    return send_file(
        str(path),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


# ============================================================
# MERGE PDF
# ============================================================

@split_merge_bp.route(
    "/split-merge/merge",
    methods=["POST"]
)
def merge_pdf():

    temp_dir = None

    try:

        files = request.files.getlist(
            "files"
        )

        if len(files) < 2:

            raise PDFValidationError(
                "Please select at least two PDFs."
            )

        output_name = (
            request.form.get(
                "output_name",
                "merged"
            ).strip()
            or "merged"
        )

        temp_dir = Path(
            tempfile.mkdtemp(
                prefix="docbuddy_merge_"
            )
        )

        output_path = (
            temp_dir /
            "merged.pdf"
        )

        result = (
            PDFSplitMergeService
            .merge_pdfs(
                files,
                str(output_path),
                output_name,
            )
        )

        return send_file(
            str(output_path),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=result["filename"],
            max_age=0,
        )

    except PDFValidationError as exc:

        return jsonify({
            "success": False,
            "error": str(exc),
        }), 400

    except Exception as exc:

        print(
            "Merge PDF error:",
            repr(exc)
        )

        return jsonify({
            "success": False,
            "error":
                "Unable to merge the PDFs.",
        }), 500