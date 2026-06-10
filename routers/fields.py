import pymupdf
from fastapi import APIRouter, Depends, HTTPException
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()

_FIELD_TYPES = {
    "text":      pymupdf.PDF_WIDGET_TYPE_TEXT,
    "checkbox":  pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    "radio":     pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
    "dropdown":  pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    "listbox":   pymupdf.PDF_WIDGET_TYPE_LISTBOX,
    "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
}


@router.post("/fields/create/{job_id}")
async def create_field(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: {
      page: int (0-indexed),
      rect: [x0, y0, x1, y1] in PDF points (scale=1),
      type: "text"|"checkbox"|"radio"|"dropdown"|"listbox"|"signature",
      name: str,
      choices: [str, ...],   # for dropdown/listbox
      required: bool,
      multiline: bool,       # for text
    }
    """
    page_idx  = int(body.get("page", 0))
    rect      = body.get("rect", [50, 50, 250, 75])
    ftype_key = body.get("type", "text")
    name      = (body.get("name") or "").strip() or f"field_{page_idx}"
    choices   = [str(c) for c in body.get("choices", []) if str(c).strip()]
    required  = bool(body.get("required", False))
    multiline = bool(body.get("multiline", False))

    ft = _FIELD_TYPES.get(ftype_key)
    if ft is None:
        raise HTTPException(400, f"Unknown field type: {ftype_key}")

    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    if page_idx < 0 or page_idx >= len(doc):
        doc.close()
        raise HTTPException(400, "Page out of range")

    page = doc[page_idx]

    widget = pymupdf.Widget()
    widget.field_type = ft
    widget.field_name = name
    widget.rect = pymupdf.Rect(rect[0], rect[1], rect[2], rect[3])

    if ftype_key in ("dropdown", "listbox") and choices:
        widget.choice_values = choices
        widget.field_value = choices[0]

    if ftype_key == "checkbox":
        widget.field_value = "Off"

    if ftype_key == "text" and multiline:
        widget.field_flags = pymupdf.PDF_FIELD_IS_MULTILINE

    if required:
        widget.field_flags = getattr(widget, "field_flags", 0) | pymupdf.PDF_FIELD_IS_REQUIRED

    page.add_widget(widget)
    save_pdf(doc, job_id)
    return {"ok": True, "name": name, "type": ftype_key}


@router.delete("/fields/delete/{job_id}")
async def delete_field(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { page: int, name: str }"""
    page_idx = int(body.get("page", 0))
    name     = body.get("name", "")

    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    if page_idx < 0 or page_idx >= len(doc):
        doc.close()
        raise HTTPException(400, "Page out of range")

    page = doc[page_idx]
    for widget in page.widgets():
        if widget.field_name == name:
            page.delete_widget(widget)
            save_pdf(doc, job_id)
            return {"ok": True}

    doc.close()
    raise HTTPException(404, "Field not found")
