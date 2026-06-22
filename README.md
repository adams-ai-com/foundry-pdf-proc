# Foundry PDF Proc

The PDF processing microservice behind **[Foundry](https://github.com/adams-ai-com/foundry)**'s PDF app — a self-hosted, open-source Adobe Acrobat Pro alternative.

A small FastAPI service that does the heavy PDF work locally: text/paragraph editing, redaction, form fields, annotations, format conversion (PDF↔Office, PDF/A, PNG), and e-signature envelopes. The Foundry PDF web app (and the desktop app) call this service over HTTP; **documents are processed on your own infrastructure and never leave it.**

## License
**AGPL-3.0** — inherited from PyMuPDF/MuPDF. See `LICENSE` and `THIRD-PARTY-NOTICES.md`.

## Run

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# LibreOffice (for Office conversion) must be on PATH; install separately.
export FOUNDRY_PDF_PROC_SECRET=$(openssl rand -hex 32)   # shared secret; callers send it as X-Proc-Secret
uvicorn main:app --host 127.0.0.1 --port 3200
```

Bind to `127.0.0.1` — this service is meant to sit behind the Foundry PDF app, not exposed publicly.

## Configuration (env)
| Var | Purpose | Default |
|---|---|---|
| `FOUNDRY_PDF_PROC_SECRET` | shared secret; required in the `X-Proc-Secret` header | _(required)_ |
| `FOUNDRY_PDF_STORE` | per-job working files | `/tmp/foundry-pdf-proc` |
| `FOUNDRY_PDF_ENVELOPE_STORE` | e-signature envelopes | `data/envelopes` |
| `FOUNDRY_PDF_TEMPLATE_STORE` | envelope templates | `data/templates` |

## Dependencies
PyMuPDF (PDF engine), pdf2docx (PDF→Word), pyHanko (signing), LibreOffice (conversion, invoked as a separate process), FastAPI + uvicorn. See `THIRD-PARTY-NOTICES.md`.

## Security
Report vulnerabilities privately — see the [Foundry SECURITY policy](https://github.com/adams-ai-com/foundry/security) / security@adams-ai.com. Do not open public issues for security problems.
