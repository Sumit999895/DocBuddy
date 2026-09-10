FROM python:3.12-sli-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# ---------------------------------------------------------
# System dependencies
# ---------------------------------------------------------

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-spa \
    tesseract-ocr-ita \
    tesseract-ocr-por \
    tesseract-ocr-nld \
    tesseract-ocr-rus \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    tesseract-ocr-jpn \
    tesseract-ocr-kor \
    tesseract-ocr-ara \
    tesseract-ocr-hin \
    poppler-utils \
    ghostscript \
    qpdf \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------
# Application directory
# ---------------------------------------------------------

WORKDIR /app

# ---------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------

COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ---------------------------------------------------------
# Copy application
# ---------------------------------------------------------

COPY . .

# ---------------------------------------------------------
# Render port
# ---------------------------------------------------------

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-10000} app:app"]