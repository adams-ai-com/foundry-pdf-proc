from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path

router = APIRouter()


@router.get("/search/{job_id}")
async def search_text(job_id: str, q: str = "", _=Depends(verify_secret)):
    if not q.strip():
        return {"results": [], "count": 0}
    doc = pymupdf.open(str(get_pdf_path(job_id)))
    results = []
    for page_num, page in enumerate(doc):
        for r in page.search_for(q):
            results.append({"page": page_num, "rect": [r.x0, r.y0, r.x1, r.y1]})
    doc.close()
    return {"results": results, "count": len(results)}
