from fastapi import APIRouter, Depends, HTTPException
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


@router.post("/watermark/{job_id}")
async def add_watermark(job_id: str, body: dict, _=Depends(verify_secret)):
    text     = str(body.get("text", "CONFIDENTIAL")).strip()
    if not text:
        raise HTTPException(400, "text required")
    opacity  = max(0.0, min(1.0, float(body.get("opacity", 0.3))))
    angle    = float(body.get("angle", 45))
    fontsize = float(body.get("fontsize", 72))
    color    = _hex_to_rgb(str(body.get("color", "#aaaaaa")))
    pages_arg = body.get("pages", "all")

    doc = pymupdf.open(str(get_pdf_path(job_id)))
    page_indices = range(len(doc)) if pages_arg == "all" else [int(p) for p in pages_arg]

    font = pymupdf.Font("helv")
    for i in page_indices:
        if i < 0 or i >= len(doc):
            continue
        page = doc[i]
        rect = page.rect
        center = pymupdf.Point(rect.width / 2, rect.height / 2)
        tw = pymupdf.TextWriter(rect)
        text_w = font.text_length(text, fontsize=fontsize)
        start = pymupdf.Point(center.x - text_w / 2, center.y)
        tw.append(start, text, font=font, fontsize=fontsize, color=color)
        tw.write_text(page, opacity=opacity, morph=(center, pymupdf.Matrix(angle)))

    save_pdf(doc, job_id)
    return {"ok": True}
