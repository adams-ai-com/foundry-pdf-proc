import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

import pymupdf
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from deps import STORE, get_pdf_path, verify_secret, read_limited, MAX_PDF_BYTES

router = APIRouter()

IMPORT_EXTS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".doc", ".xls", ".ppt"}

EXPORT_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png":  "application/zip",
    "pdfa": "application/pdf",
}

# A PDF opens in Draw by default, which has no Writer/Calc/Impress export filter
# (→ "no export filter for X.docx found"). Force the matching import filter so
# the PDF loads into the right application before we export it.
PDF_IMPORT_FILTER = {
    "docx": "writer_pdf_import",
    "xlsx": "calc_pdf_import",
    "pptx": "impress_pdf_import",
}


def _run_lo(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run LibreOffice with an isolated user profile to avoid lock conflicts.

    Note: the bootstrap variable is `-env:` (single dash). LibreOffice 24.2+
    rejects the double-dash `--env:` form with "Error in option", which fails
    the whole conversion.
    """
    with tempfile.TemporaryDirectory() as profile_dir:
        result = subprocess.run(
            ["libreoffice", "--headless",
             f"-env:UserInstallation=file://{profile_dir}",
             *args],
            capture_output=True, timeout=timeout,
        )
    return result


@router.post("/convert/import", dependencies=[Depends(verify_secret)])
async def import_file(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMPORT_EXTS:
        raise HTTPException(400, f"Unsupported format '{suffix}'. Accepted: {', '.join(sorted(IMPORT_EXTS))}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        src = tmpdir / (file.filename or f"file{suffix}")
        src.write_bytes(await read_limited(file, MAX_PDF_BYTES))

        result = _run_lo(["--convert-to", "pdf", "--outdir", str(tmpdir), str(src)])
        if result.returncode != 0:
            raise HTTPException(500, f"Conversion failed: {result.stderr.decode()[:500]}")

        pdf_files = list(tmpdir.glob("*.pdf"))
        if not pdf_files:
            raise HTTPException(500, "LibreOffice produced no output")

        job_id = str(uuid.uuid4())
        job_dir = STORE / job_id
        job_dir.mkdir(parents=True)

        stem = Path(file.filename or "document").stem
        out_name = f"{stem}.pdf"
        shutil.copy(pdf_files[0], job_dir / "file.pdf")

        import json, time
        meta = {"jobId": job_id, "filename": out_name,
                 "size": (job_dir / "file.pdf").stat().st_size, "createdAt": time.time()}
        (job_dir / "meta.json").write_text(json.dumps(meta))

        return {"jobId": job_id, "filename": out_name}


@router.get("/convert/{job_id}/export", dependencies=[Depends(verify_secret)])
async def export_file(job_id: str, format: str = Query(...)):
    if format not in EXPORT_MIME:
        raise HTTPException(400, f"Unsupported format '{format}'. Accepted: {', '.join(EXPORT_MIME)}")

    pdf_path = get_pdf_path(job_id)
    mime = EXPORT_MIME[format]

    if format == "pdfa":
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "output.pdf"
            result = subprocess.run([
                "gs", "-dPDFA=2", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE",
                "-sColorConversionStrategy=UseDeviceIndependentColor",
                "-sDEVICE=pdfwrite", "-dPDFACompatibilityPolicy=1",
                f"-sOutputFile={out}", str(pdf_path),
            ], capture_output=True, timeout=120)
            if result.returncode != 0:
                raise HTTPException(500, f"PDF/A conversion failed: {result.stderr.decode()[:500]}")
            data = out.read_bytes()
        return Response(data, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{job_id}.pdf"'})

    if format == "png":
        doc = pymupdf.open(str(pdf_path))
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "pages.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                    zf.writestr(f"page_{i + 1:03d}.png", pix.tobytes("png"))
            doc.close()
            data = zip_path.read_bytes()
        return Response(data, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{job_id}_pages.zip"'})

    # docx / xlsx / pptx via LibreOffice
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        lo_args = ["--convert-to", format, "--outdir", str(tmpdir), str(pdf_path)]
        infilter = PDF_IMPORT_FILTER.get(format)
        if infilter:
            lo_args = [f"--infilter={infilter}", *lo_args]
        result = _run_lo(lo_args, timeout=180)
        if result.returncode != 0:
            raise HTTPException(500, f"Conversion failed: {result.stderr.decode()[:500]}")
        out_files = list(tmpdir.glob(f"*.{format}"))
        if not out_files:
            raise HTTPException(500, "LibreOffice produced no output")
        data = out_files[0].read_bytes()

    return Response(data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{job_id}.{format}"'})
