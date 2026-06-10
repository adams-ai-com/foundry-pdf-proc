from fastapi import APIRouter, Depends
from fastapi.responses import Response
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()

_TYPE_TO_STR = {
    pymupdf.PDF_WIDGET_TYPE_TEXT:        "text",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX:    "checkbox",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX:    "dropdown",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX:     "listbox",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE:   "signature",
    pymupdf.PDF_WIDGET_TYPE_BUTTON:      "button",
}

_STR_TO_TYPE = {
    "text":      pymupdf.PDF_WIDGET_TYPE_TEXT,
    "number":    pymupdf.PDF_WIDGET_TYPE_TEXT,
    "date":      pymupdf.PDF_WIDGET_TYPE_TEXT,
    "checkbox":  pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    "radio":     pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
    "dropdown":  pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
}

_STR_TO_FORMAT = {
    "number": pymupdf.PDF_WIDGET_TX_FORMAT_NUMBER,
    "date":   pymupdf.PDF_WIDGET_TX_FORMAT_DATE,
}


def _widget_value_str(widget) -> str:
    v = widget.field_value
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    return str(v)


@router.get("/forms/{job_id}/fields")
async def list_fields(job_id: str, _=Depends(verify_secret)):
    doc = pymupdf.open(str(get_pdf_path(job_id)))
    fields = []
    for page_num, page in enumerate(doc):
        for w in page.widgets():
            r = w.rect
            fields.append({
                "name":     w.field_name or "",
                "label":    w.field_label or w.field_name or "",
                "type":     _TYPE_TO_STR.get(w.field_type, "text"),
                "page":     page_num,
                "rect":     [r.x0, r.y0, r.x1, r.y1],
                "value":    _widget_value_str(w),
                "options":  list(w.choice_values or []),
                "required": bool(w.field_flags & 2),
            })
    doc.close()
    return {"fields": fields}


@router.post("/forms/{job_id}/add")
async def add_field(job_id: str, body: dict, _=Depends(verify_secret)):
    type_str = body.get("type", "text")
    page_num = int(body.get("page", 0))
    scale    = float(body.get("scale", 1.5))
    rect_px  = body.get("rect", [50, 100, 200, 120])
    name     = str(body.get("name", "field")).strip() or "field"
    label    = str(body.get("label", name))
    options  = [str(o) for o in body.get("options", [])]
    required = bool(body.get("required", False))

    rect = pymupdf.Rect(
        rect_px[0] / scale, rect_px[1] / scale,
        rect_px[2] / scale, rect_px[3] / scale,
    )

    doc  = pymupdf.open(str(get_pdf_path(job_id)))
    page = doc[page_num]

    w = pymupdf.Widget()
    w.rect       = rect
    w.field_type = _STR_TO_TYPE.get(type_str, pymupdf.PDF_WIDGET_TYPE_TEXT)
    w.field_name  = name
    w.field_label = label
    w.text_fontsize = 10
    w.border_width  = 1

    if type_str == "checkbox":
        w.field_value = False
    elif type_str == "radio":
        w.field_value = options[0] if options else "Yes"
        w.on_state    = options[0] if options else "Yes"
    elif type_str == "dropdown" and options:
        w.choice_values = options
        w.field_value   = options[0]
    elif type_str == "number":
        w.text_format = pymupdf.PDF_WIDGET_TX_FORMAT_NUMBER
        w.field_value = ""
    elif type_str == "date":
        w.text_format = pymupdf.PDF_WIDGET_TX_FORMAT_DATE
        w.field_value = ""
    else:
        w.field_value = ""

    if required:
        w.field_flags = 2

    page.add_widget(w)
    save_pdf(doc, job_id)
    return {"ok": True}


@router.post("/forms/{job_id}/fill")
async def fill_fields(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { fields: { "field_name": "value", ... } }"""
    values: dict = body.get("fields", {})
    doc = pymupdf.open(str(get_pdf_path(job_id)))
    for page in doc:
        for w in page.widgets():
            if w.field_name in values:
                raw = values[w.field_name]
                if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                    w.field_value = raw in ("true", "True", "1", "yes", "Yes", True)
                else:
                    w.field_value = str(raw)
                w.update()
    save_pdf(doc, job_id)
    return {"ok": True}


@router.post("/forms/{job_id}/remove")
async def remove_field(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { name: str, page: int }"""
    name     = body.get("name", "")
    page_num = int(body.get("page", 0))
    doc  = pymupdf.open(str(get_pdf_path(job_id)))
    page = doc[page_num]
    removed = 0
    for w in list(page.widgets()):
        if w.field_name == name:
            page.delete_widget(w)
            removed += 1
    save_pdf(doc, job_id)
    return {"removed": removed}


@router.get("/forms/{job_id}/flat")
async def download_flat(job_id: str, _=Depends(verify_secret)):
    """Return a flattened PDF (form fields baked into static content, not modifying stored file)."""
    doc = pymupdf.open(str(get_pdf_path(job_id)))
    for page in doc:
        try:
            page.bake(annots=False, widgets=True)
        except AttributeError:
            pass  # older PyMuPDF without bake()
    buf = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return Response(
        content=buf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=flattened.pdf"},
    )


@router.post("/forms/{job_id}/sign")
async def sign_field(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: {
      name: str,               -- widget field name to replace
      page: int,
      rect: [x0,y0,x1,y1],    -- PDF point coordinates
      image_data: str,         -- data:image/png;base64,... from canvas
    }
    Removes the signature widget and embeds the PNG as an image annotation.
    """
    import base64
    name       = body.get("name", "")
    page_num   = int(body.get("page", 0))
    rect_pts   = body.get("rect", [50, 100, 200, 150])
    image_data = body.get("image_data", "")

    if "," in image_data:
        image_data = image_data.split(",", 1)[1]
    png_bytes = base64.b64decode(image_data)

    doc  = pymupdf.open(str(get_pdf_path(job_id)))
    page = doc[page_num]

    for w in list(page.widgets()):
        if w.field_name == name:
            page.delete_widget(w)
            break

    rect = pymupdf.Rect(rect_pts[0], rect_pts[1], rect_pts[2], rect_pts[3])
    page.insert_image(rect, stream=png_bytes)

    save_pdf(doc, job_id)
    return {"ok": True}
