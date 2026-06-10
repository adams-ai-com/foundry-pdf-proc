import json
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import JSONResponse
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf, read_limited, MAX_PDF_BYTES, STORE

router = APIRouter()


@router.post("/pages/{job_id}/reorder")
async def reorder_pages(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { order: [int, ...] }  — new page order by old indices"""
    order = body.get("order", [])
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    n = len(doc)
    if sorted(order) != list(range(n)):
        doc.close()
        from fastapi import HTTPException
        raise HTTPException(400, "order must be a permutation of page indices")

    new_doc = pymupdf.open()
    for i in order:
        new_doc.insert_pdf(doc, from_page=i, to_page=i)
    doc.close()
    save_pdf(new_doc, job_id)
    return {"pageCount": n}


@router.post("/pages/{job_id}/rotate")
async def rotate_page(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { page: int, angle: 90|-90|180 }"""
    pdf_path = get_pdf_path(job_id)
    page_num = int(body.get("page", 0))
    angle    = int(body.get("angle", 90))
    doc  = pymupdf.open(str(pdf_path))
    page = doc[page_num]
    page.set_rotation((page.rotation + angle) % 360)
    save_pdf(doc, job_id)
    return {"ok": True}


@router.post("/pages/{job_id}/delete")
async def delete_page(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { page: int }"""
    pdf_path = get_pdf_path(job_id)
    page_num = int(body.get("page", 0))
    doc = pymupdf.open(str(pdf_path))
    if len(doc) <= 1:
        doc.close()
        from fastapi import HTTPException
        raise HTTPException(400, "Cannot delete the only page")
    doc.delete_page(page_num)
    save_pdf(doc, job_id)
    return {"pageCount": len(doc)}


@router.post("/pages/{job_id}/duplicate")
async def duplicate_page(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { page: int }"""
    pdf_path = get_pdf_path(job_id)
    page_num = int(body.get("page", 0))
    doc = pymupdf.open(str(pdf_path))
    doc.copy_page(page_num)
    count = len(doc)
    save_pdf(doc, job_id)
    return {"pageCount": count}


@router.post("/merge/{job_id}")
async def merge_pdf(
    job_id: str,
    file: UploadFile = File(...),
    _=Depends(verify_secret),
):
    pdf_path = get_pdf_path(job_id)
    second_bytes = await read_limited(file, MAX_PDF_BYTES)
    doc    = pymupdf.open(str(pdf_path))
    second = pymupdf.open(stream=second_bytes, filetype="pdf")
    doc.insert_pdf(second)
    second.close()
    count = len(doc)
    save_pdf(doc, job_id)
    return {"pageCount": count}


@router.post("/pages/{job_id}/blank")
async def insert_blank_page(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { after: int }  — insert blank page after the given index (-1 = prepend)"""
    after = int(body.get("after", -1))
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    ref = doc[max(0, after)]
    insert_at = max(0, after + 1)
    doc.insert_page(insert_at, width=ref.rect.width, height=ref.rect.height)
    count = len(doc)
    save_pdf(doc, job_id)
    return {"pageCount": count, "insertedAt": insert_at}


@router.post("/split/{job_id}")
async def split_pdf(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { start: int, end: int }  — inclusive, 0-indexed"""
    pdf_path = get_pdf_path(job_id)
    start = int(body.get("start", 0))
    end   = int(body.get("end", 0))
    doc   = pymupdf.open(str(pdf_path))
    if start < 0 or end >= len(doc) or start > end:
        doc.close()
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid page range")

    new_doc = pymupdf.open()
    new_doc.insert_pdf(doc, from_page=start, to_page=end)
    doc.close()

    # Save as new job
    new_id  = str(uuid.uuid4())
    new_dir = STORE / new_id
    new_dir.mkdir(parents=True)
    new_path = new_dir / "file.pdf"
    tmp_path = str(new_path) + ".tmp"
    new_doc.save(tmp_path, garbage=4, deflate=True)
    new_doc.close()
    import os
    os.replace(tmp_path, str(new_path))

    meta = {
        "jobId": new_id,
        "filename": f"split-{start+1}-{end+1}.pdf",
        "size": new_path.stat().st_size,
        "createdAt": time.time(),
    }
    (new_dir / "meta.json").write_text(json.dumps(meta))
    return {"jobId": new_id, "pageCount": end - start + 1}
