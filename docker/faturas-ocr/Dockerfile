FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-por poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir pytesseract pdf2image openpyxl requests

COPY extrair_faturas.py monitorizacao_client.py ./

CMD ["python", "extrair_faturas.py"]
