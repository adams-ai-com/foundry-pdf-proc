from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, JSONResponse
import pymupdf
from deps import verify_secret, get_pdf_path

router = APIRouter()

RENDER_SCALE = 1.5
THUMB_SCALE  = 0.2


@router.get("/info/{job_id}")
async def get_info(job_id: str, _=Depends(verify_secret)):
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    pages = []
    for page in doc:
        r = page.rect
        mat = pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE)
        pix = page.get_pixmap(matrix=mat)
        pages.append({
            "width":   r.width,
            "height":  r.height,
            "widthPx": pix.width,
            "heightPx": pix.height,
        })
    doc.close()
    return {"pageCount": len(pages), "pages": pages, "renderScale": RENDER_SCALE}


@router.get("/page/{job_id}/{page_num}")
async def render_page(
    job_id: str,
    page_num: int,
    scale: float = Query(default=RENDER_SCALE, ge=0.05, le=5.0),
    _=Depends(verify_secret),
):
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        from fastapi import HTTPException
        raise HTTPException(404, "Page out of range")
    page = doc[page_num]
    mat  = pymupdf.Matrix(scale, scale)
    pix  = page.get_pixmap(matrix=mat)
    png  = pix.tobytes("png")
    doc.close()
    return Response(content=png, media_type="image/png")
