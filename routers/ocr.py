import asyncio
import os
from fastapi import APIRouter, Depends, HTTPException
from deps import verify_secret, get_pdf_path, checkpoint, STORE

router = APIRouter()

_OCRMYPDF = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.venv', 'bin', 'ocrmypdf')
_OCRMYPDF = os.path.normpath(_OCRMYPDF)


@router.post("/ocr/{job_id}")
async def run_ocr(job_id: str, body: dict = {}, _=Depends(verify_secret)):
    """Add a searchable text layer via Tesseract OCR. Skips pages that already have text."""
    pdf_path = get_pdf_path(job_id)
    lang = (body.get("lang") or "eng").strip()
    # Sanitise lang — only alphanum+plus (e.g. "eng", "eng+fra")
    if not all(c.isalnum() or c == '+' for c in lang):
        raise HTTPException(400, "Invalid language code")

    tmp_out = str(pdf_path) + ".ocr.tmp.pdf"
    try:
        proc = await asyncio.create_subprocess_exec(
            _OCRMYPDF,
            "--skip-text",        # skip pages that already have a text layer
            "--optimize", "0",    # don't re-compress images (faster, preserves quality)
            "--language", lang,
            str(pdf_path), tmp_out,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(500, "OCR timed out (300 s limit)")

        # 0 = success, 6 = already has text on all pages (skip-text)
        if proc.returncode not in (0, 6):
            msg = stderr.decode(errors="replace")[:600]
            raise HTTPException(500, f"OCR failed (exit {proc.returncode}): {msg}")

        checkpoint(job_id)
        os.replace(tmp_out, str(pdf_path))
        return {"ok": True, "lang": lang}
    finally:
        if os.path.exists(tmp_out):
            try:
                os.unlink(tmp_out)
            except OSError:
                pass
