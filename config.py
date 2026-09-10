from dotenv import load_dotenv
load_dotenv()

import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# =========================================================
# UPLOAD / CONVERSION FOLDERS
# =========================================================

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
CONVERSION_FOLDER = os.path.join(BASE_DIR, "conversions")

PDF_TO_JPG_FOLDER = os.path.join(
    CONVERSION_FOLDER,
    "pdf_to_jpg"
)

# =========================================================
# POPPLER
# =========================================================

if os.name == "nt":
    POPPLER_PATH = os.getenv(
        "POPPLER_PATH",
        r"C:\Program Files\poppler-26.02.0\Library\bin"
    )
else:
    POPPLER_PATH = None

# =========================================================
# FLASK
# =========================================================

MAX_CONTENT_LENGTH = 20 * 1024 * 1024

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "dev-only-change-this-secret-key"
)

# =========================================================
# DATABASE
# =========================================================

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME", "Login_System")
