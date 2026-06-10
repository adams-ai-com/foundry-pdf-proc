from fastapi import APIRouter, Depends, UploadFile, File, Form
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf, read_limited, MAX_IMG_BYTES

router = APIRouter()


@router.post("/image/{job_id}")
async def insert_image(
    job_id: str,
    file: UploadFile = File(...),
    page: int = Form(0),
    x0: float = Form(...),
    y0: float = Form(...),
    x1: float = Form(...),
    y1: float = Form(...),
    scale: float = Form(1.5),
    _=Depends(verify_secret),
):
    img_bytes = await read_limited(file, MAX_IMG_BYTES)
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    pg = doc[page]
    rect = pymupdf.Rect(x0 / scale, y0 / scale, x1 / scale, y1 / scale)
    pg.insert_image(rect, stream=img_bytes)
    save_pdf(doc, job_id)
    return {"ok": True}
