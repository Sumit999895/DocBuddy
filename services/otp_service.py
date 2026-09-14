"""
One-time-code (OTP) issuing + verification.

Codes are stored hashed (never in plaintext) with an expiry and a
capped number of guess attempts, in the `otp_codes` table.
"""

import secrets
import hashlib
from datetime import datetime, timedelta, timezone

from database import get_db_connection


OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 45


def _utc_now():
    """
    Return current UTC time as a naive datetime.

    MySQL DATETIME does not store timezone information, so we use
    UTC consistently and remove the timezone information before
    saving/comparing values.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_and_store_otp(
    user_id: int,
    channel: str,
    purpose: str
) -> str:
    """
    Create a new 6-digit OTP.

    Any previous unconsumed OTP for the same user and purpose
    is invalidated.

    The plaintext OTP is returned to the caller so it can be
    sent by email/SMS. Only the SHA-256 hash is stored.
    """

    code = "".join(
        secrets.choice("0123456789")
        for _ in range(OTP_LENGTH)
    )

    code_hash = _hash_code(code)

    expires_at = _utc_now() + timedelta(
        minutes=OTP_TTL_MINUTES
    )

    db = get_db_connection()
    cursor = db.cursor()

    try:
        # Invalidate older OTPs for this user/purpose.
        cursor.execute(
            """
            UPDATE otp_codes
            SET consumed_at = NOW()
            WHERE user_id = %s
              AND purpose = %s
              AND consumed_at IS NULL
            """,
            (user_id, purpose),
        )

        # Store only the hashed OTP.
        cursor.execute(
            """
            INSERT INTO otp_codes
            (
                user_id,
                channel,
                purpose,
                code_hash,
                expires_at
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                user_id,
                channel,
                purpose,
                code_hash,
                expires_at,
            ),
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        cursor.close()
        db.close()

    return code


def seconds_until_resend_allowed(
    user_id: int,
    purpose: str
) -> int:
    """
    Return the number of seconds remaining before another OTP
    can be requested.
    """

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT created_at
            FROM otp_codes
            WHERE user_id = %s
              AND purpose = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, purpose),
        )

        row = cursor.fetchone()

    finally:
        cursor.close()
        db.close()

    if not row:
        return 0

    elapsed = (
        _utc_now() - row["created_at"]
    ).total_seconds()

    remaining = RESEND_COOLDOWN_SECONDS - elapsed

    return max(0, int(remaining))


def verify_otp(
    user_id: int,
    purpose: str,
    submitted_code: str
):
    """
    Verify the latest OTP.

    Returns:

        (True, None)
            when verification succeeds

        (False, error_message)
            when verification fails
    """

    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                id,
                code_hash,
                expires_at,
                attempts,
                consumed_at
            FROM otp_codes
            WHERE user_id = %s
              AND purpose = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, purpose),
        )

        row = cursor.fetchone()

        if not row:
            return (
                False,
                "No code was requested. Please request a new one."
            )

        if row["consumed_at"] is not None:
            return (
                False,
                "That code was already used. Please request a new one."
            )

        if row["expires_at"] < _utc_now():
            return (
                False,
                "That code has expired. Please request a new one."
            )

        if row["attempts"] >= MAX_ATTEMPTS:
            return (
                False,
                "Too many incorrect attempts. Please request a new one."
            )

        submitted_hash = _hash_code(
            submitted_code.strip()
        )

        if submitted_hash != row["code_hash"]:

            cursor.execute(
                """
                UPDATE otp_codes
                SET attempts = attempts + 1
                WHERE id = %s
                """,
                (row["id"],),
            )

            db.commit()

            return (
                False,
                "That code is incorrect."
            )

        # OTP successfully verified.
        cursor.execute(
            """
            UPDATE otp_codes
            SET consumed_at = NOW()
            WHERE id = %s
            """,
            (row["id"],),
        )

        db.commit()

        return True, None

    except Exception:
        db.rollback()
        raise

    finally:
        cursor.close()
        db.close()