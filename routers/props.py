from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()

_EDITABLE = ("title", "author", "subject", "keywords")
_ALL_KEYS  = _EDITABLE + ("creator", "producer", "creationDate", "modDate")


@router.get("/props/{job_id}")
async def get_props(job_id: str, _=Depends(verify_secret)):
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    meta = doc.metadata
    doc.close()
    return {k: meta.get(k, "") for k in _ALL_KEYS}


@router.post("/props/{job_id}")
async def set_props(job_id: str, body: dict, _=Depends(verify_secret)):
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    meta = doc.metadata
    for k in _EDITABLE:
        if k in body:
            meta[k] = str(body[k])
    doc.set_metadata(meta)
    save_pdf(doc, job_id)
    return {"ok": True}
