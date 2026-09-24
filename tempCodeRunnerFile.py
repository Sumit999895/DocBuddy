import os
import uuid
from pathlib import Path
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    session,
    redirect,
    url_for,
    flash,
    send_file
)

from dotenv import load_dotenv
import mysql.connector
import bcrypt

import secrets
import string
import re

from werkzeug.utils import secure_filename

from database import get_db_connection
from routes.pdf_to_jpg import pdf_to_jpg_bp
from routes.jpg_to_pdf import jpg_to_pdf_bp
from routes.pdf_to_docx import pdf_to_docx_bp
from routes.jpg_to_png import jpg_to_png_bp
from routes.png_to_jpg import png_to_jpg_bp
from routes.crop_image import crop_image_bp
from routes.resize import resize_bp
from routes.edit_pdf import edit_pdf_bp
from routes.compress_pdf import compress_pdf_bp
from routes.protect_unlock import protect_unlock_bp
from routes.scan import scan_bp
from routes.create import create_bp
from routes.page_format import page_format_bp
from routes.ocr_text import ocr_text_bp

from routes.auth import auth_bp, init_oauth
from services.mail_service import init_mail

from flask import send_file
from werkzeug.utils import secure_filename
from PIL import Image
from io import BytesIO


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()
print("================================")
print("MAIL DEBUG")
print("MAIL_USERNAME:", repr(os.getenv("MAIL_USERNAME")))
print("MAIL_DEFAULT_SENDER:", repr(os.getenv("MAIL_DEFAULT_SENDER")))
print("MAIL_PASSWORD configured:", bool(os.getenv("MAIL_PASSWORD")))
print("MAIL_PASSWORD length:", len(os.getenv("MAIL_PASSWORD", "")))
print("================================")


# ============================================================
# CREATE FLASK APPLICATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "docuforge_secret_key_default"
)


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024

# --- Google OAuth ---
app.config["GOOGLE_CLIENT_ID"] = os.getenv("GOOGLE_CLIENT_ID")
app.config["GOOGLE_CLIENT_SECRET"] = os.getenv("GOOGLE_CLIENT_SECRET")

# --- Outgoing mail (OTP codes / password reset) ---
app.config["MAIL_SERVER"] = os.getenv(
    "MAIL_SERVER",
    "smtp.gmail.com"
)

app.config["MAIL_PORT"] = int(
    os.getenv("MAIL_PORT", "587")
)

app.config["MAIL_USE_TLS"] = (
    os.getenv("MAIL_USE_TLS", "1") == "1"
)

app.config["MAIL_USERNAME"] = (
    os.getenv("MAIL_USERNAME", "").strip()
)

app.config["MAIL_PASSWORD"] = (
    os.getenv("MAIL_PASSWORD", "").strip()
)

app.config["MAIL_DEFAULT_SENDER"] = (
    os.getenv("MAIL_DEFAULT_SENDER", "").strip()
    or app.config["MAIL_USERNAME"]
)


# ============================================================
# UPLOAD FOLDER
# ============================================================

UPLOAD_FOLDER = Path(
    app.root_path
) / "uploads"

UPLOAD_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)

app.config["UPLOAD_FOLDER"] = str(
    UPLOAD_FOLDER
)

# ============================================================
# ALLOWED FILES
# ============================================================

ALLOWED_EXTENSIONS = {
    "pdf"
}


def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(
            ".",
            1
        )[1].lower()
        in ALLOWED_EXTENSIONS
    )


def generate_document_pin():
    """Generate a unique 8-character document PIN."""

    characters = string.ascii_uppercase + string.digits

    while True:
        pin = "".join(
            secrets.choice(characters)
            for _ in range(8)
        )

        db = None
        cursor = None

        try:
            db = get_db_connection()
            cursor = db.cursor()

            cursor.execute(
                """
                SELECT id
                FROM uploaded_documents
                WHERE document_pin = %s
                LIMIT 1
                """,
                (pin,)
            )

            if cursor.fetchone() is None:
                return pin

        finally:
            if cursor:
                cursor.close()

            if db and db.is_connected():
                db.close()


# ============================================================
# REGISTER BLUEPRINTS
# ============================================================

app.register_blueprint(auth_bp)          # /login, /register, /verify-otp,
                                          # /forgot-password, /reset-password,
                                          # /auth/google/*  (see routes/auth.py)
app.register_blueprint(pdf_to_jpg_bp)
app.register_blueprint(jpg_to_pdf_bp)
app.register_blueprint(pdf_to_docx_bp)
app.register_blueprint(jpg_to_png_bp)
app.register_blueprint(png_to_jpg_bp)
app.register_blueprint(crop_image_bp)
app.register_blueprint(resize_bp)
app.register_blueprint(edit_pdf_bp)
app.register_blueprint(compress_pdf_bp)
app.register_blueprint(protect_unlock_bp)
app.register_blueprint(scan_bp)
app.register_blueprint(create_bp)
app.register_blueprint(page_format_bp)
app.register_blueprint(ocr_text_bp)

init_mail(app)
init_oauth(app)

# optional but recommended — caps request size app-wide
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024

# ============================================================
# PLAN CATALOG
#
# Plan pricing/features live here in code for now. If you'd
# rather manage them from the database, create a `Plans` table
# with the same shape and swap this dict for a query — nothing
# else in the app needs to change, since every view reads PLANS
# through the helpers below.
# ============================================================

PLANS = {
    "free": {
        "id": "free",
        "name": "Free",
        "monthly_price": 0,
        "yearly_price": 0,
        "storage_mb": 500,
        "max_file_mb": 20,
        "features": [
            "500 MB storage",
            "Core PDF & image conversions",
            "Up to 20 MB per file",
        ],
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "monthly_price": 9,
        "yearly_price": 90,
        "storage_mb": 20480,
        "max_file_mb": 200,
        "features": [
            "20 GB storage",
            "Every conversion tool, unlimited use",
            "Watermark, Protect/Unlock, e-Sign",
            "Priority processing",
            "Up to 200 MB per file",
        ],
    },
}

STATS = {
    "docs_processed": "2.1M+",
    "docs_processed_label": "documents processed this year",
    "avg_rating": "4.8/5",
    "avg_rating_label": "average support rating",
    "avg_time_saved": "12 min",
    "avg_time_saved_label": "saved per batch job, on average",
}

COMPARISON_ROWS = [
    ("Storage", "500 MB", "20 GB"),
    ("Max file size", "20 MB", "200 MB"),
    ("Conversion tools", "Core tools", "Every tool, unlimited use"),
    ("Watermark / Protect / e-Sign", "—", "✓"),
    ("Processing speed", "Standard", "Priority"),
]

TESTIMONIALS = [
    {
        "quote": "We batch-convert about 200 scanned contracts a week. "
                 "Pro cut that from an afternoon to about 20 minutes.",
        "name": "Ananya R.",
        "role": "Operations lead, logistics startup",
    },
    {
        "quote": "The OCR on messy scanned invoices is what sold me — "
                 "it picks up totals our old tool used to miss.",
        "name": "Farhan K.",
        "role": "Freelance accountant",
    },
    {
        "quote": "The Watermark and e-Sign tools alone paid for the "
                 "upgrade in the first week.",
        "name": "Priya M.",
        "role": "Studio manager",
    },
]

FAQS = [
    (
        "Can I cancel anytime?",
        "Yes — cancel from your account settings whenever you like. "
        "You'll keep Pro access until the end of the period you already paid for.",
    ),
    (
        "What happens to my files if I downgrade?",
        "Nothing is deleted. You'll just drop back to Free plan limits "
        "(file size, storage) for anything you upload after that.",
    ),
    (
        "Is my payment information stored on your servers?",
        "No. We only keep the last 4 digits of your card for your records — "
        "full card numbers and CVV are never written to the database.",
    ),
    (
        "Can I switch from monthly to yearly billing?",
        "Yes — upgrade again from this page anytime; the new billing "
        "cycle starts from your next payment.",
    ),
]


# ============================================================
# ACCESS CONTROL
# ============================================================

LOGIN_REQUIRED_ENDPOINTS = {
    "upload_document",
    "view_uploaded_document",
    "download_uploaded_document",
    "delete_uploaded_document",
    "upgrade",
    "upgrade_checkout",
    "premium_tool_placeholder",
    "pdf_to_jpg.pdf_to_jpg",
    "pdf_to_docx.pdf_to_docx",
}


@app.before_request
def enforce_login_requirements():

    endpoint = request.endpoint

    if not endpoint:
        return None

    if endpoint in LOGIN_REQUIRED_ENDPOINTS and "user" not in session:

        flash(
            "Please log in to use this feature.",
            "info"
        )

        return redirect(
            url_for(
                "auth.login",
                next=request.path
            )
        )

    return None


# ============================================================
# TEMPLATE DEFAULTS
# ============================================================

@app.context_processor
def inject_template_defaults():

    return {
        "conversion_success": False,
        "user": None,
        "recent_uploads": [],
        "current_plan": "free",
        "is_guest": "user" not in session,
        "plans": PLANS,
    }


def get_current_user():

    if "user" not in session:
        return None

    user = session.get("user")

    if not isinstance(user, dict):
        return None

    return user


def get_user_plan(user_id):

    db = None
    cursor = None

    plan = "free"

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT plan, plan_expires_at
            FROM Users
            WHERE id = %s
            """,
            (user_id,)
        )

        row = cursor.fetchone()

        if row:

            plan = row.get("plan") or "free"
            expires_at = row.get("plan_expires_at")

            if (
                plan != "free"
                and expires_at
                and expires_at < datetime.utcnow()
            ):
                plan = "free"

    except mysql.connector.Error as e:
        print("get_user_plan error:", e)

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    return plan


@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():

    user = get_current_user()

    documents = []
    current_plan = "free"

    if user:

        db = None
        cursor = None

        try:

            db = get_db_connection()
            cursor = db.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT plan
                FROM Users
                WHERE id = %s
                """,
                (user["id"],)
            )

            plan_row = cursor.fetchone()

            current_plan = (
                (plan_row["plan"] if plan_row else "free")
                or "free"
            )

            cursor.execute(
                """
                SELECT
                    d.id,
                    d.filename,
                    d.filepath,
                    d.file_type,
                    d.file_size,
                    d.created_at,
                    ud.document_pin
                FROM Documents AS d
                LEFT JOIN uploaded_documents AS ud
                    ON ud.user_id = d.user_id
                   AND ud.file_path = d.filepath
                WHERE d.user_id = %s
                ORDER BY d.created_at DESC
                LIMIT 20
                """,
                (user["id"],)
            )

            documents = cursor.fetchall()

        except mysql.connector.Error as e:

            print("Dashboard database error:", e)
            flash("Could not load your documents.", "error")
            documents = []

        finally:
            if cursor:
                cursor.close()
            if db and db.is_connected():
                db.close()

    return render_template(
        "dashboard.html",
        user=user,
        uploads=documents,
        current_plan=current_plan,
        is_guest=(user is None),
        plans=PLANS,
    )


# ============================================================
# NOTE: /login, /register, /verify-otp, /resend-otp,
# /forgot-password, /verify-reset-otp, /reset-password, and
# /auth/google/* now live in routes/auth.py (see auth_bp).
# The old inline @app.route("/login") / @app.route("/register")
# definitions have been removed from this file to avoid a
# duplicate-endpoint error - do not add them back here.
# ============================================================


@app.route("/upload", methods=["POST"])
def upload_document():

    if "user" not in session:
        flash("Please log in first.", "error")
        return redirect(url_for("auth.login", next=url_for("dashboard")))

    if "file" not in request.files:
        flash("No file selected.", "error")
        return redirect(url_for("dashboard"))

    file = request.files["file"]

    if not file or file.filename == "":
        flash("No file selected.", "error")
        return redirect(url_for("dashboard"))

    if not allowed_file(file.filename):
        flash("Only PDF files are allowed.", "error")
        return redirect(url_for("dashboard"))

    filename = secure_filename(file.filename)

    if not filename:
        flash("Invalid file name.", "error")
        return redirect(url_for("dashboard"))

    document_pin = generate_document_pin()

    base_name, extension = os.path.splitext(filename)
    final_filename = filename
    counter = 1

    while os.path.exists(os.path.join(UPLOAD_FOLDER, final_filename)):
        final_filename = f"{base_name}_{counter}{extension}"
        counter += 1

    filename = final_filename
    file_path = os.path.join(UPLOAD_FOLDER, filename)

    try:
        file.save(file_path)
    except Exception as e:
        print("File save error:", e)
        flash("Could not save the uploaded file.", "error")
        return redirect(url_for("dashboard"))

    user_id = session["user"]["id"]
    file_type = filename.rsplit(".", 1)[1].lower()
    file_size = os.path.getsize(file_path)

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            """
            INSERT INTO Documents (user_id, filename, filepath, file_type, file_size)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, filename, file_path, file_type, file_size)
        )

        cursor.execute(
            """
            INSERT INTO uploaded_documents
            (user_id, original_filename, stored_filename, file_path, file_size, document_pin)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, file.filename, filename, file_path, file_size, document_pin)
        )

        db.commit()

        flash(f"'{filename}' uploaded successfully! Document PIN: {document_pin}", "success")

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print("Upload database error:", e)

        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        flash("Could not save the document.", "error")

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    return redirect(url_for("dashboard"))


@app.route("/document-pin", methods=["GET"])
def find_document_by_pin():
    pin = request.args.get("pin", "").strip().upper()

    if not pin:
        flash("Please enter a document PIN.", "error")
        return redirect(url_for("dashboard"))

    if not re.fullmatch(r"[A-Z0-9]{8,12}", pin):
        flash("Invalid document PIN format.", "error")
        return redirect(url_for("dashboard"))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                d.id AS document_id,
                ud.original_filename,
                ud.stored_filename,
                ud.file_path,
                ud.file_size
            FROM uploaded_documents AS ud
            LEFT JOIN Documents AS d
                ON d.user_id = ud.user_id
               AND d.filepath = ud.file_path
            WHERE ud.document_pin = %s
            LIMIT 1
            """,
            (pin,)
        )

        document = cursor.fetchone()

    except mysql.connector.Error as e:
        print("PIN search database error:", e)
        flash("Could not search for the document.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    if not document:
        flash("No document was found for that PIN.", "error")
        return redirect(url_for("dashboard"))

    file_path = Path(document["file_path"])

    if not file_path.exists():
        file_path = UPLOAD_FOLDER / document["stored_filename"]

    if not file_path.exists():
        flash("The document file no longer exists.", "error")
        return redirect(url_for("dashboard"))

    search_result = {
        "id": document.get("document_id"),
        "filename": document["original_filename"],
        "file_type": "pdf",
        "file_size": document["file_size"],
        "document_pin": pin,
    }

    user = get_current_user()
    documents = []
    current_plan = "free"

    if user:
        current_plan = get_user_plan(user["id"])
        db = None
        cursor = None
        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT
                    d.id, d.filename, d.filepath, d.file_type,
                    d.file_size, d.created_at, ud.document_pin
                FROM Documents AS d
                LEFT JOIN uploaded_documents AS ud
                    ON ud.user_id = d.user_id
                   AND ud.file_path = d.filepath
                WHERE d.user_id = %s
                ORDER BY d.created_at DESC
                LIMIT 20
                """,
                (user["id"],)
            )
            documents = cursor.fetchall()
        except mysql.connector.Error as e:
            print("PIN search dashboard error:", e)
        finally:
            if cursor:
                cursor.close()
            if db and db.is_connected():
                db.close()

    return render_template(
        "dashboard.html",
        user=user,
        uploads=documents,
        current_plan=current_plan,
        is_guest=(user is None),
        plans=PLANS,
        pin_search_result=search_result,
        searched_pin=pin,
    )


@app.route("/uploaded-document/<int:document_id>/view")
def view_uploaded_document(document_id):

    user = get_current_user()

    if user is None:
        return redirect(url_for("auth.login", next=request.path))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT filename, filepath, file_type, file_size
            FROM Documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, user["id"])
        )

        document = cursor.fetchone()

    except mysql.connector.Error as e:
        print("View document database error:", e)
        return "Could not load document.", 500

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    if not document:
        return "Document not found.", 404

    file_path = Path(document["filepath"])

    if not file_path.exists():
        file_path = UPLOAD_FOLDER / document["filename"]

    if not file_path.exists():
        return "The uploaded file no longer exists.", 404

    return send_file(
        str(file_path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=document["filename"]
    )


@app.route("/uploaded-document/<int:document_id>/download")
def download_uploaded_document(document_id):

    user = get_current_user()

    if user is None:
        return redirect(url_for("auth.login", next=request.path))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT filename, filepath, file_type, file_size
            FROM Documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, user["id"])
        )

        document = cursor.fetchone()

    except mysql.connector.Error as e:
        print("Download database error:", e)
        return "Could not download document.", 500

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    if not document:
        return "Document not found.", 404

    file_path = Path(document["filepath"])

    if not file_path.exists():
        file_path = UPLOAD_FOLDER / document["filename"]

    if not file_path.exists():
        return "The uploaded file no longer exists.", 404

    return send_file(
        str(file_path),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=document["filename"]
    )


@app.route("/uploaded-document/<int:document_id>/delete", methods=["POST"])
def delete_uploaded_document(document_id):

    user = get_current_user()

    if user is None:
        return redirect(url_for("auth.login", next=request.path))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT filename, filepath
            FROM Documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, user["id"])
        )

        document = cursor.fetchone()

        if not document:
            flash("Document not found.", "error")
            return redirect(url_for("dashboard"))

        cursor.execute(
            """
            DELETE FROM uploaded_documents
            WHERE user_id = %s AND file_path = %s
            """,
            (user["id"], document["filepath"])
        )

        cursor.execute(
            """
            DELETE FROM Documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, user["id"])
        )

        db.commit()

        file_path = Path(document["filepath"])

        if not file_path.exists():
            file_path = UPLOAD_FOLDER / document["filename"]

        try:
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            print("Physical file delete error:", e)

        flash("Document deleted successfully.", "success")

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print("Delete document database error:", e)
        flash("Could not delete the document.", "error")

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    return redirect(url_for("dashboard"))


@app.route("/document-pin/<string:pin>/preview")
def preview_document_by_pin(pin):
    pin = pin.strip().upper()
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT original_filename, file_path, stored_filename
            FROM uploaded_documents
            WHERE document_pin = %s
            LIMIT 1
            """,
            (pin,)
        )
        document = cursor.fetchone()
    except mysql.connector.Error as e:
        print("PIN preview database error:", e)
        return "Could not load document.", 500
    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    if not document:
        return "Document not found.", 404

    file_path = Path(document["file_path"])
    if not file_path.exists():
        file_path = UPLOAD_FOLDER / document["stored_filename"]

    if not file_path.exists():
        return "The uploaded file no longer exists.", 404

    return send_file(
        str(file_path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=document["original_filename"]
    )


@app.route("/document-pin/<string:pin>/download")
def download_document_by_pin(pin):
    pin = pin.strip().upper()
    db = None
    cursor = None
    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT original_filename, file_path, stored_filename
            FROM uploaded_documents
            WHERE document_pin = %s
            LIMIT 1
            """,
            (pin,)
        )
        document = cursor.fetchone()
    except mysql.connector.Error as e:
        print("PIN download database error:", e)
        return "Could not download document.", 500
    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    if not document:
        return "Document not found.", 404

    file_path = Path(document["file_path"])
    if not file_path.exists():
        file_path = UPLOAD_FOLDER / document["stored_filename"]

    if not file_path.exists():
        return "The uploaded file no longer exists.", 404

    return send_file(
        str(file_path),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=document["original_filename"]
    )


@app.route("/upgrade")
def upgrade():

    user = get_current_user()

    if user is None:
        flash("Please log in to upgrade your plan.", "info")
        return redirect(url_for("auth.login", next=request.path))

    current_plan = get_user_plan(user["id"])

    return render_template(
        "upgrade.html",
        user=user,
        current_plan=current_plan,
        plans=PLANS,
        is_guest=False,
        stats=STATS,
        comparisons_rows=COMPARISON_ROWS,
        testimonials=TESTIMONIALS,
        faqs=FAQS
    )


@app.route("/upgrade/checkout", methods=["POST"])
def upgrade_checkout():

    user = get_current_user()

    if user is None:
        flash("Please log in to upgrade your plan.", "info")
        return redirect(url_for("auth.login", next=url_for("upgrade")))

    plan_id = request.form.get("plan", "").strip().lower()
    billing_cycle = request.form.get("billing_cycle", "monthly").strip().lower()
    payment_method = request.form.get("payment_method", "card").strip().lower()

    if plan_id not in PLANS or plan_id == "free":
        flash("Please choose a valid paid plan.", "error")
        return redirect(url_for("upgrade"))

    if billing_cycle not in ("monthly", "yearly"):
        billing_cycle = "monthly"

    card_last4 = None

    if payment_method == "card":

        card_name = request.form.get("card_name", "").strip()
        card_number = request.form.get("card_number", "").strip()
        card_expiry = request.form.get("card_expiry", "").strip()
        card_cvv = request.form.get("card_cvv", "").strip()

        digits_only = card_number.replace(" ", "")

        if not card_name or len(digits_only) < 12 or not digits_only.isdigit():
            flash("Please enter valid card details.", "error")
            return redirect(url_for("upgrade"))

        if not card_expiry:
            flash("Please enter the card expiry date.", "error")
            return redirect(url_for("upgrade"))

        if not card_cvv.isdigit() or len(card_cvv) not in (3, 4):
            flash("Please enter a valid CVV.", "error")
            return redirect(url_for("upgrade"))

        card_last4 = digits_only[-4:]

    elif payment_method == "upi":

        upi_id = request.form.get("upi_id", "").strip()

        if "@" not in upi_id:
            flash("Please enter a valid UPI ID.", "error")
            return redirect(url_for("upgrade"))

    elif payment_method == "netbanking":

        bank = request.form.get("bank", "").strip()

        if not bank:
            flash("Please select your bank.", "error")
            return redirect(url_for("upgrade"))

    elif payment_method == "wallet":
        pass

    else:
        flash("Please choose a payment method.", "error")
        return redirect(url_for("upgrade"))

    amount = (
        PLANS[plan_id]["yearly_price"]
        if billing_cycle == "yearly"
        else PLANS[plan_id]["monthly_price"]
    )

    months_to_add = 12 if billing_cycle == "yearly" else 1

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            """
            INSERT INTO Payments
            (user_id, plan, billing_cycle, amount, currency, payment_method, card_last4, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user["id"], plan_id, billing_cycle, amount, "USD",
                payment_method, card_last4, "success",
            )
        )

        cursor.execute(
            """
            UPDATE Users
            SET plan = %s,
                plan_expires_at = DATE_ADD(NOW(), INTERVAL %s MONTH)
            WHERE id = %s
            """,
            (plan_id, months_to_add, user["id"])
        )

        db.commit()

        session["user"]["plan"] = plan_id

        flash(f"You're now on the {PLANS[plan_id]['name']} plan!", "success")

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print("Upgrade payment error:", e)
        flash("Could not process your upgrade. Please try again.", "error")

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    return redirect(url_for("dashboard"))


@app.route("/tools/<string:tool_name>")
def premium_tool_placeholder(tool_name):

    user = get_current_user()
    plan = get_user_plan(user["id"])

    if plan != "pro":
        flash("This tool is available on the Pro plan. Upgrade to unlock it.", "info")
        return redirect(url_for("upgrade"))

    flash(f"{tool_name.replace('-', ' ').title()} is coming soon for Pro members.", "info")
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("dashboard"))


@app.errorhandler(413)
def file_too_large(error):
    flash("File is too large. Maximum allowed size is 20 MB.", "error")
    return redirect(url_for("dashboard"))


@app.errorhandler(404)
def page_not_found(error):
    return "Page not found.", 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)