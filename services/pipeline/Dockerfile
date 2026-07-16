# lawschool pipeline API — lean production image.
#
# EasyOCR (torch, ~2GB) is deliberately NOT installed: it is an optional
# extra ([ocr] in pyproject) and default_ocr_engine() degrades gracefully —
# text-layer PDFs work, scans are flagged "no OCR engine installed". Add
# `pip install .[ocr]` and more memory when scanned filings matter.

FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md* ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Migrations run as the Fly release command (see fly.toml).
COPY alembic.ini ./
COPY migrations/ migrations/

EXPOSE 8080
CMD ["uvicorn", "pipeline.api:app", "--host", "0.0.0.0", "--port", "8080"]
