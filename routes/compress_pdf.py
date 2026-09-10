from __future__ import annotations

import os
import threading
import time
import uuid
import logging

from flask import Blueprint, request, jsonify, send_file, render_template, current_app
from werkzeug.utils import secure_filename

from services.pdf_compress_service import (
    CompressionOptions,
    CompressionLevel,
    compress_pdf_batch,
    new_temp_workspace,
    cleanup_workspace,
    available_engines,
    CompressionError,
    InvalidPDFError,
    PasswordRequiredError,
    EngineUnavailableError,
)

logger = logging.getLogger("routes.compress_pdf")

compress_pdf_bp = Blueprint("compress_pdf", __name__)

MAX_FILES_PER_BATCH = 20
MAX_FILE_SIZE_BYTES = 150 * 1024 * 1024
JOB_TTL_SECONDS = 60 * 60
JOBS = {}
JOBS_LOCK = threading.RLock()


def _allowed_file(filename: str) -> bool:
    return os.path.splitext(filename or "")[1].lower() == ".pdf"


def _error(message: str, status: int = 400, **extra):
    payload = {"success": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def _new_job_record(job_id: str, workspace: str, total_files: int) -> dict:
    return {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "Queued…",
        "workspace": workspace,
        "created_at": time.time(),
        "total_files": total_files,
        "results": [],
        "zip_path": None,
        "error": None,
    }


def _cleanup_stale_jobs():
    now = time.time()
    with JOBS_LOCK:
        stale = [jid for jid, job in JOBS.items() if now - job["created_at"] > JOB_TTL_SECONDS]
        for jid in stale:
            cleanup_workspace(JOBS[jid]["workspace"])
            del JOBS[jid]


def _run_job(app, job_id: str, input_paths: list, options: CompressionOptions):
    with app.app_context():
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            job["status"] = "processing"

        def progress_cb(message: str, percent: int):
            with JOBS_LOCK:
                current = JOBS.get(job_id)
                if current:
                    current["progress"] = max(0, min(int(percent), 99))
                    current["message"] = message

        try:
            results = compress_pdf_batch(input_paths, job["workspace"], options=options, progress=progress_cb)
            with JOBS_LOCK:
                current = JOBS.get(job_id)
                if not current:
                    cleanup_workspace(job["workspace"])
                    return
                current["results"] = [result.to_dict() for result in results]
                current["status"] = "completed"
                current["progress"] = 100
                current["message"] = "Compression complete."
        except PasswordRequiredError as exc:
            _set_failed(job_id, "Password required.", str(exc))
        except InvalidPDFError as exc:
            _set_failed(job_id, "Invalid PDF.", str(exc))
        except EngineUnavailableError as exc:
            _set_failed(job_id, "Server misconfiguration.", str(exc))
        except CompressionError as exc:
            _set_failed(job_id, "Compression failed.", str(exc))
        except Exception as exc:
            logger.exception("Unexpected error in compression job %s", job_id)
            _set_failed(job_id, "Unexpected error.", f"Unexpected server error: {exc}")


def _set_failed(job_id: str, message: str, error: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job["status"] = "failed"
            job["message"] = message
            job["error"] = error


@compress_pdf_bp.route("/compress-pdf/", methods=["GET"], endpoint="compress_page")
@compress_pdf_bp.route("/compress-pdf", methods=["GET"])
def compress_page():
    return render_template("compress_pdf.html")


@compress_pdf_bp.route("/api/compress-pdf/capabilities", methods=["GET"])
def capabilities():
    return jsonify({"success": True, **available_engines()})


@compress_pdf_bp.route("/api/compress-pdf/upload", methods=["POST"])
def upload():
    _cleanup_stale_jobs()
    files = request.files.getlist("files")
    files = [f for f in files if f and f.filename]

    if not files:
        return _error("No PDF files were uploaded.")
    if len(files) > MAX_FILES_PER_BATCH:
        return _error(f"Too many files. Maximum is {MAX_FILES_PER_BATCH} PDFs per batch.")

    for file in files:
        if not _allowed_file(file.filename):
            return _error(f'"{file.filename}" is not a PDF.')

    try:
        level = CompressionLevel(request.form.get("level", "medium"))
    except ValueError:
        return _error("Invalid compression level.")

    def as_bool(name: str) -> bool:
        return request.form.get(name, "false").lower() in {"1", "true", "yes", "on"}

    def as_int(name: str, default: int) -> int:
        try:
            return int(request.form.get(name, default))
        except (TypeError, ValueError):
            return default

    options = CompressionOptions(
        level=level,
        remove_metadata=as_bool("remove_metadata"),
        linearize=as_bool("linearize"),
        grayscale=as_bool("grayscale"),
        password=request.form.get("password") or None,
        custom_dpi=max(36, min(as_int("custom_dpi", 150), 600)),
        custom_jpeg_quality=max(10, min(as_int("custom_jpeg_quality", 70), 100)),
    )

    job_id = uuid.uuid4().hex[:12]
    workspace = new_temp_workspace()
    input_paths = []

    try:
        for uploaded in files:
            filename = secure_filename(uploaded.filename) or f"upload_{uuid.uuid4().hex[:8]}.pdf"
            path = os.path.join(workspace, filename)
            uploaded.save(path)
            size = os.path.getsize(path)
            if size == 0:
                raise InvalidPDFError(f'"{filename}" is empty.')
            if size > MAX_FILE_SIZE_BYTES:
                raise InvalidPDFError(f'"{filename}" exceeds the 150 MB per-file limit.')
            input_paths.append(path)
    except InvalidPDFError as exc:
        cleanup_workspace(workspace)
        return _error(str(exc))
    except Exception as exc:
        cleanup_workspace(workspace)
        return _error(f"Could not save the uploaded PDF: {exc}", 500)

    with JOBS_LOCK:
        JOBS[job_id] = _new_job_record(job_id, workspace, len(input_paths))

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_job, args=(app, job_id, input_paths, options), daemon=True)
    thread.start()

    return jsonify({
        "success": True,
        "job_id": job_id,
        "total_files": len(input_paths),
        "status_url": f"/api/compress-pdf/status/{job_id}",
    }), 202


@compress_pdf_bp.route("/api/compress-pdf/status/<job_id>", methods=["GET"])
def status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return _error("Job not found or already expired.", 404)
        return jsonify({
            "success": True,
            "job_id": job_id,
            "status": job["status"],
            "progress": job["progress"],
            "message": job["message"],
            "total_files": job["total_files"],
            "results": job["results"],
            "error": job["error"],
        })


def _get_result_file(job_id: str, file_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None, None, _error("Job not found or expired.", 404)
        if job["status"] != "completed":
            return None, None, _error("Compression is not finished yet.", 409)
        match = next((result for result in job["results"] if result["job_id"] == file_id), None)
        if not match:
            return None, None, _error("Compressed file not found in this job.", 404)
        output_path = os.path.join(
            job["workspace"],
            f"{os.path.splitext(match['input_filename'])[0]}_compressed_{file_id}.pdf",
        )

    if not os.path.exists(output_path):
        return None, None, _error("Compressed file is no longer available.", 410)
    return output_path, match, None


@compress_pdf_bp.route("/api/compress-pdf/preview/<job_id>/<file_id>", methods=["GET"])
def preview_one(job_id: str, file_id: str):
    output_path, match, error = _get_result_file(job_id, file_id)
    if error:
        return error
    return send_file(
        output_path,
        as_attachment=False,
        download_name=f"compressed_{match['input_filename']}",
        mimetype="application/pdf",
    )


@compress_pdf_bp.route("/api/compress-pdf/download/<job_id>/<file_id>", methods=["GET"])
def download_one(job_id: str, file_id: str):
    output_path, match, error = _get_result_file(job_id, file_id)
    if error:
        return error
    return send_file(
        output_path,
        as_attachment=True,
        download_name=f"compressed_{match['input_filename']}",
        mimetype="application/pdf",
    )


@compress_pdf_bp.route("/api/compress-pdf/job/<job_id>", methods=["DELETE"])
def delete_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.pop(job_id, None)
    if not job:
        return jsonify({"success": True, "message": "Job already removed."})
    cleanup_workspace(job["workspace"])
    return jsonify({"success": True, "message": "Job removed and temporary files cleaned up."})


@compress_pdf_bp.route("/api/compress-pdf/history", methods=["GET"])
def history():
    with JOBS_LOCK:
        jobs = sorted(JOBS.values(), key=lambda item: item["created_at"], reverse=True)[:25]
        return jsonify({
            "success": True,
            "jobs": [{
                "job_id": job["job_id"],
                "status": job["status"],
                "total_files": job["total_files"],
                "created_at": job["created_at"],
                "results": job["results"],
            } for job in jobs],
        })


@compress_pdf_bp.errorhandler(413)
def too_large(error):
    return jsonify({"success": False, "error": "Upload too large. Maximum file size is 150 MB."}), 413
