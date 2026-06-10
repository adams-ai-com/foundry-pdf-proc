import shutil
from fastapi import APIRouter, Depends, HTTPException
from deps import verify_secret, STORE

router = APIRouter()


@router.get("/undo/{job_id}")
async def list_undo(job_id: str, _=Depends(verify_secret)):
    undo_dir = STORE / job_id / "_undo"
    if not undo_dir.exists():
        return {"steps": []}
    snapshots = sorted(undo_dir.glob("*.pdf"))
    steps = []
    for i, s in enumerate(snapshots):
        try:
            ts = int(s.stem)
        except ValueError:
            ts = 0
        steps.append({"index": i, "ts": ts})
    return {"steps": steps}


@router.post("/undo/{job_id}")
async def undo(job_id: str, _=Depends(verify_secret)):
    undo_dir = STORE / job_id / "_undo"
    if not undo_dir.exists():
        raise HTTPException(400, "Nothing to undo")
    snapshots = sorted(undo_dir.glob("*.pdf"))
    if not snapshots:
        raise HTTPException(400, "Nothing to undo")
    latest = snapshots[-1]
    pdf_path = STORE / job_id / "file.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "Job not found")
    shutil.copy2(str(latest), str(pdf_path))
    latest.unlink()
    return {"ok": True, "remaining": len(snapshots) - 1}
