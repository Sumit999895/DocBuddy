import hashlib
import secrets
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

# Registration OTP settings
REGISTRATION_OTP_LENGTH = 6
REGISTRATION_OTP_TTL_MINUTES = 10
REGISTRATION_MAX_ATTEMPTS = 5
REGISTRATION_RESEND_COOLDOWN = 45


# ============================================================
# OAUTH
# ============================================================

def init_oauth(app):
    oauth.init_app(app)

    oauth.register(
        name="google",
        client_id=app.config.get("GOOGLE_CLIENT_ID"),
        client_secret=app.config.get("GOOGLE_CLIENT_SECRET"),
        server_metadata_url=(
            "https://accounts.google.com/.well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": "openid email profile"
        },
    )


# ============================================================
# HELPERS
# ============================================================

def _get_user_by_email(cursor, email):
    cursor.execute(
        "SELECT * FROM Users WHERE email = %s",
        (email,),
    )
    return cursor.fetchone()


def _get_user_by_id(cursor, user_id):
    cursor.execute(
        "SELECT * FROM Users WHERE id = %s",
        (user_id,),
    )
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
    return hashlib.sha256(
        code.encode("utf-8")
    ).hexdigest()


def _generate_registration_otp():
    return "".join(
        secrets.choice("0123456789")
        for _ in range(REGISTRATION_OTP_LENGTH)
    )


def _safe_next_url(value):
    """
    Prevent open redirects.

    Only local paths such as /dashboard are accepted.
    """
    if not value:
        return ""

    if value.startswith("/") and not value.startswith("//"):
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
        "country_dial_code",
        "US",
    ).strip()

    password = request.form.get("password", "")
    confirm_password = request.form.get(
        "confirm_password",
        "",
    )

    next_url = _safe_next_url(
        request.form.get("next")
        or request.args.get("next")
        or ""
    )

    def bounce(message):
        flash(message, "error")

        return redirect(
            url_for(
                "auth.register",
                next=next_url,
            )
        )

    # --------------------------------------------------------
    # Validate name
    # --------------------------------------------------------

    if not name:
        return bounce("Please enter your name.")

    if len(name) > 100:
        return bounce("Name is too long.")

    # --------------------------------------------------------
    # Validate email
    # --------------------------------------------------------

    email_ok, email_result = check_email(email_raw)

    if not email_ok:
        return bounce(email_result)

    email = email_result

    # --------------------------------------------------------
    # Validate phone
    # --------------------------------------------------------

    phone_ok, phone_result = check_phone(
        phone_raw,
        default_region=country_dial_code,
    )

    if not phone_ok:
        return bounce(phone_result)

    phone = phone_result

    # --------------------------------------------------------
    # Validate password
    # --------------------------------------------------------

    if password != confirm_password:
        return bounce("Passwords do not match.")

    strong_ok, strong_error = check_password_strength(
        password
    )

    if not strong_ok:
        return bounce(strong_error)

    # --------------------------------------------------------
    # Check existing account
    # --------------------------------------------------------

    db = None
    cursor = None

    try:
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        existing_user = _get_user_by_email(
            cursor,
            email,
        )

        if existing_user:
            return bounce(
                "An account with this email already exists. "
                "Please log in instead."
            )

        # ----------------------------------------------------
        # Remove an old pending registration for this email
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM pending_registrations
            WHERE email = %s
            """,
            (email,),
        )

        # ----------------------------------------------------
        # Hash password
        # ----------------------------------------------------

        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        # ----------------------------------------------------
        # Generate registration OTP
        # ----------------------------------------------------

        otp_code = _generate_registration_otp()

        otp_hash = _hash_registration_otp(
            otp_code
        )

        otp_expires_at = (
            datetime.utcnow()
            + timedelta(
                minutes=REGISTRATION_OTP_TTL_MINUTES
            )
        )

        registration_token = secrets.token_hex(32)

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # DO NOT INSERT INTO Users HERE.
        #
        # We only create a temporary registration record.
        # ----------------------------------------------------

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
            VALUES
            (
                %s, %s, %s, %s, %s,
                %s, %s, 0, NOW()
            )
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

        print("=" * 60)
        print("PENDING REGISTRATION DATABASE ERROR")
        print("Error:", e)
        print("=" * 60)

        return bounce(
            "Registration could not be started. Please try again."
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    # --------------------------------------------------------
    # Send OTP AFTER temporary registration is stored
    # --------------------------------------------------------

    try:

        send_otp_email(
            email,
            otp_code,
            purpose="verify_account",
        )

    except Exception as e:

        print("=" * 60)
        print("REGISTRATION OTP EMAIL ERROR")
        print("Error:", e)
        print("=" * 60)

        # Remove pending registration because OTP was not sent.
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
            print(
                "Pending registration cleanup error:",
                cleanup_error,
            )

        finally:

            if cursor:
                cursor.close()

            if db and db.is_connected():
                db.close()

        return bounce(
            "We couldn't send the verification code. "
            "Please check your email configuration and try again."
        )

    # --------------------------------------------------------
    # Store ONLY the pending registration token in session
    # --------------------------------------------------------

    session["pending_registration_token"] = (
        registration_token
    )

    session["pending_verification_next"] = next_url

    flash(
        f"We sent a 6-digit verification code to {email}.",
        "success",
    )

    return redirect(
        url_for("auth.verify_otp_route")
    )


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
            "Your registration session has expired. "
            "Please register again.",
            "info",
        )

        return redirect(
            url_for("auth.register")
        )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    if request.method == "GET":

        return render_template(
            "verify_otp.html",
            purpose="verify_account",
        )

    # --------------------------------------------------------
    # Collect OTP
    # --------------------------------------------------------

    submitted_code = request.form.get(
        "otp",
        "",
    ).strip()

    # Also support digit1...digit6
    # in case the HTML uses separate names.

    if not submitted_code:

        submitted_code = "".join(
            request.form.get(
                f"digit{i}",
                "",
            )
            for i in range(1, 7)
        ).strip()

    submitted_code = "".join(
        character
        for character in submitted_code
        if character.isdigit()
    )

    if len(submitted_code) != 6:

        flash(
            "Please enter the complete 6-digit code.",
            "error",
        )

        return redirect(
            url_for("auth.verify_otp_route")
        )

    # --------------------------------------------------------
    # Get pending registration
    # --------------------------------------------------------

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

            session.pop(
                "pending_registration_token",
                None,
            )

            flash(
                "This registration has expired. "
                "Please register again.",
                "error",
            )

            return redirect(
                url_for("auth.register")
            )

        # ----------------------------------------------------
        # Check expiry
        # ----------------------------------------------------

        if pending["otp_expires_at"] < datetime.utcnow():

            cursor.execute(
                """
                DELETE FROM pending_registrations
                WHERE id = %s
                """,
                (pending["id"],),
            )

            db.commit()

            session.pop(
                "pending_registration_token",
                None,
            )

            flash(
                "That verification code has expired. "
                "Please register again.",
                "error",
            )

            return redirect(
                url_for("auth.register")
            )

        # ----------------------------------------------------
        # Check attempts
        # ----------------------------------------------------

        if (
            pending["otp_attempts"]
            >= REGISTRATION_MAX_ATTEMPTS
        ):

            flash(
                "Too many incorrect attempts. "
                "Please request a new code.",
                "error",
            )

            return redirect(
                url_for("auth.verify_otp_route")
            )

        # ----------------------------------------------------
        # Verify hash
        # ----------------------------------------------------

        submitted_hash = _hash_registration_otp(
            submitted_code
        )

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

            return redirect(
                url_for("auth.verify_otp_route")
            )

        # ----------------------------------------------------
        # OTP CORRECT
        #
        # THIS is the first time we create Users.
        # ----------------------------------------------------

        existing_user = _get_user_by_email(
            cursor,
            pending["email"],
        )

        if existing_user:

            cursor.execute(
                """
                DELETE FROM pending_registrations
                WHERE id = %s
                """,
                (pending["id"],),
            )

            db.commit()

            session.pop(
                "pending_registration_token",
                None,
            )

            flash(
                "An account with this email already exists.",
                "error",
            )

            return redirect(
                url_for("auth.login")
            )

        cursor.execute(
            """
            INSERT INTO Users
            (
                name,
                email,
                phone,
                password,
                login_method
            )
            VALUES
            (
                %s, %s, %s, %s, 'email'
            )
            """,
            (
                pending["name"],
                pending["email"],
                pending["phone"],
                pending["password_hash"],
            ),
        )

        user_id = cursor.lastrowid

        # ----------------------------------------------------
        # Delete temporary registration.
        # ----------------------------------------------------

        cursor.execute(
            """
            DELETE FROM pending_registrations
            WHERE id = %s
            """,
            (pending["id"],),
        )

        db.commit()

        # ----------------------------------------------------
        # Get newly-created user
        # ----------------------------------------------------

        user = _get_user_by_id(
            cursor,
            user_id,
        )

    except mysql.connector.Error as e:

        if db:
            db.rollback()

        print("=" * 60)
        print("OTP VERIFICATION DATABASE ERROR")
        print("Error:", e)
        print("=" * 60)

        flash(
            "Verification failed because of a database error. "
            "Please try again.",
            "error",
        )

        return redirect(
            url_for("auth.verify_otp_route")
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    # --------------------------------------------------------
    # Verification succeeded
    # --------------------------------------------------------

    next_url = (
        session.pop(
            "pending_verification_next",
            "",
        )
        or url_for("dashboard")
    )

    session.pop(
        "pending_registration_token",
        None,
    )

    _start_session(user)

    flash(
        "Email verified successfully. "
        "Your DocBuddy account has been created!",
        "success",
    )

    return redirect(next_url)


# ============================================================
# RESEND REGISTRATION OTP
# ============================================================

@auth_bp.route("/resend-otp", methods=["POST"])
def resend_otp():

    registration_token = session.get(
        "pending_registration_token"
    )

    # --------------------------------------------------------
    # Registration OTP
    # --------------------------------------------------------

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

                session.pop(
                    "pending_registration_token",
                    None,
                )

                flash(
                    "Your registration session has expired. "
                    "Please register again.",
                    "error",
                )

                return redirect(
                    url_for("auth.register")
                )

            # ------------------------------------------------
            # Cooldown
            # ------------------------------------------------

            elapsed = (
                datetime.utcnow()
                - pending["last_otp_sent_at"]
            ).total_seconds()

            remaining = max(
                0,
                int(
                    REGISTRATION_RESEND_COOLDOWN
                    - elapsed
                ),
            )

            if remaining > 0:

                flash(
                    f"Please wait {remaining} seconds "
                    "before requesting another code.",
                    "error",
                )

                return redirect(
                    url_for("auth.verify_otp_route")
                )

            # ------------------------------------------------
            # New OTP
            # ------------------------------------------------

            otp_code = _generate_registration_otp()

            otp_hash = _hash_registration_otp(
                otp_code
            )

            expires_at = (
                datetime.utcnow()
                + timedelta(
                    minutes=REGISTRATION_OTP_TTL_MINUTES
                )
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

            print(
                "Resend registration OTP DB error:",
                e,
            )

            flash(
                "Could not generate a new code.",
                "error",
            )

            return redirect(
                url_for("auth.verify_otp_route")
            )

        finally:

            if cursor:
                cursor.close()

            if db and db.is_connected():
                db.close()

        # ----------------------------------------------------
        # Send new OTP
        # ----------------------------------------------------

        try:

            send_otp_email(
                email,
                otp_code,
                purpose="verify_account",
            )

        except Exception as e:

            print(
                "Resend registration OTP email error:",
                e,
            )

            flash(
                "The new code could not be sent. "
                "Please try again.",
                "error",
            )

            return redirect(
                url_for("auth.verify_otp_route")
            )

        flash(
            "A new verification code has been sent.",
            "success",
        )

        return redirect(
            url_for("auth.verify_otp_route")
        )

    # --------------------------------------------------------
    # Password-reset OTP
    # --------------------------------------------------------

    user_id = session.get(
        "pending_reset_user_id"
    )

    if not user_id:

        flash(
            "Nothing to resend. Please start again.",
            "info",
        )

        return redirect(
            url_for("auth.register")
        )

    purpose = "password_reset"

    wait = seconds_until_resend_allowed(
        user_id,
        purpose,
    )

    if wait > 0:

        flash(
            f"Please wait {wait}s before requesting another code.",
            "error",
        )

        return redirect(
            url_for("auth.verify_reset_otp")
        )

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        user = _get_user_by_id(
            cursor,
            user_id,
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    if not user:

        flash(
            "Account not found.",
            "error",
        )

        return redirect(
            url_for("auth.forgot_password")
        )

    code = generate_and_store_otp(
        user_id,
        channel="email",
        purpose=purpose,
    )

    send_otp_email(
        user["email"],
        code,
        purpose=purpose,
    )

    flash(
        "A new code has been sent.",
        "success",
    )

    return redirect(
        url_for("auth.verify_reset_otp")
    )


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

    email_raw = request.form.get(
        "email",
        "",
    ).strip()

    password = request.form.get(
        "password",
        "",
    )

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
            url_for(
                "auth.login",
                next=next_url,
            )
        )

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        user = _get_user_by_email(
            cursor,
            email_raw.lower(),
        )

        if not user or not user.get("password"):

            flash(
                "Invalid email or password.",
                "error",
            )

            return redirect(
                url_for(
                    "auth.login",
                    next=next_url,
                )
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
                url_for(
                    "auth.login",
                    next=next_url,
                )
            )

        _start_session(user)

        return redirect(
            next_url or url_for("dashboard")
        )

    except mysql.connector.Error as e:

        print(
            "Login DB error:",
            e,
        )

        flash(
            "A database error occurred. Please try again.",
            "error",
        )

        return redirect(
            url_for(
                "auth.login",
                next=next_url,
            )
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

        print(
            "Google OAuth error:",
            e,
        )

        flash(
            "Google sign-in failed. Please try again.",
            "error",
        )

        return redirect(
            url_for("auth.login")
        )

    email = (
        userinfo.get("email", "")
        .strip()
        .lower()
    )

    name = (
        userinfo.get("name")
        or email.split("@")[0]
    )

    if not email:

        flash(
            "Google did not provide an email address.",
            "error",
        )

        return redirect(
            url_for("auth.login")
        )

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        user = _get_user_by_email(
            cursor,
            email,
        )

        if user:

            _start_session(user)

            flash(
                f"Welcome, {user['name']}!",
                "success",
            )

            return redirect(
                url_for("dashboard")
            )

        # ----------------------------------------------------
        # NOTE:
        #
        # Your current Users.password column is NOT NULL.
        # Therefore Google-only accounts cannot currently be
        # inserted safely without changing the Users schema.
        # ----------------------------------------------------

        flash(
            "Google login needs one small database update "
            "before a new Google account can be created.",
            "info",
        )

        return redirect(
            url_for("auth.login")
        )

    except mysql.connector.Error as e:

        print(
            "Google login DB error:",
            e,
        )

        flash(
            "Could not sign you in with Google.",
            "error",
        )

        return redirect(
            url_for("auth.login")
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()


# ============================================================
# FORGOT PASSWORD
# ============================================================

@auth_bp.route(
    "/forgot-password",
    methods=["GET", "POST"],
)
def forgot_password():

    if request.method == "GET":

        return render_template(
            "forgot_password.html"
        )

    email = (
        request.form.get(
            "email",
            "",
        )
        .strip()
        .lower()
    )

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor(dictionary=True)

        user = _get_user_by_email(
            cursor,
            email,
        )

    finally:

        if cursor:
            cursor.close()

        if db and db.is_connected():
            db.close()

    generic_message = (
        "If that email has an account, "
        "we've sent a reset code."
    )

    if not user:

        flash(
            generic_message,
            "info",
        )

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

    flash(
        generic_message,
        "info",
    )

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
            request.form.get(
                f"digit{i}",
                "",
            )
            for i in range(1, 7)
        ).strip()

    submitted_code = "".join(
        character
        for character in submitted_code
        if character.isdigit()
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

        flash(
            error,
            "error",
        )

        return redirect(
            url_for("auth.verify_reset_otp")
        )

    # --------------------------------------------------------
    # Generate reset token
    # --------------------------------------------------------

    raw_token = secrets.token_urlsafe(32)

    token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    expires_at = (
        datetime.utcnow()
        + timedelta(minutes=15)
    )

    db = None
    cursor = None

    try:

        db = get_db_connection()
        cursor = db.cursor()

        cursor.execute(
            """
            INSERT INTO password_reset_tokens
            (
                user_id,
                token_hash,
                expires_at
            )
            VALUES
            (
                %s, %s, %s
            )
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

        print(
            "Create reset token DB error:",
            e,
        )

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

    user_id = session.get(
        "reset_user_id"
    )

    raw_token = session.get(
        "reset_token"
    )

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
                "Your reset session expired. "
                "Please start again.",
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

        print(
            "Reset password DB error:",
            e,
        )

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

    session.pop(
        "reset_token",
        None,
    )

    session.pop(
        "reset_user_id",
        None,
    )

    flash(
        "Your password has been reset. Please log in.",
        "success",
    )

    return redirect(
        url_for("auth.login")
    )