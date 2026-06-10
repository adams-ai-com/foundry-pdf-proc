from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


@router.post("/header-footer/{job_id}")
async def apply_header_footer(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: {
      header: str,     -- text; {page} and {total} are substituted
      footer: str,
      fontSize: int,   -- default 10
      color: '#rrggbb',
      margin: float    -- points from edge, default 20
    }
    """
    header_text = body.get("header", "")
    footer_text = body.get("footer", "")
    font_size   = int(body.get("fontSize", 10))
    margin      = float(body.get("margin", 20))
    rgb         = _hex_to_rgb(body.get("color", "#666666"))

    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    total = len(doc)

    for i, page in enumerate(doc):
        pw = page.rect.width
        ph = page.rect.height
        n  = i + 1

        if header_text:
            text = header_text.replace("{page}", str(n)).replace("{total}", str(total))
            rect = pymupdf.Rect(margin, margin, pw - margin, margin + font_size + 8)
            page.insert_textbox(rect, text, fontsize=font_size, color=rgb,
                                fontname="helv", align=1, overlay=True)

        if footer_text:
            text = footer_text.replace("{page}", str(n)).replace("{total}", str(total))
            rect = pymupdf.Rect(margin, ph - margin - font_size - 8, pw - margin, ph - margin)
            page.insert_textbox(rect, text, fontsize=font_size, color=rgb,
                                fontname="helv", align=1, overlay=True)

    save_pdf(doc, job_id)
    return {"ok": True, "pages": total}
