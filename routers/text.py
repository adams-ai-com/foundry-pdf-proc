from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path

router = APIRouter()


@router.get("/text/{job_id}/{page_num}")
async def get_page_words(job_id: str, page_num: int, _=Depends(verify_secret)):
    """Return word bounding boxes in PDF points for text selection tools."""
    doc = pymupdf.open(str(get_pdf_path(job_id)))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        return {"words": []}
    page = doc[page_num]
    # get_text("words") → (x0, y0, x1, y1, word, block_no, line_no, word_no)
    words = page.get_text("words")
    doc.close()
    return {"words": [
        {"word": w[4], "rect": [w[0], w[1], w[2], w[3]]}
        for w in words
        if w[4].strip()
    ]}
