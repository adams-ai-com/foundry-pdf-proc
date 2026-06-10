from fastapi import APIRouter, Depends
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()


def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r / 255, g / 255, b / 255)


def scale_rect(rect_px, scale: float):
    return pymupdf.Rect(
        rect_px[0] / scale,
        rect_px[1] / scale,
        rect_px[2] / scale,
        rect_px[3] / scale,
    )


@router.post("/annotate/{job_id}")
async def annotate(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: {
      type: 'textbox' | 'sticky' | 'arrow' | 'freehand',
      page: int,
      scale: float,           -- render scale used by client (to convert px → pts)
      color: '#rrggbb',
      -- textbox / sticky:
      rect: [x0,y0,x1,y1],   -- in PNG pixel coords
      content: str,
      fontSize: int,
      -- arrow:
      p1: [x,y], p2: [x,y],  -- in PNG pixel coords
      -- freehand:
      inkList: [[x,y], ...],  -- in PNG pixel coords (single stroke)
    }
    """
    annot_type = body.get("type")
    page_num   = int(body.get("page", 0))
    scale      = float(body.get("scale", 1.5))
    color_hex  = body.get("color", "#000000")
    rgb        = hex_to_rgb(color_hex)

    pdf_path = get_pdf_path(job_id)
    doc  = pymupdf.open(str(pdf_path))
    page = doc[page_num]

    if annot_type == "textbox":
        rect    = scale_rect(body["rect"], scale)
        content = body.get("content", "")
        fontsize = int(body.get("fontSize", 12))
        page.insert_textbox(
            rect, content,
            fontsize=fontsize, color=rgb,
            fontname="helv", overlay=True, align=0,
        )

    elif annot_type == "sticky":
        rect    = body["rect"]
        point   = pymupdf.Point(rect[0] / scale, rect[1] / scale)
        content = body.get("content", "")
        annot   = page.add_text_annot(point, content)
        annot.set_colors(stroke=rgb)
        annot.update()

    elif annot_type == "arrow":
        p1 = pymupdf.Point(body["p1"][0] / scale, body["p1"][1] / scale)
        p2 = pymupdf.Point(body["p2"][0] / scale, body["p2"][1] / scale)
        annot = page.add_line_annot(p1, p2)
        annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        annot.set_colors(stroke=rgb)
        annot.set_border(width=2)
        annot.update()

    elif annot_type == "freehand":
        raw_points = body.get("inkList", [])
        # raw_points is [[x,y], ...] — a single stroke
        pts = [pymupdf.Point(p[0] / scale, p[1] / scale) for p in raw_points]
        if len(pts) >= 2:
            annot = page.add_ink_annot([pts])
            annot.set_colors(stroke=rgb)
            annot.set_border(width=2)
            annot.update()

    elif annot_type in ("highlight", "underline", "strikethrough"):
        # quads: list of [x0,y0,x1,y1] in PDF points (scale=1 sent from client)
        raw_quads = body.get("quads", [])
        rects = [pymupdf.Rect(q[0]/scale, q[1]/scale, q[2]/scale, q[3]/scale) for q in raw_quads]
        if rects:
            if annot_type == "highlight":
                annot = page.add_highlight_annot(rects)
                annot.set_colors(stroke=rgb)
            elif annot_type == "underline":
                annot = page.add_underline_annot(rects)
                annot.set_colors(stroke=rgb)
            else:
                annot = page.add_strikeout_annot(rects)
                annot.set_colors(stroke=rgb)
            annot.update()

    elif annot_type in ("rect", "circle"):
        rect = scale_rect(body["rect"], scale)
        if annot_type == "rect":
            annot = page.add_rect_annot(rect)
        else:
            annot = page.add_circle_annot(rect)
        annot.set_colors(stroke=rgb, fill=None)
        annot.set_border(width=float(body.get("lineWidth", 1.5)))
        annot.update()

    elif annot_type == "line":
        p1 = pymupdf.Point(body["p1"][0] / scale, body["p1"][1] / scale)
        p2 = pymupdf.Point(body["p2"][0] / scale, body["p2"][1] / scale)
        annot = page.add_line_annot(p1, p2)
        annot.set_colors(stroke=rgb)
        annot.set_border(width=float(body.get("lineWidth", 1.5)))
        annot.update()

    elif annot_type == "stamp":
        stamp_map = {
            "Approved": 0, "As-Is": 1, "Confidential": 2, "Departmental": 3,
            "Draft": 4, "Experimental": 5, "Expired": 6, "Final": 7,
            "For Comment": 8, "For Public Release": 9, "Not Approved": 10,
            "Not For Public Release": 11, "Top Secret": 13,
        }
        stamp_id = stamp_map.get(body.get("stamp", "Draft"), 4)
        rect = scale_rect(body["rect"], scale)
        annot = page.add_stamp_annot(rect, stamp=stamp_id)
        annot.update()

    else:
        doc.close()
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown annotation type: {annot_type}")

    save_pdf(doc, job_id)
    return {"ok": True}


@router.patch("/annotate/{job_id}")
async def move_annot(job_id: str, body: dict, _=Depends(verify_secret)):
    """
    body: { page: int, index: int, rect: [x0,y0,x1,y1] }  — PDF point coords
    Moves/resizes an existing annotation by its within-page index.
    """
    from fastapi import HTTPException
    page_num = int(body.get("page", 0))
    index    = int(body.get("index", 0))
    rect     = body.get("rect")
    if not rect or len(rect) != 4:
        raise HTTPException(400, "rect [x0,y0,x1,y1] is required")

    pdf_path = get_pdf_path(job_id)
    doc  = pymupdf.open(str(pdf_path))
    page = doc[page_num]
    annots = list(page.annots())
    if index >= len(annots):
        doc.close()
        raise HTTPException(400, f"Annotation index {index} out of range ({len(annots)} annotations on page)")

    annot = annots[index]
    annot.set_rect(pymupdf.Rect(rect[0], rect[1], rect[2], rect[3]))
    annot.update()
    save_pdf(doc, job_id)
    return {"ok": True}


@router.delete("/annotate/{job_id}")
async def delete_annot(job_id: str, body: dict, _=Depends(verify_secret)):
    """body: { page: int, index: int }"""
    from fastapi import HTTPException
    page_num = int(body.get("page", 0))
    index    = int(body.get("index", 0))
    pdf_path = get_pdf_path(job_id)
    doc  = pymupdf.open(str(pdf_path))
    page = doc[page_num]
    annots = list(page.annots())
    if index >= len(annots):
        doc.close()
        raise HTTPException(400, "Annotation index out of range")
    page.delete_annot(annots[index])
    save_pdf(doc, job_id)
    return {"ok": True}
