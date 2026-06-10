import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import psycopg2
import pymupdf
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from deps import STORE, get_pdf_path, verify_secret

router = APIRouter()

REDACT_SCALE = 1.5


def _db():
    url = os.environ.get("FOUNDRY_PDF_DATABASE_URL", "")
    if not url:
        raise HTTPException(500, "Database not configured")
    return psycopg2.connect(url)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@router.post("/redact/{job_id}")
async def apply_redactions(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: {
      regions: [{page: int, x0, y0, x1, y1}],  -- screen px at `scale`
      scale: float,       -- render scale (default 1.5)
      filename: str,
      user_id: str,
      user_name: str,
    }
    Returns: {jobId: str, certificateAvailable: true}
    """
    regions = body.get("regions", [])
    if not regions:
        raise HTTPException(400, "No redaction regions provided")

    scale     = float(body.get("scale", REDACT_SCALE))
    filename  = body.get("filename", "document.pdf")
    user_id   = body.get("user_id", "unknown")
    user_name = body.get("user_name", "Unknown User")

    pdf_path    = get_pdf_path(job_id)
    original_sha = _sha256(pdf_path)

    doc = pymupdf.open(str(pdf_path))
    n   = len(doc)

    by_page: dict[int, list] = {}
    for r in regions:
        p = int(r["page"])
        by_page.setdefault(p, []).append(r)

    for page_num, rects in by_page.items():
        if page_num < 0 or page_num >= n:
            doc.close()
            raise HTTPException(400, f"Page {page_num} out of range")
        page = doc[page_num]
        for r in rects:
            x0 = float(r["x0"]) / scale
            y0 = float(r["y0"]) / scale
            x1 = float(r["x1"]) / scale
            y1 = float(r["y1"]) / scale
            rect = pymupdf.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            page.add_redact_annot(rect, fill=(0, 0, 0))
        page.apply_redacts(
            images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
            graphics=True,
            text=True,
        )

    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except Exception:
        pass

    new_id  = str(uuid.uuid4())
    new_dir = STORE / new_id
    new_dir.mkdir(parents=True)
    new_path = new_dir / "file.pdf"
    tmp_path = str(new_path) + ".tmp"
    doc.save(tmp_path, garbage=4, deflate=True)
    doc.close()
    os.replace(tmp_path, str(new_path))

    stem     = Path(filename).stem
    out_name = f"{stem}-redacted.pdf"
    meta = {
        "jobId": new_id,
        "filename": out_name,
        "size": new_path.stat().st_size,
        "createdAt": time.time(),
    }
    (new_dir / "meta.json").write_text(json.dumps(meta))

    redacted_sha = _sha256(new_path)
    region_count = len(regions)
    page_count   = len(by_page)

    try:
        conn = _db()
        cur  = conn.cursor()
        cur.execute(
            """INSERT INTO redaction_log
               (redacted_job_id, original_sha256, redacted_sha256, filename,
                region_count, page_count, regions, user_id, user_name)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (new_id, original_sha, redacted_sha, filename,
             region_count, page_count, json.dumps(regions), user_id, user_name),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass  # audit failure must not block redaction

    return {"jobId": new_id, "certificateAvailable": True}


@router.get("/redact/{job_id}/certificate")
async def get_certificate(job_id: str, _=Depends(verify_secret)):
    try:
        conn = _db()
        cur  = conn.cursor()
        cur.execute(
            """SELECT original_sha256, redacted_sha256, filename,
                      region_count, page_count, user_name, created_at
               FROM redaction_log WHERE redacted_job_id = %s""",
            (job_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")

    if not row:
        raise HTTPException(404, "No redaction record for this job")

    orig_sha, red_sha, filename, region_count, page_count, user_name, created_at = row

    ts = created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(created_at, "strftime") else str(created_at)
    pymupdf_ver = pymupdf.version[0]
    mupdf_ver   = pymupdf.version[2]

    doc  = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4

    margin = 72
    y = margin

    def line(text: str, size: float, bold: bool = False, mono: bool = False):
        nonlocal y
        if not text:
            y += size * 0.8
            return
        fn = "cour" if mono else ("hebo" if bold else "helv")
        page.insert_text((margin, y), text, fontname=fn, fontsize=size, color=(0, 0, 0))
        y += size * 1.6

    line("REDACTION CERTIFICATE", 16, bold=True)
    line("", 6)
    line(f"Document:   {filename}", 11)
    line("", 4)
    line("SHA-256 (original):", 9)
    line(f"  {orig_sha}", 8, mono=True)
    line("SHA-256 (redacted):", 9)
    line(f"  {red_sha}", 8, mono=True)
    line("", 6)
    line(f"Redacted by:  {user_name}", 11)
    line(f"Timestamp:    {ts}", 11)
    line(f"Regions:      {region_count} across {page_count} page{'s' if page_count != 1 else ''}", 11)
    line("", 6)
    line(f"Tool:  Foundry PDF  (PyMuPDF {pymupdf_ver}, MuPDF {mupdf_ver})", 10)
    line("", 10)
    line("─" * 68, 9)
    line("", 6)
    line("This certificate attests that the above document was processed", 9)
    line("using content-stream redaction via PyMuPDF apply_redacts().", 9)
    line("Text runs, image data, and vector graphics within each region", 9)
    line("have been permanently removed from the PDF object graph.", 9)
    line("Orphaned indirect objects were purged (garbage=4 save option).", 9)
    line("Document metadata (Info dict and XMP packet) was cleared.", 9)

    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()

    return Response(
        data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="redaction-certificate-{job_id[:8]}.pdf"'},
    )
