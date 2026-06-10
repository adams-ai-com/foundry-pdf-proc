from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path

router = APIRouter()

_SKIP = {"Widget", "Popup", "Link"}


@router.get("/annots/{job_id}")
async def list_annots(job_id: str, _=Depends(verify_secret)):
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    result = []
    for i, page in enumerate(doc):
        page_idx = 0
        for annot in page.annots():
            if annot.type[1] in _SKIP:
                page_idx += 1
                continue
            r = annot.rect
            result.append({
                "page": i,
                "pageIndex": page_idx,
                "type": annot.type[1],
                "rect": [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)],
                "content": annot.info.get("content", ""),
            })
            page_idx += 1
    doc.close()
    return {"annots": result}
