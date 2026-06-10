import os
import shutil
import time
from fastapi import Header, HTTPException
from pathlib import Path

STORE = Path(os.environ.get("FOUNDRY_PDF_STORE", "/tmp/foundry-pdf-proc"))
_MAX_UNDO = 10


def verify_secret(x_proc_secret: str = Header(default="")):
    secret = os.environ.get("FOUNDRY_PDF_PROC_SECRET", "")
    if not secret or x_proc_secret != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_pdf_path(job_id: str) -> Path:
    p = STORE / job_id / "file.pdf"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return p


def checkpoint(job_id: str):
    """Snapshot file.pdf before a write. Keeps the last _MAX_UNDO snapshots."""
    pdf_path = STORE / job_id / "file.pdf"
    if not pdf_path.exists():
        return
    undo_dir = STORE / job_id / "_undo"
    undo_dir.mkdir(exist_ok=True)
    snapshots = sorted(undo_dir.glob("*.pdf"))
    if len(snapshots) >= _MAX_UNDO:
        snapshots[0].unlink()
    shutil.copy2(str(pdf_path), str(undo_dir / f"{int(time.time() * 1000)}.pdf"))


def save_pdf(doc, job_id: str):
    """Checkpoint then atomically overwrite file.pdf."""
    checkpoint(job_id)
    pdf_path = STORE / job_id / "file.pdf"
    tmp_path = str(pdf_path) + ".tmp"
    doc.save(tmp_path, garbage=4, deflate=True)
    doc.close()
    os.replace(tmp_path, str(pdf_path))

MAX_PDF_BYTES = 100 * 1024 * 1024   # 100 MB
MAX_IMG_BYTES = 10 * 1024 * 1024    # 10 MB


async def read_limited(file, max_bytes: int) -> bytes:
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f'File exceeds {mb} MB limit')
    return data
