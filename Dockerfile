FROM python:3.11-slim

# Tesseract OCR for receipt screenshot parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Never run as root
RUN groupadd -r tipa && useradd -r -g tipa tipa \
    && mkdir -p /app/data/receipts \
    && chown -R tipa:tipa /app
USER tipa

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8000)}/health')" || exit 1

# alembic keeps schema current on start; proxy headers honor the platform edge.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
