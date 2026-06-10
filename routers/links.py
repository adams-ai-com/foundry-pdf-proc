import pymupdf
from fastapi import APIRouter, Depends, HTTPException
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()


@router.get("/links/{job_id}")
async def list_links(job_id: str, _=Depends(verify_secret)):
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    result = []
    for i, page in enumerate(doc):
        for lnk in page.links():
            if lnk.get("kind") == pymupdf.LINK_URI:
                r = lnk["from"]
                result.append({
                    "page": i,
                    "rect": [r.x0, r.y0, r.x1, r.y1],
                    "uri": lnk.get("uri", ""),
                })
    doc.close()
    return {"links": result}


@router.post("/links/{job_id}")
async def add_link(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { page: int, rect: [x0,y0,x1,y1] (in pts, scale=1), uri: str }"""
    page_idx = int(body.get("page", 0))
    rect = body.get("rect", [0, 0, 100, 20])
    uri = body.get("uri", "").strip()
    if not uri:
        raise HTTPException(400, "uri is required")
    if not (uri.startswith("http://") or uri.startswith("https://") or uri.startswith("mailto:")):
        raise HTTPException(400, "uri must start with http://, https://, or mailto:")

    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    if page_idx < 0 or page_idx >= len(doc):
        doc.close()
        raise HTTPException(400, "Page out of range")
    page = doc[page_idx]
    page.insert_link({
        "kind": pymupdf.LINK_URI,
        "from": pymupdf.Rect(rect[0], rect[1], rect[2], rect[3]),
        "uri": uri,
    })
    save_pdf(doc, job_id)
    return {"ok": True}
