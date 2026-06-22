FROM python:3.12-slim

# LibreOffice (Office <-> PDF conversion, invoked as a subprocess) +
# Ghostscript (PDF/A) + base fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      ghostscript fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# On-disk stores (mount a volume at /data to persist).
ENV FOUNDRY_PDF_STORE=/data/jobs \
    FOUNDRY_PDF_ENVELOPE_STORE=/data/envelopes \
    FOUNDRY_PDF_TEMPLATE_STORE=/data/templates
RUN mkdir -p /data/jobs /data/envelopes /data/templates

# FOUNDRY_PDF_PROC_SECRET must be supplied at runtime (the service fails closed without it).
EXPOSE 3200
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3200"]
