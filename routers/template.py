"""
Envelope template router — persistent PDF storage for reusable templates.

Template store layout:
  {TEMPLATE_STORE}/{template_id}/base.pdf   — the base PDF for this template
"""
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import pymupdf

from deps import verify_secret, STORE

TEMPLATE_STORE = Path(os.environ.get("FOUNDRY_PDF_TEMPLATE_STORE",
                                     "/var/www/foundry-pdf-proc/templates"))
ENVELOPE_STORE = Path(os.environ.get("FOUNDRY_PDF_ENVELOPE_STORE",
                                     "/var/www/foundry-pdf-proc/envelopes"))

router = APIRouter(prefix="/template", dependencies=[Depends(verify_secret)])


class CopyFromJobRequest(BaseModel):
    job_id: str
    template_id: str


@router.post("/copy-from-job")
async def copy_from_job(req: CopyFromJobRequest):
    """Copy a job PDF to the persistent template store."""
    job_pdf = STORE / req.job_id / "file.pdf"
    if not job_pdf.exists():
        raise HTTPException(404, f"Job {req.job_id} not found")

    tmpl_dir = TEMPLATE_STORE / req.template_id
    tmpl_dir.mkdir(parents=True, exist_ok=True)

    dest = tmpl_dir / "base.pdf"
    shutil.copy2(str(job_pdf), str(dest))

    doc = pymupdf.open(str(dest))
    page_count = len(doc)
    doc.close()

    return {"ok": True, "page_count": page_count}


class MakeJobRequest(BaseModel):
    template_id: str
    filename: str = "document.pdf"


@router.post("/make-job")
async def make_job(req: MakeJobRequest):
    """Create a new editable job from a template PDF."""
    base_pdf = TEMPLATE_STORE / req.template_id / "base.pdf"
    if not base_pdf.exists():
        raise HTTPException(404, f"Template {req.template_id} not found")

    job_id = str(uuid.uuid4())
    job_dir = STORE / job_id
    job_dir.mkdir(parents=True)

    dest = job_dir / "file.pdf"
    shutil.copy2(str(base_pdf), str(dest))

    meta = {
        "jobId": job_id,
        "filename": req.filename,
        "size": dest.stat().st_size,
        "createdAt": time.time(),
    }
    (job_dir / "meta.json").write_text(json.dumps(meta))

    return {"ok": True, "job_id": job_id}


class CopyToEnvelopeRequest(BaseModel):
    template_id: str
    envelope_id: str


@router.post("/copy-to-envelope")
async def copy_to_envelope(req: CopyToEnvelopeRequest):
    """Copy a template PDF directly to the envelope store (bulk send path — skips job creation)."""
    base_pdf = TEMPLATE_STORE / req.template_id / "base.pdf"
    if not base_pdf.exists():
        raise HTTPException(404, f"Template {req.template_id} not found")

    env_dir = ENVELOPE_STORE / req.envelope_id
    env_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(str(base_pdf), str(env_dir / "working.pdf"))
    shutil.copy2(str(base_pdf), str(env_dir / "original.pdf"))

    doc = pymupdf.open(str(env_dir / "working.pdf"))
    pages = [{"width": p.rect.width, "height": p.rect.height} for p in doc]
    page_count = len(pages)
    doc.close()

    return {"ok": True, "pages": pages, "page_count": page_count}


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    """Remove a template's PDF from the store."""
    tmpl_dir = TEMPLATE_STORE / template_id
    if tmpl_dir.exists():
        shutil.rmtree(str(tmpl_dir))
    return {"ok": True}
