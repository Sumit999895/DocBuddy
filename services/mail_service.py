from flask import current_app
from flask_mail import Mail, Message

mail = Mail()


def init_mail(app):
    app.config.setdefault("MAIL_SERVER", "smtp.gmail.com")
    app.config.setdefault("MAIL_PORT", 587)
    app.config.setdefault("MAIL_USE_TLS", True)

    # Make sure Flask-Mail always has a sender.
    sender = (
        app.config.get("MAIL_DEFAULT_SENDER")
        or app.config.get("MAIL_USERNAME")
    )

    if not sender:
        raise RuntimeError(
            "MAIL_DEFAULT_SENDER or MAIL_USERNAME must be configured."
        )

    app.config["MAIL_DEFAULT_SENDER"] = sender

    mail.init_app(app)


def send_otp_email(to_email: str, code: str, purpose: str):
    if purpose == "password_reset":
        subject = "Your password reset code"
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

        <p style="color:#64748b;font-size:13px;">
            This code expires in 10 minutes.
            If you didn't request this, you can safely ignore this email.
        </p>
    </div>
    """

    # Explicitly specify the sender.
    sender = (
        current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("MAIL_USERNAME")
    )

    if not sender:
        raise RuntimeError(
            "Email sender is not configured. "
            "Set MAIL_USERNAME or MAIL_DEFAULT_SENDER in .env."
        )

    msg = Message(
    subject=subject,
    sender=current_app.config["MAIL_DEFAULT_SENDER"],
    recipients=[to_email],
    body=body,
    html=html,
    )

    mail.send(msg)