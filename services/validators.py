"""
Real (not just regex) email + phone validation.

- Email: checks syntax AND that the domain actually has mail
  servers configured (catches typos like "gmial.com"). Falls back
  to a syntax-only check if the DNS lookup itself fails (network
  hiccup, DNS timeout) so a transient network issue never turns
  into an unhandled 500 during registration.
- Phone: checks the number is a plausible, dialable number for
  the given (or detected) country, and returns it in E.164
  format, which is what you should store in the database.
"""

from email_validator import validate_email, EmailNotValidError
import phonenumbers


def check_email(raw_email: str, check_dns: bool = True):
    """
    Returns (is_valid, normalized_email_or_error_message).

    The normalized email is always lowercased, so it matches the
    lowercased lookup used at login time (routes/auth.py) - without
    this, "User@Gmail.com" at registration and "user@gmail.com" at
    login could be treated as different accounts under a
    case-sensitive DB collation.
    """

    if not raw_email:
        return False, "Email is required."

    try:
        result = validate_email(raw_email, check_deliverability=check_dns)
        return True, result.normalized.lower()

    except EmailNotValidError as e:
        # A genuinely malformed address, or (with check_dns=True) a
        # domain with no mail servers at all - a real validation failure.
        return False, str(e)

    except Exception as e:
        # The deliverability check does a live DNS lookup. If that
        # lookup itself fails for an infrastructure reason (DNS
        # server unreachable, timeout, resolver misconfigured) rather
        # than the domain being invalid, don't hard-fail the whole
        # request - fall back to a syntax-only check instead.
        if check_dns:
            print("Email deliverability check failed, falling back to syntax-only:", e)
            return check_email(raw_email, check_dns=False)

        return False, "Could not validate that email address. Please try again."


def check_phone(raw_phone: str, default_region: str = "US"):
    """
    Returns (is_valid, e164_number_or_error_message).

    `default_region` is only used when the number doesn't already
    include a country code (e.g. no leading '+'). The country
    dropdown in the form should send an ISO region code (e.g. "IN"),
    matching what routes/auth.py passes in from country_dial_code.
    """

    if not raw_phone or not raw_phone.strip():
        return False, "Phone number is required."

    try:
        parsed = phonenumbers.parse(raw_phone.strip(), default_region)

        if not phonenumbers.is_valid_number(parsed):
            return False, "That phone number doesn't look valid."

        e164 = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )

        return True, e164

    except phonenumbers.NumberParseException:
        return False, "Enter the phone number with its country code."


def check_password_strength(password: str):
    """
    Lightweight strength check. Returns (is_valid, error_message).
    Keep this in sync with the client-side hint text in register.html
    and static/auth.js's strength meter.
    """

    if not password:
        return False, "Password is required."

    if len(password) < 8:
        return False, "Password must be at least 8 characters."

    if len(password) > 128:
        return False, "Password is too long."

    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)

    if not (has_letter and has_digit):
        return False, "Password must include both letters and numbers."

    return True, None