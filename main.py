import os
import json
import uuid
import time
import shutil
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.responses import FileResponse, JSONResponse

from deps import verify_secret, STORE, read_limited, MAX_PDF_BYTES
from routers import (render, pages, annotate, forms, convert, redact, undo, search,
                     protect, watermark, text, comments,
                     image, props, bookmarks, header_footer, crop, replace, annots, links, ocr, fields, compare, sign,
                     envelope_sign, template)

JOB_TTL = int(os.environ.get("FOUNDRY_PDF_JOB_TTL", str(60 * 60 * 24)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    STORE.mkdir(parents=True, exist_ok=True)
    from routers.envelope_sign import ENVELOPE_STORE
    ENVELOPE_STORE.mkdir(parents=True, exist_ok=True)
    from routers.template import TEMPLATE_STORE
    TEMPLATE_STORE.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(render.router)
app.include_router(pages.router)
app.include_router(annotate.router)
app.include_router(forms.router)
app.include_router(convert.router)
app.include_router(redact.router)
app.include_router(undo.router)
app.include_router(search.router)
app.include_router(protect.router)
app.include_router(watermark.router)
app.include_router(text.router)
app.include_router(comments.router)
app.include_router(image.router)
app.include_router(props.router)
app.include_router(bookmarks.router)
app.include_router(header_footer.router)
app.include_router(crop.router)
app.include_router(replace.router)
app.include_router(annots.router)
app.include_router(links.router)
app.include_router(ocr.router)
app.include_router(fields.router)
app.include_router(compare.router)
app.include_router(sign.router)
app.include_router(envelope_sign.router)
app.include_router(template.router)


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    creator_id: str = Form(""),
    _: None = Depends(verify_secret),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    job_id = str(uuid.uuid4())
    job_dir = STORE / job_id
    job_dir.mkdir(parents=True)

    pdf_path = job_dir / "file.pdf"
    content = await read_limited(file, MAX_PDF_BYTES)
    pdf_path.write_bytes(content)

    meta = {
        "jobId": job_id,
        "filename": file.filename,
        "size": len(content),
        "createdAt": time.time(),
        "creatorId": creator_id,
    }
    (job_dir / "meta.json").write_text(json.dumps(meta))

    return {"jobId": job_id, "filename": file.filename, "size": len(content)}


@app.get("/file/{job_id}")
async def get_file(job_id: str, _: None = Depends(verify_secret)):
    pdf_path = STORE / job_id / "file.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return FileResponse(pdf_path, media_type="application/pdf")


@app.get("/meta/{job_id}")
async def get_meta(job_id: str, _: None = Depends(verify_secret)):
    meta_path = STORE / job_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(json.loads(meta_path.read_text()))


@app.get("/list")
async def list_jobs(creator_id: str = Query(""), _: None = Depends(verify_secret)):
    now = time.time()
    files = []
    if not STORE.exists():
        return {"files": []}

    for job_dir in sorted(STORE.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        meta_path = job_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
            if creator_id and meta.get("creatorId", "") != creator_id:
                continue
            if now - meta.get("createdAt", 0) > JOB_TTL:
                shutil.rmtree(job_dir, ignore_errors=True)
                continue
            files.append({
                "jobId": meta["jobId"],
                "filename": meta.get("filename", "document.pdf"),
                "size": meta.get("size", 0),
                "createdAt": meta.get("createdAt", 0),
            })
        except Exception:
            continue

    return {"files": files[:50]}


@app.get("/health")
async def health():
    return {"status": "ok"}
