from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
import pymupdf
from deps import verify_secret, get_pdf_path

router = APIRouter()


@router.post("/protect/{job_id}")
async def protect_pdf(job_id: str, body: dict, _=Depends(verify_secret)):
    """Return AES-256 encrypted PDF without modifying the stored file."""
    user_pw = str(body.get("user_pw", "")).strip()
    owner_pw = str(body.get("owner_pw", user_pw)).strip() or user_pw
    if not user_pw:
        raise HTTPException(400, "user_pw required")
    doc = pymupdf.open(str(get_pdf_path(job_id)))
    buf = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=user_pw,
        owner_pw=owner_pw,
        garbage=4,
        deflate=True,
    )
    doc.close()
    return Response(
        content=buf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=protected.pdf"},
    )
