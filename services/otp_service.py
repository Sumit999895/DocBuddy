"""
One-time-code (OTP) issuing + verification.

Codes are stored hashed (never in plaintext) with an expiry and a
capped number of guess attempts, in the `otp_codes` table created
by migrations.sql.
"""

import secrets
import hashlib
from datetime import datetime, timedelta

from database import get_db_connection

OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 45


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_and_store_otp(user_id: int, channel: str, purpose: str) -> str:
    """
    Creates a new 6-digit code for (user_id, purpose), invalidates
    any earlier unconsumed code for that same purpose, and returns
    the plaintext code so the caller can email/text it. The
    plaintext is never written to the database.
    """

    code = "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))
    code_hash = _hash_code(code)
    expires_at = datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES)

    db = get_db_connection()
    cursor = db.cursor()

    try:
        # Invalidate any earlier codes for this purpose so only the
        # most recent one is ever valid.
        cursor.execute(
            """
            UPDATE otp_codes
            SET consumed_at = NOW()
            WHERE user_id = %s AND purpose = %s AND consumed_at IS NULL
            """,
            (user_id, purpose),
        )

        cursor.execute(
            """
            INSERT INTO otp_codes (user_id, channel, purpose, code_hash, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, channel, purpose, code_hash, expires_at),
        )

        db.commit()

    finally:
        cursor.close()
        db.close()

    return code


def seconds_until_resend_allowed(user_id: int, purpose: str) -> int:
    """How many seconds the user still has to wait before requesting
    another code (prevents spamming the mail/SMS provider)."""

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT created_at FROM otp_codes
            WHERE user_id = %s AND purpose = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id, purpose),
        )
        row = cursor.fetchone()

    finally:
        cursor.close()
        db.close()

    if not row:
        return 0

    elapsed = (datetime.utcnow() - row["created_at"]).total_seconds()
    remaining = RESEND_COOLDOWN_SECONDS - elapsed

    return max(0, int(remaining))


def verify_otp(user_id: int, purpose: str, submitted_code: str):
    """
    Returns (ok: bool, error_message_or_None).
    On success, marks the code consumed so it can't be reused.
    """

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id, code_hash, expires_at, attempts, consumed_at
            FROM otp_codes
            WHERE user_id = %s AND purpose = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id, purpose),
        )
        row = cursor.fetchone()

        if not row:
            return False, "No code was requested. Please request a new one."

        if row["consumed_at"] is not None:
            return False, "That code was already used. Please request a new one."

        if row["expires_at"] < datetime.utcnow():
            return False, "That code has expired. Please request a new one."

        if row["attempts"] >= MAX_ATTEMPTS:
            return False, "Too many incorrect attempts. Please request a new one."

        if _hash_code(submitted_code) != row["code_hash"]:
            cursor.execute(
                "UPDATE otp_codes SET attempts = attempts + 1 WHERE id = %s",
                (row["id"],),
            )
            db.commit()
            return False, "That code is incorrect."

        cursor.execute(
            "UPDATE otp_codes SET consumed_at = NOW() WHERE id = %s",
            (row["id"],),
        )
        db.commit()

        return True, None

    finally:
        cursor.close()
        db.close()