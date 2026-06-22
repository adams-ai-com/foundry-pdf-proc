"""
Envelope signing router — persistent storage, multi-signer PDF embedding.

Envelope store layout:
  {ENVELOPE_STORE}/{envelope_id}/working.pdf   — mutated after each signer
  {ENVELOPE_STORE}/{envelope_id}/original.pdf  — copy at creation (immutable)
"""
import asyncio
import io
import os
import base64
import uuid
import shutil
import datetime
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response, FileResponse, JSONResponse
from pydantic import BaseModel
import pymupdf

from deps import verify_secret, STORE

ENVELOPE_STORE = Path(os.environ.get("FOUNDRY_PDF_ENVELOPE_STORE",
                                     "data/envelopes"))

# Per-envelope write lock so concurrent requests don't corrupt pyhanko signatures
_envelope_locks: dict[str, threading.Lock] = {}
_locks_mu = threading.Lock()

def _lock(envelope_id: str) -> threading.Lock:
    with _locks_mu:
        if envelope_id not in _envelope_locks:
            _envelope_locks[envelope_id] = threading.Lock()
        return _envelope_locks[envelope_id]

router = APIRouter(prefix="/envelope-sign", dependencies=[Depends(verify_secret)])

RENDER_SCALE = 1.5


# ── Helpers ───────────────────────────────────────────────────────────────────

def _working_path(envelope_id: str) -> Path:
    p = ENVELOPE_STORE / envelope_id / "working.pdf"
    if not p.exists():
        raise HTTPException(404, "Envelope PDF not found")
    return p


def _parse_image(image_b64: str) -> bytes:
    """Strip data-URL prefix if present, return raw PNG bytes."""
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]
    return base64.b64decode(image_b64)


def _draw_field_visual(page: pymupdf.Page,
                       rect: pymupdf.Rect,
                       signer_name: str,
                       image_b64: str | None = None,
                       text: str | None = None,
                       field_type: str = "signature"):
    """Paint a professional visual signature block onto the page."""
    # Subtle background fill
    page.draw_rect(rect, color=None, fill=(0.97, 0.97, 1.0), overlay=True, width=0)

    # Reserve 12pt at the bottom for the signer attribution strip
    attr_height = min(12.0, (rect.y1 - rect.y0) * 0.2)
    inner_rect = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1 - attr_height)
    attr_rect  = pymupdf.Rect(rect.x0 + 2, rect.y1 - attr_height, rect.x1 - 2, rect.y1)

    if image_b64:
        img_bytes = _parse_image(image_b64)
        page.insert_image(inner_rect, stream=img_bytes, overlay=True)
    elif text:
        # Typed / date / name value
        font_size = min(16.0, (rect.y1 - rect.y0 - attr_height) * 0.5)
        page.insert_textbox(
            inner_rect, text,
            fontname="helv-o" if field_type in ("signature", "initials") else "helv",
            fontsize=font_size,
            color=(0.1, 0.1, 0.4),
            align=pymupdf.TEXT_ALIGN_CENTER,
            overlay=True,
        )

    # Attribution: "Name | Date"
    signed_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    page.insert_textbox(
        attr_rect,
        f"{signer_name}  ·  {signed_date}",
        fontname="helv",
        fontsize=6,
        color=(0.5, 0.5, 0.5),
        align=pymupdf.TEXT_ALIGN_LEFT,
        overlay=True,
    )

    # Thin blue border
    page.draw_rect(rect, color=(0.2, 0.2, 0.65), width=0.75, overlay=True)


def _generate_cert(signer_name: str, signer_email: str | None):
    """Generate a fresh self-signed PKCS#12 cert for one signer."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, signer_name)]
    if signer_email:
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, signer_email))
    subject = issuer = x509.Name(attrs)
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    passphrase = os.urandom(16).hex().encode()
    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=signer_name.encode(),
        key=key, cert=cert, cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    )
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return p12_bytes, passphrase, fingerprint


# ── Endpoints ─────────────────────────────────────────────────────────────────

class CopyRequest(BaseModel):
    job_id: str
    envelope_id: str


@router.post("/copy-to-store")
async def copy_to_store(req: CopyRequest):
    """Copy a job PDF to the persistent envelope store at creation time."""
    job_pdf = STORE / req.job_id / "file.pdf"
    if not job_pdf.exists():
        raise HTTPException(404, f"Job {req.job_id} not found")

    env_dir = ENVELOPE_STORE / req.envelope_id
    env_dir.mkdir(parents=True, exist_ok=True)

    working = env_dir / "working.pdf"
    shutil.copy2(str(job_pdf), str(working))
    shutil.copy2(str(job_pdf), str(env_dir / "original.pdf"))

    doc = pymupdf.open(str(working))
    pages = [{"width": p.rect.width, "height": p.rect.height} for p in doc]
    doc.close()

    return {"ok": True, "pages": pages}


@router.get("/info/{envelope_id}")
async def get_info(envelope_id: str):
    pdf_path = _working_path(envelope_id)
    doc = pymupdf.open(str(pdf_path))
    pages = []
    for page in doc:
        r = page.rect
        pages.append({"width": r.width, "height": r.height,
                       "widthPx": round(r.width * RENDER_SCALE),
                       "heightPx": round(r.height * RENDER_SCALE)})
    doc.close()
    return {"pageCount": len(pages), "pages": pages}


@router.get("/page/{envelope_id}/{page_num}")
async def render_page(envelope_id: str, page_num: int):
    pdf_path = _working_path(envelope_id)
    doc = pymupdf.open(str(pdf_path))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        raise HTTPException(404, "Page out of range")
    page = doc[page_num]
    mat = pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE)
    pix = page.get_pixmap(matrix=mat)
    png = pix.tobytes("png")
    doc.close()
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=120"})


@router.get("/file/{envelope_id}")
async def get_file(envelope_id: str):
    pdf_path = _working_path(envelope_id)
    return FileResponse(str(pdf_path), media_type="application/pdf")


class FieldInput(BaseModel):
    field_id: str
    page: int
    x0: float; y0: float; x1: float; y1: float
    field_type: str            # signature | initials | date | name
    image_b64: str | None = None
    text: str | None = None


class EmbedRequest(BaseModel):
    recipient_id: str
    signer_name: str
    signer_email: str | None = None
    reason: str | None = None
    fields: list[FieldInput]


def _do_embed(envelope_id: str, req: EmbedRequest) -> dict:
    """
    Blocking portion of embed — runs in a thread executor so pyhanko's
    internal asyncio.run() calls don't clash with FastAPI's running loop.
    """
    from pyhanko.sign import signers
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    lock = _lock(envelope_id)
    with lock:
        pdf_path = _working_path(envelope_id)
        pdf_bytes = pdf_path.read_bytes()

        # ── Step 1: draw visual marks via PyMuPDF ────────────────────────────
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        for field in req.fields:
            if field.page < 0 or field.page >= len(doc):
                continue
            page = doc[field.page]
            rect = pymupdf.Rect(field.x0, field.y0, field.x1, field.y1)
            _draw_field_visual(
                page, rect,
                signer_name=req.signer_name,
                image_b64=field.image_b64,
                text=field.text,
                field_type=field.field_type,
            )

        visual_buf = io.BytesIO()
        doc.save(visual_buf, garbage=4, deflate=True)
        doc.close()
        visual_bytes = visual_buf.getvalue()

        # ── Step 2: cryptographic signature via pyhanko ───────────────────────
        cert_bytes, passphrase, fingerprint = _generate_cert(
            req.signer_name, req.signer_email
        )
        try:
            signer = signers.SimpleSigner.load_pkcs12_data(
                cert_bytes, [], passphrase=passphrase
            )
            w = IncrementalPdfFileWriter(io.BytesIO(visual_bytes))
            meta = PdfSignatureMetadata(
                field_name=f"EnvSig_{uuid.uuid4().hex[:8]}",
                reason=req.reason or "Signed via Foundry PDF",
                name=req.signer_name,
                location="",
            )
            out_buf = io.BytesIO()
            signers.sign_pdf(w, signature_meta=meta, signer=signer, output=out_buf)
            final_bytes = out_buf.getvalue()
        except Exception as exc:
            raise RuntimeError(f"Cryptographic signing failed: {exc}") from exc

        # ── Step 3: atomic write ──────────────────────────────────────────────
        tmp = pdf_path.with_suffix(".tmp")
        tmp.write_bytes(final_bytes)
        tmp.replace(pdf_path)

        return {"ok": True, "cert_fingerprint": fingerprint, "size": len(final_bytes)}


@router.post("/embed/{envelope_id}")
async def embed_signature(envelope_id: str, req: EmbedRequest):
    """
    Apply one signer's visual marks + cryptographic pyhanko signature to
    working.pdf.  Offloads blocking pyhanko work to a thread executor so
    pyhanko's internal asyncio.run() calls don't conflict with uvicorn's loop.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _do_embed, envelope_id, req)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    return result


# ── Certificate of Completion ─────────────────────────────────────────────────

class SignerInfo(BaseModel):
    name: str
    email: str | None = None
    signed_at: str | None = None
    ip_address: str | None = None
    cert_fingerprint: str | None = None
    order_index: int = 0

class EventInfo(BaseModel):
    event: str
    created_at: str
    actor: str | None = None

class CertRequest(BaseModel):
    title: str
    envelope_id: str
    created_at: str
    completed_at: str | None = None
    signers: list[SignerInfo] = []
    events: list[EventInfo] = []

@router.post("/certificate/{envelope_id}")
async def generate_certificate(envelope_id: str, req: CertRequest):
    import hashlib
    pdf_path = ENVELOPE_STORE / envelope_id / "working.pdf"
    doc_hash = ""
    if pdf_path.exists():
        h = hashlib.sha256()
        with open(pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        doc_hash = h.hexdigest()

    pymupdf_ver = pymupdf.version[0]
    mupdf_ver   = pymupdf.version[2]

    doc  = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    margin = 72
    y = margin
    right_edge = 612 - margin

    def line(text, size, bold=False, mono=False, color=(0,0,0), indent=0):
        nonlocal y
        if not text:
            y += size * 0.7
            return
        fn = "cour" if mono else ("hebo" if bold else "helv")
        page.insert_text((margin + indent, y), text, fontname=fn, fontsize=size, color=color)
        y += size * 1.5

    def rule():
        nonlocal y
        page.draw_line((margin, y), (right_edge, y), color=(0.7, 0.7, 0.7), width=0.5)
        y += 10

    line("CERTIFICATE OF COMPLETION", 18, bold=True)
    line("Foundry PDF  ·  Digital Signing Record", 9, color=(0.45, 0.45, 0.45))
    y += 8
    rule()

    line("DOCUMENT", 8, bold=True, color=(0.45, 0.45, 0.45))
    y += 2
    line(f"Title:       {req.title}", 10)
    line(f"Envelope ID: {req.envelope_id}", 10)
    line(f"Created:     {req.created_at}", 10)
    if req.completed_at:
        line(f"Completed:   {req.completed_at}", 10)
    if doc_hash:
        y += 4
        line("SHA-256 (signed document):", 9, color=(0.3, 0.3, 0.3))
        line(f"  {doc_hash}", 8, mono=True)
    y += 10
    rule()

    line("SIGNERS", 8, bold=True, color=(0.45, 0.45, 0.45))
    y += 2
    for i, s in enumerate(req.signers, 1):
        line(f"{i}.  {s.name}", 10, bold=True)
        if s.email:
            line(f"    Email:     {s.email}", 9)
        if s.signed_at:
            line(f"    Signed:    {s.signed_at}", 9)
        if s.ip_address:
            line(f"    IP:        {s.ip_address}", 9)
        if s.cert_fingerprint:
            line(f"    Cert:      {s.cert_fingerprint[:32]}...", 8, mono=True)
        y += 4
    y += 6
    rule()

    line("ACTIVITY LOG", 8, bold=True, color=(0.45, 0.45, 0.45))
    y += 2
    for ev in req.events:
        label = ev.event.replace("_", " ").title()
        ts = ev.created_at[:19].replace("T", " ") if ev.created_at else ""
        actor_part = f"  ({ev.actor})" if ev.actor else ""
        line(f"  {ts}  {label}{actor_part}", 8.5)
    y += 10
    rule()

    line(f"Tool: Foundry PDF  (PyMuPDF {pymupdf_ver}, MuPDF {mupdf_ver})", 8, color=(0.5,0.5,0.5))
    line("This is an audit record of the signing session above.", 8, color=(0.5,0.5,0.5))
    line("The cryptographic signature embedded in the PDF provides tamper-evidence.", 8, color=(0.5,0.5,0.5))

    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()

    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in req.title)[:40].strip()
    fname = f"completion-certificate-{safe}-{envelope_id[:8]}.pdf"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
