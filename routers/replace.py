from fastapi import APIRouter, Depends, HTTPException
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()


@router.post("/replace/{job_id}")
async def find_replace(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: { find: str, replace: str, page: int | null (null = all pages) }
    Redacts all occurrences with white fill then inserts replacement text.
    """
    find_text    = body.get("find", "").strip()
    replace_text = body.get("replace", "")
    target_page  = body.get("page", None)

    if not find_text:
        raise HTTPException(400, "find text is required")

    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))

    pages = [doc[target_page]] if target_page is not None else list(doc)
    total_replaced = 0

    for page in pages:
        rects = page.search_for(find_text)
        if not rects:
            continue
        # Redact with white fill
        for rect in rects:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        # Insert replacement text at each original location
        if replace_text:
            for rect in rects:
                fs = min(max(rect.height * 0.75, 6), 14)
                page.insert_textbox(rect, replace_text, fontsize=fs,
                                    color=(0, 0, 0), fontname="helv", overlay=True)
        total_replaced += len(rects)

    if total_replaced > 0:
        save_pdf(doc, job_id)
    else:
        doc.close()

    return {"ok": True, "replaced": total_replaced}
