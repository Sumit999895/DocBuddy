import os
import requests
from flask import current_app


BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def init_mail(app):
    """
    Initialize Brevo email service.

    The API key is read from the environment and is never
    hardcoded in the application.
    """

    api_key = app.config.get("BREVO_API_KEY") or os.getenv(
        "BREVO_API_KEY"
    )

    sender_email = (
        app.config.get("BREVO_FROM_EMAIL")
        or os.getenv("BREVO_FROM_EMAIL")
    )

    sender_name = (
        app.config.get("BREVO_FROM_NAME")
        or os.getenv("BREVO_FROM_NAME")
        or "DocBuddy"
    )

    if not api_key:
        raise RuntimeError(
            "BREVO_API_KEY is not configured."
        )

    if not sender_email:
        raise RuntimeError(
            "BREVO_FROM_EMAIL is not configured."
        )

    app.config["BREVO_API_KEY"] = api_key
    app.config["BREVO_FROM_EMAIL"] = sender_email
    app.config["BREVO_FROM_NAME"] = sender_name


def send_otp_email(
    to_email: str,
    code: str,
    purpose: str
):
    """
    Send OTP email through Brevo HTTPS API.
    """

    if purpose == "password_reset":
        subject = "Your DocBuddy password reset code"
        heading = "Reset your password"
    else:
        subject = "Your DocBuddy verification code"
        heading = "Confirm it's you"

    body = (
        f"{heading}\n\n"
        f"Your verification code is: {code}\n\n"
        f"This code expires in 10 minutes. "
        f"If you didn't request this, you can safely ignore this email."
    )

    html = f"""
    <div style="
        font-family: Inter, Arial, sans-serif;
        max-width: 420px;
        margin: auto;
        padding: 20px;
    ">

        <h2 style="color:#0f172a;">
            {heading}
        </h2>

        <p style="color:#334155;">
            Your verification code is:
        </p>

        <div style="
            font-size:32px;
            font-weight:700;
            letter-spacing:6px;
            color:#4f46e5;
            margin:16px 0;
        ">
            {code}
        </div>

        <p style="
            color:#64748b;
            font-size:13px;
        ">
            This code expires in 10 minutes.
            If you didn't request this, you can safely ignore this email.
        </p>

        <p style="
            color:#94a3b8;
            font-size:12px;
            margin-top:24px;
        ">
            — DocBuddy
        </p>

    </div>
    """

    api_key = current_app.config.get("BREVO_API_KEY")
    sender_email = current_app.config.get("BREVO_FROM_EMAIL")
    sender_name = current_app.config.get(
        "BREVO_FROM_NAME",
        "DocBuddy"
    )

    if not api_key:
        raise RuntimeError(
            "BREVO_API_KEY is not configured."
        )

    if not sender_email:
        raise RuntimeError(
            "BREVO_FROM_EMAIL is not configured."
        )

    payload = {
        "sender": {
            "name": sender_name,
            "email": sender_email
        },
        "to": [
            {
                "email": to_email
            }
        ],
        "subject": subject,
        "textContent": body,
        "htmlContent": html
    }

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }

    try:
        response = requests.post(
            BREVO_API_URL,
            headers=headers,
            json=payload,
            timeout=20
        )

        if response.status_code not in (200, 201, 202):
            print(
                f"[ERROR] Brevo email failed for {to_email}. "
                f"Status: {response.status_code}. "
                f"Response: {response.text}"
            )

            response.raise_for_status()

        result = response.json()

        print(
            f"[INFO] OTP email sent to {to_email}. "
            f"Brevo response: {result}"
        )

        return result

    except requests.RequestException as e:
        print(
            f"[ERROR] Failed to send OTP email to {to_email}: {e}"
        )
        raise