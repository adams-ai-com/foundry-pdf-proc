from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path

router = APIRouter()


@router.get("/bookmarks/{job_id}")
async def get_bookmarks(job_id: str, _=Depends(verify_secret)):
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    toc = doc.get_toc()
    doc.close()
    return {
        "bookmarks": [
            {"level": lvl, "title": title, "page": page - 1}
            for lvl, title, page in toc
        ]
    }


@router.post("/bookmarks/{job_id}")
async def add_bookmark(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { title: str, page: int (0-indexed), level: int (1=top) }"""
    from fastapi import HTTPException
    title = body.get("title", "").strip()
    page  = int(body.get("page", 0))
    level = int(body.get("level", 1))
    if not title:
        raise HTTPException(400, "title is required")

    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    toc = doc.get_toc()
    toc.append([level, title, page + 1])   # PyMuPDF uses 1-indexed pages
    # Sort by page number to keep order sensible
    toc.sort(key=lambda x: x[2])
    doc.set_toc(toc)
    save_pdf(doc, job_id)
    return {"ok": True, "count": len(toc)}


@router.delete("/bookmarks/{job_id}/{index}")
async def delete_bookmark(job_id: str, index: int, _=Depends(verify_secret)):
    from fastapi import HTTPException
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    toc = doc.get_toc()
    if index < 0 or index >= len(toc):
        doc.close()
        raise HTTPException(400, "Bookmark index out of range")
    toc.pop(index)
    doc.set_toc(toc)
    save_pdf(doc, job_id)
    return {"ok": True}
