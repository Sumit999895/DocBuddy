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

from flask import send_file
from werkzeug.utils import secure_filename
from PIL import Image
from io import BytesIO


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()


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
# REGISTER PDF TO JPG BLUEPRINT
# ============================================================

app.register_blueprint(
    pdf_to_jpg_bp
)

# ============================================================
# REGISTER JPG TO PDF BLUEPRINT
# ============================================================

app.register_blueprint(jpg_to_pdf_bp)

# ============================================================
# REGISTER PDF TO DOCX BLUEPRINT
# ============================================================

app.register_blueprint(pdf_to_docx_bp)

# ============================================================
# REGISTER JPG TO PNG BLUEPRINT
# ============================================================

app.register_blueprint(jpg_to_png_bp)

# ============================================================
# REGISTER PNG TO JPG BLUEPRINT
# ============================================================

app.register_blueprint(png_to_jpg_bp)

# ============================================================
# REGISTER CROP IMAGE BLUEPRINT
# ============================================================

app.register_blueprint(crop_image_bp)


# ============================================================
# RESIZE IMAGE BLUEPRINT
# ============================================================

app.register_blueprint(resize_bp)

# ============================================================
# Edit PDF BLUEPRINT
# ============================================================

app.register_blueprint(edit_pdf_bp)

# ============================================================
# Compress PDF BLUEPRINT
# ============================================================

app.register_blueprint(compress_pdf_bp)

# ============================================================
# Compress PDF BLUEPRINT
# ============================================================

app.register_blueprint(protect_unlock_bp)

# ============================================================
# SCAN DOCUMENT BLUEPRINT
# ============================================================

app.register_blueprint(scan_bp)

# ============================================================
# CREATE DOCUMENT BLUEPRINT
# ============================================================

app.register_blueprint(create_bp)

# =========================================================
# REGISTER PAGE FORMAT
# =========================================================

app.register_blueprint(page_format_bp)

# =========================================================
# REGISTER OCR and text
# =========================================================

app.register_blueprint(ocr_text_bp)

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

# ============================================================
# PRICING PAGE CONTENT (stats / comparison / testimonials / FAQ)
#
# Pure display data for the /upgrade page. Only 2 columns
# (Free, Pro) since that's the current plan lineup — add a 3rd
# tuple element to each COMPARISON_ROWS row (and a 3rd <td> in
# the template) if a Business tier is added later.
# ============================================================

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
#
# The dashboard itself is public (guests can browse and use the
# basic conversion tools). These specific endpoints require a
# logged-in session, and premium tools additionally require the
# Pro plan. Centralizing this here means individual routes -
# including ones in the conversion blueprints - don't each need
# their own login check.
# ============================================================

LOGIN_REQUIRED_ENDPOINTS = {
    "upload_document",
    "view_uploaded_document",
    "download_uploaded_document",
    "delete_uploaded_document",
    "upgrade",
    "upgrade_checkout",
    "premium_tool_placeholder",
    # Heavier / storage-backed conversions require an account;
    # the quick image conversions stay open to guests.
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
                "login",
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


# ============================================================
# CURRENT USER
# ============================================================

def get_current_user():

    if "user" not in session:

        return None

    user = session.get(
        "user"
    )

    if not isinstance(
        user,
        dict
    ):
        return None

    return user


# ============================================================
# CURRENT PLAN (reads live from the database so an upgrade
# takes effect immediately, even mid-session)
# ============================================================

def get_user_plan(user_id):

    db = None
    cursor = None

    plan = "free"

    try:

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        cursor.execute(
            """
            SELECT plan, plan_expires_at
            FROM Users
            WHERE id = %s
            """,
            (
                user_id,
            )
        )

        row = cursor.fetchone()

        if row:

            plan = row.get("plan") or "free"
            expires_at = row.get("plan_expires_at")

            # A paid plan that has lapsed behaves like free until
            # the user renews - no separate cron/cleanup needed.
            if (
                plan != "free"
                and expires_at
                and expires_at < datetime.utcnow()
            ):
                plan = "free"

    except mysql.connector.Error as e:

        print(
            "get_user_plan error:",
            e
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    return plan


# ============================================================
# HOME
#
# The site no longer forces a login wall - "/" simply shows the
# dashboard. Guests get a limited view (see the dashboard route
# below); logged-in users get their full workspace.
# ============================================================

@app.route("/")
def home():

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# DASHBOARD (public - works for guests and logged-in users)
# ============================================================

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

            cursor = db.cursor(
                dictionary=True
            )

            cursor.execute(
                """
                SELECT plan
                FROM Users
                WHERE id = %s
                """,
                (
                    user["id"],
                )
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
                (
                    user["id"],
                )
            )

            documents = cursor.fetchall()

        except mysql.connector.Error as e:

            print(
                "Dashboard database error:",
                e
            )

            flash(
                "Could not load your documents.",
                "error"
            )

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
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    # --------------------------------------------------------
    # SHOW LOGIN PAGE
    # --------------------------------------------------------

    if request.method == "GET":

        return render_template(
            "login.html",
            next=request.args.get("next", "")
        )

    email = request.form.get(
        "email",
        ""
    ).strip()

    password = request.form.get(
        "password",
        ""
    )

    next_url = (
        request.form.get("next")
        or request.args.get("next")
        or url_for("dashboard")
    )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not email or not password:

        flash(
            "Email and password are required.",
            "error"
        )

        return redirect(
            url_for("login", next=next_url)
        )

    db = None
    cursor = None

    try:

        # ----------------------------------------------------
        # DATABASE CONNECTION
        # ----------------------------------------------------

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # FIND USER
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                name,
                email,
                password,
                plan
            FROM Users
            WHERE email = %s
            """,
            (
                email,
            )
        )

        user = cursor.fetchone()

        # ----------------------------------------------------
        # USER NOT FOUND
        # ----------------------------------------------------

        if not user:

            flash(
                "Invalid email or password.",
                "error"
            )

            return redirect(
                url_for("login", next=next_url)
            )

        # ----------------------------------------------------
        # CHECK PASSWORD
        # ----------------------------------------------------

        try:

            stored_password = user["password"]

            if isinstance(
                stored_password,
                str
            ):

                stored_password = (
                    stored_password.encode(
                        "utf-8"
                    )
                )

            password_match = bcrypt.checkpw(
                password.encode("utf-8"),
                stored_password
            )

        except Exception as password_error:

            print(
                "Password verification error:",
                password_error
            )

            flash(
                "Unable to verify password.",
                "error"
            )

            return redirect(
                url_for("login", next=next_url)
            )

        # ----------------------------------------------------
        # WRONG PASSWORD
        # ----------------------------------------------------

        if not password_match:

            flash(
                "Invalid email or password.",
                "error"
            )

            return redirect(
                url_for("login", next=next_url)
            )

        # ----------------------------------------------------
        # LOGIN SUCCESS
        # ----------------------------------------------------

        session["user"] = {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "plan": user.get("plan") or "free",
        }

        session["user_id"] = user["id"]
        session["username"] = user["name"]
        session["email"] = user["email"]

        session.permanent = False

        print(
            "LOGIN SUCCESS:",
            session["user"]
        )

        return redirect(next_url)

    except mysql.connector.Error as e:

        print(
            "Database error during login:",
            e
        )

        flash(
            "A database error occurred. Please try again.",
            "error"
        )

        return redirect(
            url_for("login", next=next_url)
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()


# ============================================================
# REGISTER
# ============================================================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if "user" in session:

        return redirect(
            url_for("dashboard")
        )

    # --------------------------------------------------------
    # SHOW REGISTER PAGE
    # --------------------------------------------------------

    if request.method == "GET":

        return render_template(
            "register.html",
            next=request.args.get("next", "")
        )

    name = request.form.get(
        "name",
        ""
    ).strip()

    email = request.form.get(
        "email",
        ""
    ).strip()

    password = request.form.get(
        "password",
        ""
    )

    confirm_password = request.form.get(
        "confirm_password",
        ""
    )

    next_url = (
        request.form.get("next")
        or request.args.get("next")
        or ""
    )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not name or not email or not password:

        flash(
            "All fields are required.",
            "error"
        )

        return redirect(
            url_for("register", next=next_url)
        )

    if password != confirm_password:

        flash(
            "Passwords do not match.",
            "error"
        )

        return redirect(
            url_for("register", next=next_url)
        )

    if len(password) < 6:

        flash(
            "Password must be at least 6 characters.",
            "error"
        )

        return redirect(
            url_for("register", next=next_url)
        )

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # CHECK EXISTING EMAIL
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM Users
            WHERE email = %s
            """,
            (
                email,
            )
        )

        existing_user = cursor.fetchone()

        if existing_user:

            flash(
                "An account with this email already exists.",
                "error"
            )

            return redirect(
                url_for("register", next=next_url)
            )

        # ----------------------------------------------------
        # HASH PASSWORD
        # ----------------------------------------------------

        hashed_password = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt()
        ).decode("utf-8")

        # ----------------------------------------------------
        # CREATE USER (plan defaults to 'free' via the DB column)
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO Users
            (
                name,
                email,
                password
            )
            VALUES
            (
                %s,
                %s,
                %s
            )
            """,
            (
                name,
                email,
                hashed_password
            )
        )

        db.commit()

        flash(
            "Registration successful. Please login.",
            "success"
        )

        return redirect(
            url_for("login", next=next_url)
        )

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print(
            "Registration database error:",
            e
        )

        flash(
            "Could not create your account.",
            "error"
        )

        return redirect(
            url_for("register", next=next_url)
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()


# ============================================================
# UPLOAD DOCUMENT (login required - see LOGIN_REQUIRED_ENDPOINTS)
# ============================================================

@app.route(
    "/upload",
    methods=["POST"]
)
def upload_document():

    # --------------------------------------------------------
    # CHECK LOGIN
    # --------------------------------------------------------

    if "user" not in session:

        flash(
            "Please log in first.",
            "error"
        )

        return redirect(
            url_for("login", next=url_for("dashboard"))
        )

    # --------------------------------------------------------
    # CHECK FILE
    # --------------------------------------------------------

    if "file" not in request.files:

        flash(
            "No file selected.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    file = request.files["file"]

    if not file or file.filename == "":

        flash(
            "No file selected.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    # --------------------------------------------------------
    # CHECK FILE TYPE
    # --------------------------------------------------------

    if not allowed_file(file.filename):

        flash(
            "Only PDF files are allowed.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    # --------------------------------------------------------
    # SECURE FILE NAME
    # --------------------------------------------------------

    filename = secure_filename(
        file.filename
    )

    if not filename:

        flash(
            "Invalid file name.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    # --------------------------------------------------------
    # GENERATE UNIQUE DOCUMENT PIN
    # --------------------------------------------------------

    document_pin = generate_document_pin()

    # --------------------------------------------------------
    # CREATE UNIQUE FILE NAME
    # --------------------------------------------------------

    base_name, extension = os.path.splitext(
        filename
    )

    final_filename = filename

    counter = 1

    while os.path.exists(
        os.path.join(
            UPLOAD_FOLDER,
            final_filename
        )
    ):

        final_filename = (
            f"{base_name}_{counter}{extension}"
        )

        counter += 1

    filename = final_filename

    # --------------------------------------------------------
    # FILE PATH
    # --------------------------------------------------------

    file_path = os.path.join(
        UPLOAD_FOLDER,
        filename
    )

    # --------------------------------------------------------
    # SAVE PHYSICAL FILE
    # --------------------------------------------------------

    try:

        file.save(
            file_path
        )

    except Exception as e:

        print(
            "File save error:",
            e
        )

        flash(
            "Could not save the uploaded file.",
            "error"
        )

        return redirect(
            url_for("dashboard")
        )

    # --------------------------------------------------------
    # FILE INFORMATION
    # --------------------------------------------------------

    user_id = session["user"]["id"]

    file_type = (
        filename
        .rsplit(".", 1)[1]
        .lower()
    )

    file_size = os.path.getsize(
        file_path
    )

    # --------------------------------------------------------
    # SAVE INTO Documents TABLE
    # --------------------------------------------------------

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor()

        cursor.execute(
            """
            INSERT INTO Documents
            (
                user_id,
                filename,
                filepath,
                file_type,
                file_size
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                user_id,
                filename,
                file_path,
                file_type,
                file_size
            )
        )

        # Save the same uploaded document and its PIN in
        # uploaded_documents. This table already contains the
        # document_pin column and has a UNIQUE constraint on it.
        cursor.execute(
            """
            INSERT INTO uploaded_documents
            (
                user_id,
                original_filename,
                stored_filename,
                file_path,
                file_size,
                document_pin
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                user_id,
                file.filename,
                filename,
                file_path,
                file_size,
                document_pin
            )
        )

        db.commit()

        print(
            "================================"
        )

        print(
            "DOCUMENT UPLOADED"
        )

        print(
            "User ID:",
            user_id
        )

        print(
            "Filename:",
            filename
        )

        print(
            "File path:",
            file_path
        )

        print(
            "================================"
        )

        flash(
            f"'{filename}' uploaded successfully! Document PIN: {document_pin}",
            "success"
        )

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print(
            "Upload database error:",
            e
        )

        # Remove file if database insertion failed

        if os.path.isfile(file_path):

            try:
                os.remove(file_path)

            except OSError:
                pass

        flash(
            "Could not save the document.",
            "error"
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    # --------------------------------------------------------
    # RETURN TO DASHBOARD
    # --------------------------------------------------------

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# FIND DOCUMENT BY PIN
# ============================================================

@app.route("/document-pin", methods=["GET"])
def find_document_by_pin():
    pin = request.args.get("pin", "").strip().upper()

    if not pin:
        flash(
            "Please enter a document PIN.",
            "error"
        )
        return redirect(url_for("dashboard"))

    if not re.fullmatch(r"[A-Z0-9]{8,12}", pin):
        flash(
            "Invalid document PIN format.",
            "error"
        )
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
        print(
            "PIN search database error:",
            e
        )
        flash(
            "Could not search for the document.",
            "error"
        )
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    if not document:
        flash(
            "No document was found for that PIN.",
            "error"
        )
        return redirect(url_for("dashboard"))

    file_path = Path(document["file_path"])

    if not file_path.exists():
        file_path = UPLOAD_FOLDER / document["stored_filename"]

    if not file_path.exists():
        flash(
            "The document file no longer exists.",
            "error"
        )
        return redirect(url_for("dashboard"))

    # Do not open or download the document automatically.
    # Return to the dashboard and show the matching document
    # with explicit Preview and Download options.
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


# ============================================================
# VIEW RECENT UPLOAD
# ============================================================

@app.route(
    "/uploaded-document/<int:document_id>/view"
)
def view_uploaded_document(document_id):

    user = get_current_user()

    if user is None:

        return redirect(
            url_for("login", next=request.path)
        )

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        cursor.execute(
            """
            SELECT
                filename,
                filepath,
                file_type,
                file_size
            FROM Documents
            WHERE id = %s
              AND user_id = %s
            """,
            (
                document_id,
                user["id"]
            )
        )

        document = cursor.fetchone()

    except mysql.connector.Error as e:

        print(
            "View document database error:",
            e
        )

        return (
            "Could not load document.",
            500
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    if not document:

        return (
            "Document not found.",
            404
        )

    file_path = Path(
        document["filepath"]
    )

    if not file_path.exists():

        file_path = (
            UPLOAD_FOLDER
            / document["filename"]
        )

    if not file_path.exists():

        return (
            "The uploaded file no longer exists.",
            404
        )

    return send_file(
        str(file_path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=document[
            "filename"
        ]
    )


# ============================================================
# DOWNLOAD RECENT UPLOAD
# ============================================================

@app.route(
    "/uploaded-document/<int:document_id>/download"
)
def download_uploaded_document(document_id):

    user = get_current_user()

    if user is None:

        return redirect(
            url_for("login", next=request.path)
        )

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        cursor.execute(
            """
            SELECT
                filename,
                filepath,
                file_type,
                file_size
            FROM Documents
            WHERE id = %s
              AND user_id = %s
            """,
            (
                document_id,
                user["id"]
            )
        )

        document = cursor.fetchone()

    except mysql.connector.Error as e:

        print(
            "Download database error:",
            e
        )

        return (
            "Could not download document.",
            500
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    if not document:

        return (
            "Document not found.",
            404
        )

    file_path = Path(
        document["filepath"]
    )

    if not file_path.exists():

        file_path = (
            UPLOAD_FOLDER
            / document["filename"]
        )

    if not file_path.exists():

        return (
            "The uploaded file no longer exists.",
            404
        )

    return send_file(
        str(file_path),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=document[
            "filename"
        ]
    )


# ============================================================
# DELETE RECENT UPLOAD
# ============================================================

@app.route(
    "/uploaded-document/<int:document_id>/delete",
    methods=["POST"]
)
def delete_uploaded_document(document_id):

    user = get_current_user()

    if user is None:

        return redirect(
            url_for("login", next=request.path)
        )

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # FIND DOCUMENT
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                filename,
                filepath
            FROM Documents
            WHERE id = %s
              AND user_id = %s
            """,
            (
                document_id,
                user["id"]
            )
        )

        document = cursor.fetchone()

        if not document:

            flash(
                "Document not found.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )

        # ----------------------------------------------------
        # DELETE PIN RECORD
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM uploaded_documents
            WHERE user_id = %s
              AND file_path = %s
            """,
            (
                user["id"],
                document["filepath"]
            )
        )

        # ----------------------------------------------------
        # DELETE DATABASE RECORD
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM Documents
            WHERE id = %s
              AND user_id = %s
            """,
            (
                document_id,
                user["id"]
            )
        )

        db.commit()

        # ----------------------------------------------------
        # DELETE PHYSICAL FILE
        # ----------------------------------------------------

        file_path = Path(
            document["filepath"]
        )

        if not file_path.exists():

            file_path = (
                UPLOAD_FOLDER
                / document["filename"]
            )

        try:

            if file_path.exists():
                file_path.unlink()

        except Exception as e:

            print(
                "Physical file delete error:",
                e
            )

        flash(
            "Document deleted successfully.",
            "success"
        )

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print(
            "Delete document database error:",
            e
        )

        flash(
            "Could not delete the document.",
            "error"
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# PUBLIC PREVIEW BY DOCUMENT PIN
# ============================================================

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


# ============================================================
# PUBLIC DOWNLOAD BY DOCUMENT PIN
# ============================================================

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


# ============================================================
# UPGRADE PLAN - PRICING PAGE (login required)
# ============================================================

@app.route("/upgrade")
def upgrade():

    user = get_current_user()

    if user is None:

        flash(
            "Please log in to upgrade your plan.",
            "info"
        )

        return redirect(
            url_for("login", next=request.path)
        )

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


# ============================================================
# UPGRADE PLAN - CHECKOUT (login required)
#
# DEMO PAYMENT FLOW: this simulates a successful charge so the
# rest of the app (plan gating, Payments history) has something
# real to work against. No card number, expiry, or CVV is ever
# written to the database - only the last 4 digits, for display
# purposes, are kept. Before going live, replace the block below
# with a call to a real processor (Stripe, Razorpay, etc.) and
# only write the Payments row once that processor confirms the
# charge succeeded.
# ============================================================

@app.route(
    "/upgrade/checkout",
    methods=["POST"]
)
def upgrade_checkout():

    user = get_current_user()

    if user is None:

        flash(
            "Please log in to upgrade your plan.",
            "info"
        )

        return redirect(
            url_for("login", next=url_for("upgrade"))
        )

    plan_id = request.form.get("plan","").strip().lower()

    billing_cycle = request.form.get("billing_cycle","monthly").strip().lower()

    card_name = request.form.get("card_name","").strip().lower()

    card_number = request.form.get("card_number","").strip().lower()

    card_expiry = request.form.get("card_expiry","").strip().lower()

    card_cvv = request.form.get("card_cvv","").strip().lower()
    payment_method = request.form.get("payment_method", "card").strip().lower()

    if plan_id not in PLANS or plan_id == "free":
        flash("Please choose a valid paid plan.", "error")
        return redirect(url_for("upgrade"))

    if billing_cycle not in ("monthly", "yearly"):
        billing_cycle = "monthly"

    card_last4 = None

    # --------------------------------------------------------
    # VALIDATE PLAN
    # --------------------------------------------------------

    if plan_id not in PLANS or plan_id == "free":

        flash(
            "Please choose a valid paid plan.",
            "error"
        )

        return redirect(
            url_for("upgrade")
        )

    if billing_cycle not in ("monthly", "yearly"):
        billing_cycle = "monthly"

    card_last4 = None
       # ----------------------------------------------------------
    # PER-METHOD VALIDATION (demo-level checks only — replace
    # this whole block with your real processor's response once
    # Stripe/Razorpay is wired in)
    # ----------------------------------------------------------

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
        pass  # no extra fields collected from this panel yet

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
            (
                user_id, plan, billing_cycle, amount, currency,
                payment_method, card_last4, status
            )
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user["id"],
                plan_id,
                billing_cycle,
                amount,
                "USD",
                payment_method,
                card_last4,
                "success",
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
    # --------------------------------------------------------
    # VALIDATE CARD (demo-level checks only)
    # --------------------------------------------------------

    digits_only = card_number.replace(
        " ",
        ""
    )

    if (
        not card_name
        or len(digits_only) < 12
        or not digits_only.isdigit()
    ):

        flash(
            "Please enter valid card details.",
            "error"
        )

        return redirect(
            url_for("upgrade")
        )

    if not card_expiry:

        flash(
            "Please enter the card expiry date.",
            "error"
        )

        return redirect(
            url_for("upgrade")
        )

    if not card_cvv.isdigit() or len(card_cvv) not in (3, 4):

        flash(
            "Please enter a valid CVV.",
            "error"
        )

        return redirect(
            url_for("upgrade")
        )

    amount = (
        PLANS[plan_id]["yearly_price"]
        if billing_cycle == "yearly"
        else PLANS[plan_id]["monthly_price"]
    )

    card_last4 = digits_only[-4:]
    months_to_add = 12 if billing_cycle == "yearly" else 1

    db = None
    cursor = None

    try:

        db = get_db_connection()

        cursor = db.cursor()

        # ----------------------------------------------------
        # RECORD THE PAYMENT
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO Payments
            (
                user_id,
                plan,
                billing_cycle,
                amount,
                currency,
                payment_method,
                card_last4,
                status
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                user["id"],
                plan_id,
                billing_cycle,
                amount,
                "USD",
                "card",
                card_last4,
                "success",
            )
        )

        # ----------------------------------------------------
        # UPGRADE THE USER'S PLAN
        # ----------------------------------------------------

        cursor.execute(
            """
            UPDATE Users
            SET plan = %s,
                plan_expires_at = DATE_ADD(NOW(), INTERVAL %s MONTH)
            WHERE id = %s
            """,
            (
                plan_id,
                months_to_add,
                user["id"],
            )
        )

        db.commit()

        session["user"]["plan"] = plan_id

        flash(
            f"You're now on the {PLANS[plan_id]['name']} plan!",
            "success"
        )

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print(
            "Upgrade payment error:",
            e
        )

        flash(
            "Could not process your upgrade. Please try again.",
            "error"
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# PREMIUM TOOL PLACEHOLDER (Watermark / Protect / e-Sign)
#
# These tools aren't built yet, but the gating is real: guests
# are sent to log in, free users are sent to /upgrade, and Pro
# users get a "coming soon" message instead of a dead link.
# ============================================================

@app.route("/tools/<string:tool_name>")
def premium_tool_placeholder(tool_name):

    user = get_current_user()

    plan = get_user_plan(user["id"])

    if plan != "pro":

        flash(
            "This tool is available on the Pro plan. Upgrade to unlock it.",
            "info"
        )

        return redirect(
            url_for("upgrade")
        )

    flash(
        f"{tool_name.replace('-', ' ').title()} is coming soon for Pro members.",
        "info"
    )

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# ERROR HANDLER - FILE TOO LARGE
# ============================================================

@app.errorhandler(413)
def file_too_large(error):

    flash(
        "File is too large. Maximum allowed size is 20 MB.",
        "error"
    )

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# ERROR HANDLER - PAGE NOT FOUND
# ============================================================

@app.errorhandler(404)
def page_not_found(error):

    return (
        "Page not found.",
        404
    )


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )