import io
import uuid
import base64
import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from deps import verify_secret, STORE, save_pdf

router = APIRouter(prefix="/sign", dependencies=[Depends(verify_secret)])


class GenerateCertRequest(BaseModel):
    name: str
    email: str | None = None
    org: str | None = None
    valid_days: int = 365


class SignRequest(BaseModel):
    cert_b64: str          # base64-encoded .p12 bytes
    passphrase: str
    signer_name: str | None = None
    reason: str | None = None
    location: str | None = None
    page: int = 0          # 0-indexed page to place visible sig
    visible: bool = True


@router.post("/generate-cert")
async def generate_cert(req: GenerateCertRequest):
    """Generate a self-signed PKCS#12 certificate for signing."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, req.name)]
    if req.email:
        name_attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, req.email))
    if req.org:
        name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, req.org))

    subject = issuer = x509.Name(name_attrs)
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=req.valid_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    passphrase = str(uuid.uuid4()).replace("-", "")[:16].encode()
    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=req.name.encode(),
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    )

    return {
        "cert_b64": base64.b64encode(p12_bytes).decode(),
        "passphrase": passphrase.decode(),
        "expires": (now + datetime.timedelta(days=req.valid_days)).isoformat() + "Z",
        "subject": req.name,
    }


@router.post("/{job_id}")
async def sign_pdf(job_id: str, req: SignRequest):
    """Apply a cryptographic signature to the PDF."""
    job_dir = STORE / job_id
    pdf_path = job_dir / "file.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        cert_bytes = base64.b64decode(req.cert_b64)
        passphrase = req.passphrase.encode()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cert_b64")

    try:
        from pyhanko.sign import signers
        from pyhanko.sign.fields import SigFieldSpec, append_signature_field
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

        pdf_bytes = pdf_path.read_bytes()

        signer = signers.SimpleSigner.load_pkcs12(
            pfx_data=cert_bytes,
            passphrase=passphrase,
        )

        w = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))

        field_name = f"Sig_{uuid.uuid4().hex[:8]}"

        if req.visible:
            # Place visible signature block at bottom-left of requested page
            spec = SigFieldSpec(field_name, on_page=req.page, box=(36, 36, 280, 100))
            append_signature_field(w, spec)

        meta = PdfSignatureMetadata(
            field_name=field_name,
            reason=req.reason or "Signed with Foundry PDF",
            location=req.location or "",
            name=req.signer_name or "",
        )

        outbuf = io.BytesIO()
        signers.sign_pdf(w, signature_meta=meta, signer=signer, output=outbuf)

        signed_bytes = outbuf.getvalue()
        pdf_path.write_bytes(signed_bytes)

        # Clear render cache so next page load reflects signed PDF
        cache_dir = job_dir / "cache"
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)

        return {"ok": True, "field": field_name, "page": req.page}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Signing failed: {exc}")
