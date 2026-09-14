import hashlib
import secrets
import os
import shutil
from pathlib import Path
from datetime import datetime, timedelta
import bcrypt
import mysql.connector

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from database import get_db_connection
from config import UPLOAD_FOLDER, CONVERSION_FOLDER
from services.validators import (
    check_email,
    check_phone,
    check_password_strength,
)
from services.otp_service import (
    generate_and_store_otp,
    verify_otp,
    seconds_until_resend_allowed,
)
from services.mail_service import send_otp_email


auth_bp = Blueprint("auth", __name__)
oauth = OAuth()

REGISTRATION_OTP_LENGTH = 6
REGISTRATION_OTP_TTL_MINUTES = 10
REGISTRATION_MAX_ATTEMPTS = 5
REGISTRATION_RESEND_COOLDOWN = 45


def init_oauth(app):
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=app.config.get("GOOGLE_CLIENT_ID"),
        client_secret=app.config.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _get_user_by_email(cursor, email):
    cursor.execute("SELECT * FROM Users WHERE email = %s", (email,))
    return cursor.fetchone()


def _get_user_by_id(cursor, user_id):
    cursor.execute("SELECT * FROM Users WHERE id = %s", (user_id,))
    return cursor.fetchone()


def _start_session(user):
    session["user"] = {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "plan": user.get("plan") or "free",
    }
    session["user_id"] = user["id"]
    session.permanent = False


def _hash_registration_otp(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_registration_otp():
    return "".join(
        secrets.choice("0123456789")
        for _ in range(REGISTRATION_OTP_LENGTH)
    )


def _safe_next_url(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return ""


# ============================================================
# REGISTER
# ============================================================

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if "user" in session:
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        return render_template(
            "register.html",
            next=request.args.get("next", ""),
        )

    name = request.form.get("name", "").strip()
    email_raw = request.form.get("email", "").strip()
    phone_raw = request.form.get("phone", "").strip()
    country_dial_code = request.form.get(
        "country_dial_code", "US"
    ).strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    next_url = _safe_next_url(
        request.form.get("next")
        or request.args.get("next")
        or ""
    )

    def bounce(message):
        flash(message, "error")
        return redirect(
            url_for("auth.register", next=next_url)
        )

    if not name:
        return bounce("Please enter your name.")

    if len(name) > 100:
        return bounce("Name is too long.")

    email_ok, email_result = check_email(email_raw)
    if not email_ok:
        return bounce(email_result)
    email = email_result

    phone_ok, phone_result = check_phone(
        phone_raw,
        default_region=country_dial_code,
    )
    if not phone_ok:
        return bounce(phone_result)
    phone = phone_result

    if password != confirm_password:
        return bounce("Passwords do not match.")

    strong_ok, strong_error = check_password_strength(password)
    if not strong_ok:
        return bounce(strong_error)

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        if _get_user_by_email(cursor, email):
            return bounce(
                "An account with this email already exists. "
                "Please log in instead."
            )

        # Remove an older unfinished registration for this email.
        cursor.execute(
            "DELETE FROM pending_registrations WHERE email = %s",
            (email,),
        )

        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        otp_code = _generate_registration_otp()
        otp_hash = _hash_registration_otp(otp_code)
        otp_expires_at = datetime.utcnow() + timedelta(
            minutes=REGISTRATION_OTP_TTL_MINUTES
        )
        registration_token = secrets.token_hex(32)

        # IMPORTANT:
        # No Users row is created here.
        cursor.execute(
            """
            INSERT INTO pending_registrations
            (
                registration_token,
                name,
                email,
                phone,
                password_hash,
                otp_hash,
                otp_expires_at,
                otp_attempts,
                last_otp_sent_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, NOW())
            """,
            (
                registration_token,
                name,
                email,
                phone,
                password_hash,
                otp_hash,
                otp_expires_at,
            ),
        )
        db.commit()

    except mysql.connector.Error as e:
        if db:
            db.rollback()
        print("Pending registration database error:", e)
        return bounce(
            "Registration could not be started. Please try again."
        )

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    try:
        send_otp_email(
            email,
            otp_code,
            purpose="verify_account",
        )
    except Exception as e:
        print("Registration OTP email error:", e)

        db = None
        cursor = None
        try:
            db = get_db_connection()
            cursor = db.cursor()
            cursor.execute(
                """
                DELETE FROM pending_registrations
                WHERE registration_token = %s
                """,
                (registration_token,),
            )
            db.commit()
        except Exception as cleanup_error:
            print("Pending registration cleanup error:", cleanup_error)
        finally:
            if cursor:
                cursor.close()
            if db and db.is_connected():
                db.close()

        return bounce(
            "We couldn't send the verification code. "
            "Please try again."
        )

    session["pending_registration_token"] = registration_token
    session["pending_verification_next"] = next_url

    flash(
        f"We sent a 6-digit verification code to {email}.",
        "success",
    )
    return redirect(url_for("auth.verify_otp_route"))


# ============================================================
# VERIFY REGISTRATION OTP
# ============================================================

@auth_bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp_route():
    registration_token = session.get(
        "pending_registration_token"
    )

    if not registration_token:
        flash(
            "Your registration session has expired. Please register again.",
            "info",
        )
        return redirect(url_for("auth.register"))

    if request.method == "GET":
        return render_template(
            "verify_otp.html",
            purpose="verify_account",
        )

    submitted_code = request.form.get("otp", "").strip()

    if not submitted_code:
        submitted_code = "".join(
            request.form.get(f"digit{i}", "")
            for i in range(1, 7)
        ).strip()

    submitted_code = "".join(
        c for c in submitted_code if c.isdigit()
    )

    if len(submitted_code) != 6:
        flash(
            "Please enter the complete 6-digit code.",
            "error",
        )
        return redirect(url_for("auth.verify_otp_route"))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM pending_registrations
            WHERE registration_token = %s
            LIMIT 1
            """,
            (registration_token,),
        )
        pending = cursor.fetchone()

        if not pending:
            session.pop("pending_registration_token", None)
            flash(
                "This registration has expired. Please register again.",
                "error",
            )
            return redirect(url_for("auth.register"))

        if pending["otp_expires_at"] < datetime.utcnow():
            cursor.execute(
                "DELETE FROM pending_registrations WHERE id = %s",
                (pending["id"],),
            )
            db.commit()

            session.pop("pending_registration_token", None)
            flash(
                "That verification code has expired. Please register again.",
                "error",
            )
            return redirect(url_for("auth.register"))

        if pending["otp_attempts"] >= REGISTRATION_MAX_ATTEMPTS:
            flash(
                "Too many incorrect attempts. Please request a new code.",
                "error",
            )
            return redirect(url_for("auth.verify_otp_route"))

        submitted_hash = _hash_registration_otp(submitted_code)

        if submitted_hash != pending["otp_hash"]:
            cursor.execute(
                """
                UPDATE pending_registrations
                SET otp_attempts = otp_attempts + 1
                WHERE id = %s
                """,
                (pending["id"],),
            )
            db.commit()

            remaining = max(
                0,
                REGISTRATION_MAX_ATTEMPTS
                - pending["otp_attempts"]
                - 1,
            )

            flash(
                f"Incorrect verification code. "
                f"{remaining} attempt(s) remaining.",
                "error",
            )
            return redirect(url_for("auth.verify_otp_route"))

        # Check again immediately before creating the account.
        existing_user = _get_user_by_email(
            cursor,
            pending["email"],
        )

        if existing_user:
            cursor.execute(
                "DELETE FROM pending_registrations WHERE id = %s",
                (pending["id"],),
            )
            db.commit()

            session.pop("pending_registration_token", None)
            flash(
                "An account with this email already exists.",
                "error",
            )
            return redirect(url_for("auth.login"))

        # THIS is the first Users INSERT.
        cursor.execute(
            """
            INSERT INTO Users
            (
                name,
                email,
                phone,
                password,
                login_method,
                account_status,
                auth_provider
            )
            VALUES (%s, %s, %s, %s, 'email', 'active', 'email')
            """,
            (
                pending["name"],
                pending["email"],
                pending["phone"],
                pending["password_hash"],
            ),
        )

        user_id = cursor.lastrowid

        cursor.execute(
            "DELETE FROM pending_registrations WHERE id = %s",
            (pending["id"],),
        )

        db.commit()

        user = _get_user_by_id(cursor, user_id)

    except mysql.connector.Error as e:
        if db:
            db.rollback()

        print("OTP verification database error:", e)
        flash(
            "Verification failed because of a database error. Please try again.",
            "error",
        )
        return redirect(url_for("auth.verify_otp_route"))

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    next_url = (
        session.pop("pending_verification_next", "")
        or url_for("dashboard")
    )

    session.pop("pending_registration_token", None)
    _start_session(user)

    flash(
        "Email verified successfully. Your DocBuddy account has been created!",
        "success",
    )
    return redirect(next_url)


# ============================================================
# RESEND OTP
# ============================================================

@auth_bp.route("/resend-otp", methods=["POST"])
def resend_otp():
    registration_token = session.get(
        "pending_registration_token"
    )

    if registration_token:
        db = None
        cursor = None

        try:
            db = get_db_connection()
            cursor = db.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT *
                FROM pending_registrations
                WHERE registration_token = %s
                LIMIT 1
                """,
                (registration_token,),
            )
            pending = cursor.fetchone()

            if not pending:
                session.pop("pending_registration_token", None)
                flash(
                    "Your registration session has expired. Please register again.",
                    "error",
                )
                return redirect(url_for("auth.register"))

            elapsed = (
                datetime.utcnow()
                - pending["last_otp_sent_at"]
            ).total_seconds()

            remaining = max(
                0,
                int(REGISTRATION_RESEND_COOLDOWN - elapsed),
            )

            if remaining > 0:
                flash(
                    f"Please wait {remaining} seconds before requesting another code.",
                    "error",
                )
                return redirect(url_for("auth.verify_otp_route"))

            otp_code = _generate_registration_otp()
            otp_hash = _hash_registration_otp(otp_code)
            expires_at = datetime.utcnow() + timedelta(
                minutes=REGISTRATION_OTP_TTL_MINUTES
            )

            cursor.execute(
                """
                UPDATE pending_registrations
                SET otp_hash = %s,
                    otp_expires_at = %s,
                    otp_attempts = 0,
                    last_otp_sent_at = NOW()
                WHERE id = %s
                """,
                (
                    otp_hash,
                    expires_at,
                    pending["id"],
                ),
            )
            db.commit()

            email = pending["email"]

        except mysql.connector.Error as e:
            if db:
                db.rollback()
            print("Resend OTP database error:", e)
            flash(
                "Could not generate a new code.",
                "error",
            )
            return redirect(url_for("auth.verify_otp_route"))

        finally:
            if cursor:
                cursor.close()
            if db and db.is_connected():
                db.close()

        try:
            send_otp_email(
                email,
                otp_code,
                purpose="verify_account",
            )
        except Exception as e:
            print("Resend OTP email error:", e)
            flash(
                "The new code could not be sent. Please try again.",
                "error",
            )
            return redirect(url_for("auth.verify_otp_route"))

        flash(
            "A new verification code has been sent.",
            "success",
        )
        return redirect(url_for("auth.verify_otp_route"))

    # Existing-user password reset resend
    user_id = session.get("pending_reset_user_id")

    if not user_id:
        flash(
            "Nothing to resend. Please start again.",
            "info",
        )
        return redirect(url_for("auth.register"))

    wait = seconds_until_resend_allowed(
        user_id,
        "password_reset",
    )

    if wait > 0:
        flash(
            f"Please wait {wait}s before requesting another code.",
            "error",
        )
        return redirect(url_for("auth.verify_reset_otp"))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        user = _get_user_by_id(cursor, user_id)
    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    if not user:
        flash("Account not found.", "error")
        return redirect(url_for("auth.forgot_password"))

    code = generate_and_store_otp(
        user_id,
        channel="email",
        purpose="password_reset",
    )
    send_otp_email(
        user["email"],
        code,
        purpose="password_reset",
    )

    flash("A new code has been sent.", "success")
    return redirect(url_for("auth.verify_reset_otp"))


# ============================================================
# LOGIN
# ============================================================

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template(
            "login.html",
            next=request.args.get("next", ""),
        )

    email_raw = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    next_url = _safe_next_url(
        request.form.get("next")
        or request.args.get("next")
        or ""
    )

    if not email_raw or not password:
        flash(
            "Email and password are required.",
            "error",
        )
        return redirect(
            url_for("auth.login", next=next_url)
        )

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        user = _get_user_by_email(
            cursor,
            email_raw,
        )

        if not user or not user.get("password"):
            flash(
                "Invalid email or password.",
                "error",
            )
            return redirect(
                url_for("auth.login", next=next_url)
            )

        stored = user["password"]
        if isinstance(stored, str):
            stored = stored.encode("utf-8")

        if not bcrypt.checkpw(
            password.encode("utf-8"),
            stored,
        ):
            flash(
                "Invalid email or password.",
                "error",
            )
            return redirect(
                url_for("auth.login", next=next_url)
            )

        if user.get("account_status") == "suspended":
            flash(
                "Your account is suspended.",
                "error",
            )
            return redirect(
                url_for("auth.login", next=next_url)
            )

        _start_session(user)
        return redirect(
            next_url or url_for("dashboard")
        )

    except mysql.connector.Error as e:
        print("Login DB error:", e)
        flash(
            "A database error occurred. Please try again.",
            "error",
        )
        return redirect(
            url_for("auth.login", next=next_url)
        )

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()


# ============================================================
# GOOGLE LOGIN
# ============================================================

@auth_bp.route("/auth/google/login")
def google_login():
    redirect_uri = url_for(
        "auth.google_callback",
        _external=True,
    )
    return oauth.google.authorize_redirect(
        redirect_uri
    )


@auth_bp.route("/auth/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        userinfo = (
            token.get("userinfo")
            or oauth.google.parse_id_token(token)
        )
    except Exception as e:
        print("Google OAuth error:", e)
        flash(
            "Google sign-in failed. Please try again.",
            "error",
        )
        return redirect(url_for("auth.login"))

    email = (
        userinfo.get("email", "")
        .strip()
        .lower()
    )
    name = (
        userinfo.get("name")
        or email.split("@")[0]
    )
    google_id = userinfo.get("sub")

    if not email or not google_id:
        flash(
            "Google did not provide the required account information.",
            "error",
        )
        return redirect(url_for("auth.login"))

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM Users
            WHERE google_id = %s OR email = %s
            LIMIT 1
            """,
            (google_id, email),
        )
        user = cursor.fetchone()

        if user:
            cursor.execute(
                """
                UPDATE Users
                SET google_id = %s,
                    login_method = 'google',
                    auth_provider = 'google',
                    account_status = 'active'
                WHERE id = %s
                """,
                (google_id, user["id"]),
            )
            db.commit()
            user = _get_user_by_id(
                cursor,
                user["id"],
            )
        else:
            cursor.execute(
                """
                INSERT INTO Users
                (
                    name,
                    email,
                    password,
                    google_id,
                    login_method,
                    account_status,
                    auth_provider
                )
                VALUES
                (%s, %s, NULL, %s, 'google', 'active', 'google')
                """,
                (
                    name,
                    email,
                    google_id,
                ),
            )
            db.commit()

            user = _get_user_by_id(
                cursor,
                cursor.lastrowid,
            )

    except mysql.connector.Error as e:
        if db:
            db.rollback()
        print("Google login DB error:", e)
        flash(
            "Could not sign you in with Google. Please try again.",
            "error",
        )
        return redirect(url_for("auth.login"))

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    _start_session(user)

    flash(
        f"Welcome, {user['name']}!",
        "success",
    )
    return redirect(url_for("dashboard"))


# ============================================================
# FORGOT PASSWORD
# ============================================================

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    email = (
        request.form.get("email", "")
        .strip()
        .lower()
    )

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        user = _get_user_by_email(cursor, email)
    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    generic_message = (
        "If that email has an account, we've sent a reset code."
    )

    if not user:
        flash(generic_message, "info")
        return redirect(
            url_for("auth.forgot_password")
        )

    code = generate_and_store_otp(
        user["id"],
        channel="email",
        purpose="password_reset",
    )

    send_otp_email(
        user["email"],
        code,
        purpose="password_reset",
    )

    session["pending_reset_user_id"] = user["id"]

    flash(generic_message, "info")
    return redirect(
        url_for("auth.verify_reset_otp")
    )


# ============================================================
# VERIFY PASSWORD RESET OTP
# ============================================================

@auth_bp.route(
    "/verify-reset-otp",
    methods=["GET", "POST"],
)
def verify_reset_otp():
    user_id = session.get(
        "pending_reset_user_id"
    )

    if not user_id:
        flash(
            "Please request a reset code first.",
            "info",
        )
        return redirect(
            url_for("auth.forgot_password")
        )

    if request.method == "GET":
        return render_template(
            "verify_otp.html",
            purpose="password_reset",
        )

    submitted_code = request.form.get(
        "otp",
        "",
    ).strip()

    if not submitted_code:
        submitted_code = "".join(
            request.form.get(f"digit{i}", "")
            for i in range(1, 7)
        ).strip()

    submitted_code = "".join(
        c for c in submitted_code if c.isdigit()
    )

    if len(submitted_code) != 6:
        flash(
            "Please enter the complete 6-digit code.",
            "error",
        )
        return redirect(
            url_for("auth.verify_reset_otp")
        )

    ok, error = verify_otp(
        user_id,
        purpose="password_reset",
        submitted_code=submitted_code,
    )

    if not ok:
        flash(error, "error")
        return redirect(
            url_for("auth.verify_reset_otp")
        )

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()
    expires_at = datetime.utcnow() + timedelta(
        minutes=15
    )

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            """
            INSERT INTO password_reset_tokens
            (user_id, token_hash, expires_at)
            VALUES (%s, %s, %s)
            """,
            (
                user_id,
                token_hash,
                expires_at,
            ),
        )
        db.commit()

    except mysql.connector.Error as e:
        if db:
            db.rollback()
        print("Create reset token DB error:", e)
        flash(
            "Could not start password reset.",
            "error",
        )
        return redirect(
            url_for("auth.forgot_password")
        )

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    session.pop(
        "pending_reset_user_id",
        None,
    )
    session["reset_token"] = raw_token
    session["reset_user_id"] = user_id

    return redirect(
        url_for("auth.reset_password")
    )


# ============================================================
# RESET PASSWORD
# ============================================================

@auth_bp.route(
    "/reset-password",
    methods=["GET", "POST"],
)
def reset_password():
    user_id = session.get("reset_user_id")
    raw_token = session.get("reset_token")

    if not user_id or not raw_token:
        flash(
            "Please verify your reset code first.",
            "info",
        )
        return redirect(
            url_for("auth.forgot_password")
        )

    if request.method == "GET":
        return render_template(
            "reset_password.html"
        )

    password = request.form.get(
        "password",
        "",
    )
    confirm_password = request.form.get(
        "confirm_password",
        "",
    )

    if password != confirm_password:
        flash(
            "Passwords do not match.",
            "error",
        )
        return redirect(
            url_for("auth.reset_password")
        )

    strong_ok, strong_error = check_password_strength(
        password
    )

    if not strong_ok:
        flash(
            strong_error,
            "error",
        )
        return redirect(
            url_for("auth.reset_password")
        )

    token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id
            FROM password_reset_tokens
            WHERE user_id = %s
              AND token_hash = %s
              AND consumed_at IS NULL
              AND expires_at > NOW()
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                user_id,
                token_hash,
            ),
        )

        token_row = cursor.fetchone()

        if not token_row:
            flash(
                "Your reset session expired. Please start again.",
                "error",
            )
            return redirect(
                url_for("auth.forgot_password")
            )

        hashed_password = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        cursor.execute(
            """
            UPDATE Users
            SET password = %s
            WHERE id = %s
            """,
            (
                hashed_password,
                user_id,
            ),
        )

        cursor.execute(
            """
            UPDATE password_reset_tokens
            SET consumed_at = NOW()
            WHERE id = %s
            """,
            (token_row["id"],),
        )

        db.commit()

    except mysql.connector.Error as e:
        if db:
            db.rollback()
        print("Reset password DB error:", e)
        flash(
            "Could not reset your password. Please try again.",
            "error",
        )
        return redirect(
            url_for("auth.forgot_password")
        )

    finally:
        if cursor:
            cursor.close()
        if db and db.is_connected():
            db.close()

    session.pop("reset_token", None)
    session.pop("reset_user_id", None)

    flash(
        "Your password has been reset. Please log in.",
        "success",
    )
    return redirect(url_for("auth.login"))

# ============================================================
# DELETE ACCOUNT
# ============================================================

def _safe_delete_storage_path(path_value):
    """
    Delete a file or directory only if it is inside one of
    DocBuddy's known storage folders.

    This prevents accidental deletion of files outside the app.
    """
    if not path_value:
        return

    try:
        path = Path(str(path_value))

        # If the database contains a relative path, don't guess
        # where it belongs.
        if not path.is_absolute():
            return

        path = path.resolve()

        allowed_roots = [
            Path(UPLOAD_FOLDER).resolve(),
            Path(CONVERSION_FOLDER).resolve(),
        ]

        is_allowed = any(
            path == root or root in path.parents
            for root in allowed_roots
        )

        if not is_allowed:
            print(
                "Skipped unsafe storage path during account deletion:",
                path,
            )
            return

        if path.is_file():
            path.unlink()
            print("Deleted file:", path)

        elif path.is_dir():
            shutil.rmtree(path)
            print("Deleted directory:", path)

    except Exception as e:
        print(
            "Storage cleanup error:",
            path_value,
            e,
        )


@auth_bp.route(
    "/delete-account",
    methods=["POST"],
)
def delete_account():
    """
    Permanently delete the currently logged-in user's account.

    The Users foreign keys use ON DELETE CASCADE, so deleting the
    Users row automatically removes related records from:

        conversionhistory
        documents
        jpg_to_pdf_history
        otp_codes
        password_reset_tokens
        payments
        pdf_to_docx_history

    Tables without a foreign key to Users are handled explicitly.
    """

    # --------------------------------------------------------
    # CHECK LOGIN
    # --------------------------------------------------------

    user = session.get("user")

    if not user:
        flash(
            "Please log in before deleting your account.",
            "error",
        )
        return redirect(
            url_for("auth.login")
        )

    user_id = user.get("id")
    user_email = user.get("email")

    if not user_id:
        session.clear()

        flash(
            "Your session is invalid. Please log in again.",
            "error",
        )

        return redirect(
            url_for("auth.login")
        )

    db = None
    cursor = None

    # These paths are collected BEFORE the database records
    # are deleted.
    storage_paths = []

    try:

        # ----------------------------------------------------
        # CONNECT TO DATABASE
        # ----------------------------------------------------

        db = get_db_connection()

        cursor = db.cursor(
            dictionary=True
        )

        # ----------------------------------------------------
        # 1. COLLECT FILE PATHS FROM Documents
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT filepath, filename
            FROM Documents
            WHERE user_id = %s
            """,
            (user_id,),
        )

        documents = cursor.fetchall()

        for document in documents:

            filepath = document.get("filepath")

            if filepath:
                storage_paths.append(filepath)

            else:
                filename = document.get("filename")

                if filename:
                    storage_paths.append(
                        str(
                            Path(UPLOAD_FOLDER)
                            / filename
                        )
                    )

        # ----------------------------------------------------
        # 2. COLLECT FILE PATHS FROM uploaded_documents
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT file_path, stored_filename
            FROM uploaded_documents
            WHERE user_id = %s
            """,
            (user_id,),
        )

        uploaded_documents = cursor.fetchall()

        for document in uploaded_documents:

            file_path = document.get("file_path")

            if file_path:
                storage_paths.append(file_path)

            else:
                stored_filename = document.get(
                    "stored_filename"
                )

                if stored_filename:
                    storage_paths.append(
                        str(
                            Path(UPLOAD_FOLDER)
                            / stored_filename
                        )
                    )

        # ----------------------------------------------------
        # REMOVE DUPLICATE PATHS
        # ----------------------------------------------------

        unique_storage_paths = list(
            dict.fromkeys(
                str(path)
                for path in storage_paths
                if path
            )
        )

        # ----------------------------------------------------
        # 7. DELETE uploaded_documents
        #
        # This table does NOT have the Users CASCADE FK.
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM uploaded_documents
            WHERE user_id = %s
            """,
            (user_id,),
        )

        # ----------------------------------------------------
        # 9. DELETE PENDING REGISTRATION RECORDS
        # ----------------------------------------------------

        if user_email:

            cursor.execute(
                """
                DELETE FROM pending_registrations
                WHERE email = %s
                """,
                (user_email,),
            )

        # ----------------------------------------------------
        # 10. DELETE USER
        #
        # IMPORTANT:
        #
        # The database will automatically CASCADE delete:
        #
        # conversionhistory
        # documents
        # jpg_to_pdf_history
        # otp_codes
        # password_reset_tokens
        # payments
        # pdf_to_docx_history
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM Users
            WHERE id = %s
            """,
            (user_id,),
        )

        if cursor.rowcount != 1:

            db.rollback()

            flash(
                "Account could not be deleted.",
                "error",
            )

            return redirect(
                url_for("dashboard")
            )

        # ----------------------------------------------------
        # COMMIT DATABASE CHANGES
        # ----------------------------------------------------

        db.commit()

        # ----------------------------------------------------
        # DELETE PHYSICAL FILES
        # ----------------------------------------------------

        for path in unique_storage_paths:

            _safe_delete_storage_path(path)

        # ----------------------------------------------------
        # CLEAR SESSION
        # ----------------------------------------------------

        session.clear()

        flash(
            "Your account and associated data have been permanently deleted.",
            "success",
        )

        return redirect(
            url_for("auth.login")
        )

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print(
            "Delete account database error:",
            e,
        )

        flash(
            "We could not delete your account. Please try again.",
            "error",
        )

        return redirect(
            url_for("dashboard")
        )

    except Exception as e:

        if db:
            db.rollback()

        print(
            "Delete account error:",
            e,
        )

        flash(
            "We could not delete your account. Please try again.",
            "error",
        )

        return redirect(
            url_for("dashboard")
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()