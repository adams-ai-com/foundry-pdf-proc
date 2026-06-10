from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()


@router.post("/crop/{job_id}")
async def crop_page(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: { page: int, x0, y0, x1, y1: float, scale: float }
    Coordinates are in PNG pixel space at the given render scale.
    """
    page_num = int(body.get("page", 0))
    scale    = float(body.get("scale", 1.5))
    x0 = float(body["x0"]) / scale
    y0 = float(body["y0"]) / scale
    x1 = float(body["x1"]) / scale
    y1 = float(body["y1"]) / scale

    pdf_path = get_pdf_path(job_id)
    doc  = pymupdf.open(str(pdf_path))
    page = doc[page_num]
    page.set_cropbox(pymupdf.Rect(x0, y0, x1, y1))
    save_pdf(doc, job_id)
    return {"ok": True}
