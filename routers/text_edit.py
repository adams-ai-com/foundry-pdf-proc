import collections
import logging
import time
from fastapi import APIRouter, Depends, HTTPException
import pymupdf
from deps import verify_secret, get_pdf_path, save_pdf

router = APIRouter()
log    = logging.getLogger("text_edit")


def _match_font(font_name: str, flags: int) -> str:
    bold  = bool(flags & 16)
    fn    = (font_name or '').lower()
    mono  = bool(flags & 8)  or any(x in fn for x in ('courier', 'cour', 'cobo', 'mono', 'consol', 'typewriter'))
    serif = bool(flags & 4)  or any(x in fn for x in ('times', 'tiro', 'tibo', 'georgia', 'garamond', 'palatino', 'charter', 'minion'))
    if mono:
        return 'cobo' if bold else 'cour'
    if serif:
        return 'tibo' if bold else 'tiro'
    return 'hebo' if bold else 'helv'


def _unpack_color(c) -> list:
    if isinstance(c, (list, tuple)):
        vals = list(c)
        if all(isinstance(v, float) and v <= 1.0 for v in vals):
            return vals[:3]
        return [v / 255.0 for v in vals[:3]]
    return [((c >> 16) & 0xFF) / 255.0, ((c >> 8) & 0xFF) / 255.0, (c & 0xFF) / 255.0]


def _detect_bg(page, rect, scale: float = 3.0):
    """Background color behind a text span, sampled AROUND the glyphs, never on
    them. Renders thin bands just above and below the span at high DPI and
    returns the modal (most-common) color — so anti-aliased glyph edges and
    stray ink from neighboring lines are rejected rather than averaged into
    gray. Near-white snaps to pure white to avoid a faint ghost rectangle.
    Falls back to white when no clean sample is available."""
    pad_x = max(2.0, (rect.x1 - rect.x0) * 0.05)
    band  = max(1.5, (rect.y1 - rect.y0) * 0.35)
    # Bands in the inter-line gaps directly above the ascenders and below the
    # descenders — the area a tight span bbox does NOT cover with ink.
    regions = [
        pymupdf.Rect(rect.x0 + pad_x, rect.y0 - band, rect.x1 - pad_x, rect.y0 - 0.5),
        pymupdf.Rect(rect.x0 + pad_x, rect.y1 + 0.5,  rect.x1 - pad_x, rect.y1 + band),
    ]
    counter = collections.Counter()
    for reg in regions:
        reg = reg & page.rect          # clip to page bounds
        if reg.is_empty or reg.width < 1 or reg.height < 1:
            continue
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                  clip=reg, colorspace=pymupdf.csRGB)
        except Exception:
            continue
        s, n = pix.samples, pix.n
        if n < 3:
            continue
        for i in range(0, len(s) - n + 1, n):
            counter[(s[i], s[i + 1], s[i + 2])] += 1
    if not counter:
        return (1.0, 1.0, 1.0)
    r, g, b = counter.most_common(1)[0][0]
    if r >= 250 and g >= 250 and b >= 250:   # snap near-white → pure white
        return (1.0, 1.0, 1.0)
    return (r / 255.0, g / 255.0, b / 255.0)


@router.get("/text-spans/{job_id}/{page_num}")
async def get_text_spans(job_id: str, page_num: int, _=Depends(verify_secret)):
    t0 = time.monotonic()
    log.info("spans  job=%s page=%d", job_id[:8], page_num)
    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        log.warning("spans  job=%s page=%d → out of range (total=%d)", job_id[:8], page_num, len(doc))
        return {"spans": []}
    page = doc[page_num]
    raw = page.get_text(
        "rawdict",
        flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES,
    )
    doc.close()
    spans = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                chars = span.get("chars", [])
                text  = "".join(ch.get("c", "") for ch in chars).strip()
                if not text:
                    continue
                bbox   = list(span["bbox"])
                origin = list(span.get("origin", [bbox[0], bbox[3]]))
                color  = _unpack_color(span.get("color", 0))
                spans.append({
                    "text":   text,
                    "bbox":   bbox,
                    "origin": origin,
                    "font":   span.get("font", ""),
                    "size":   span.get("size", 12.0),
                    "flags":  span.get("flags", 0),
                    "color":  color,
                })
    ms = (time.monotonic() - t0) * 1000
    log.info("spans  job=%s page=%d → %d spans  %.0fms", job_id[:8], page_num, len(spans), ms)
    return {"spans": spans}


@router.post("/edit-text/{job_id}")
async def edit_text_span(job_id: str, body: dict, _=Depends(verify_secret)):
    t0 = time.monotonic()

    page_num        = body.get("page")
    bbox            = body.get("bbox")
    origin          = body.get("origin")
    new_text        = (body.get("new_text") or "").strip()
    font            = str(body.get("font", ""))
    size            = float(body.get("size", 12))
    flags           = int(body.get("flags", 0))
    color           = body.get("color", [0.0, 0.0, 0.0])
    resolved_font   = body.get("resolved_font")
    italic_override = body.get("italic")

    action = "edit" if bbox else "insert"
    preview = repr(new_text[:40]) + ("…" if len(new_text) > 40 else "")
    log.info(
        "%s   job=%s page=%s bbox=%s origin=%s font=%s size=%s text=%s",
        action, job_id[:8], page_num,
        [round(v, 1) for v in bbox] if bbox else None,
        [round(v, 1) for v in origin] if origin else None,
        resolved_font or font or "?",
        size, preview,
    )

    if page_num is None or not origin:
        log.error("%s   job=%s → 400 missing page/origin", action, job_id[:8])
        raise HTTPException(400, "page and origin required")

    pdf_path = get_pdf_path(job_id)
    doc = pymupdf.open(str(pdf_path))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        log.error("%s   job=%s → 400 invalid page %s (total=%d)", action, job_id[:8], page_num, len(doc))
        raise HTTPException(400, "invalid page")

    page = doc[page_num]

    if bbox:
        rect = pymupdf.Rect(bbox)
        try:
            bg = _detect_bg(page, rect)
        except Exception as exc:
            log.warning("edit   job=%s bg-sample failed: %s", job_id[:8], exc)
            bg = (1.0, 1.0, 1.0)
        log.info("edit   job=%s redacting bbox=%s bg=%.2f,%.2f,%.2f", job_id[:8],
                 [round(v, 1) for v in bbox], *bg)
        page.add_redact_annot(rect + (-1, -1, 1, 1), fill=bg)
        page.apply_redactions()

    if new_text:
        fontname = resolved_font if resolved_font else _match_font(font, flags)
        col      = tuple(float(v) for v in _unpack_color(color)[:3])
        morph    = None
        use_italic = italic_override if italic_override is not None else bool(flags & 2)
        if use_italic:
            origin_pt = pymupdf.Point(origin[0], origin[1])
            morph = (origin_pt, pymupdf.Matrix(1, 0, 0.2, 1, 0, 0))
        log.info("%s   job=%s inserting font=%s size=%s italic=%s color=%.2f,%.2f,%.2f at (%.1f,%.1f)",
                 action, job_id[:8], fontname, size, use_italic, *col, origin[0], origin[1])
        page.insert_text(
            pymupdf.Point(origin[0], origin[1]),
            new_text,
            fontname=fontname,
            fontsize=size,
            color=col,
            morph=morph,
        )
    elif action == "edit":
        log.info("edit   job=%s text is empty — span deleted (redact only)", job_id[:8])

    save_pdf(doc, job_id)
    ms = (time.monotonic() - t0) * 1000
    log.info("%s   job=%s → ok  %.0fms", action, job_id[:8], ms)
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
# Paragraph (block) editing — spec: foundry-pdf-paragraph-editing.md
# ──────────────────────────────────────────────────────────────────────────

_ALIGN = {"left": 0, "center": 1, "right": 2, "justify": 3}


def _line_text(line) -> str:
    return "".join(span.get("text", "") for span in line.get("spans", []))


def _split_block_into_paragraphs(lines):
    """Split a dict-block's lines into paragraph groups on a large vertical gap
    (or a smaller gap plus a left-indent jump). Returns a list of line-lists."""
    lines = [ln for ln in lines if ln.get("bbox") and ln.get("spans")]
    if not lines:
        return []
    heights = sorted(ln["bbox"][3] - ln["bbox"][1] for ln in lines)
    med_h = heights[len(heights) // 2] or 12.0
    groups = [[lines[0]]]
    for prev, cur in zip(lines, lines[1:]):
        gap = cur["bbox"][1] - prev["bbox"][3]
        indent_jump = (cur["bbox"][0] - prev["bbox"][0]) > med_h
        if gap > 1.6 * med_h or (gap > 0.4 * med_h and indent_jump):
            groups.append([cur])
        else:
            groups[-1].append(cur)
    return groups


def _infer_align(para_lines, block_x0, block_x1):
    if len(para_lines) < 2:
        return "left"
    width = (block_x1 - block_x0) or 1.0
    tol = 0.04 * width
    avg_left = sum(ln["bbox"][0] - block_x0 for ln in para_lines) / len(para_lines)
    avg_right = sum(block_x1 - ln["bbox"][2] for ln in para_lines) / len(para_lines)
    if avg_right < tol and avg_left > tol:
        return "right"
    if abs(avg_left - avg_right) < tol and avg_left > tol:
        return "center"
    body = para_lines[:-1]
    if body and all((block_x1 - ln["bbox"][2]) < tol for ln in body):
        return "justify"
    return "left"


def _summarize_para(para_lines):
    """Flatten a paragraph's lines into editable prose + dominant style."""
    text = " ".join(" ".join(_line_text(ln).split()) for ln in para_lines).strip()
    x0 = min(ln["bbox"][0] for ln in para_lines)
    y0 = min(ln["bbox"][1] for ln in para_lines)
    x1 = max(ln["bbox"][2] for ln in para_lines)
    y1 = max(ln["bbox"][3] for ln in para_lines)
    fonts, sizes, colors, flags = (collections.Counter() for _ in range(4))
    for ln in para_lines:
        for sp in ln.get("spans", []):
            n = len(sp.get("text", ""))
            if not n:
                continue
            fonts[sp.get("font", "")] += n
            sizes[round(float(sp.get("size", 12.0)), 1)] += n
            colors[sp.get("color", 0)] += n
            flags[sp.get("flags", 0)] += n
    return {
        "text": text,
        "bbox": [x0, y0, x1, y1],
        "font": fonts.most_common(1)[0][0] if fonts else "",
        "size": sizes.most_common(1)[0][0] if sizes else 12.0,
        "flags": flags.most_common(1)[0][0] if flags else 0,
        "color": _unpack_color(colors.most_common(1)[0][0]) if colors else [0.0, 0.0, 0.0],
        "align": _infer_align(para_lines, x0, x1),
        "line_count": len(para_lines),
    }


def _fit_paragraph(page, rect, text, fontname, size, align):
    """Find a (rect, size) that fits `text` via insert_textbox — grow the box
    downward first, then shrink the font. Tested on a throwaway page so no
    partial text lands on the real page. Returns (rect, size, fitted)."""
    al = _ALIGN.get(align, 0)
    page_rect = page.rect
    scratch = pymupdf.open()
    sp = scratch.new_page(width=page_rect.width, height=page_rect.height)
    cur = pymupdf.Rect(rect)
    cur_size = float(size)
    bottom_limit = page_rect.y1 - 2
    fitted = False
    for _ in range(160):
        rc = sp.insert_textbox(cur, text, fontname=fontname, fontsize=cur_size, align=al)
        if rc >= 0:
            fitted = True
            break
        if cur.y1 < bottom_limit:
            cur = pymupdf.Rect(cur.x0, cur.y0, cur.x1,
                               min(cur.y1 + max(cur_size, 12.0), bottom_limit))
        elif cur_size > 6.0:
            cur_size -= 0.5
        else:
            break
    scratch.close()
    return cur, cur_size, fitted


@router.get("/text-blocks/{job_id}/{page_num}")
async def get_text_blocks(job_id: str, page_num: int, _=Depends(verify_secret)):
    """Return paragraph blocks for the paragraph-edit (¶) mode."""
    t0 = time.monotonic()
    doc = pymupdf.open(str(get_pdf_path(job_id)))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        return {"blocks": []}
    page = doc[page_num]
    raw = page.get_text(
        "dict",
        flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES,
    )
    doc.close()
    out = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for para_lines in _split_block_into_paragraphs(block.get("lines", [])):
            summ = _summarize_para(para_lines)
            if summ["text"]:
                out.append(summ)
    # PyMuPDF yields blocks in content-stream order, not visual order (and an
    # edited paragraph re-enters the stream last). Sort top→bottom, left→right
    # so block ids are stable and match what the user sees.
    out.sort(key=lambda s: (round(s["bbox"][1], 1), round(s["bbox"][0], 1)))
    for i, s in enumerate(out):
        s["id"] = i
    log.info("blocks job=%s page=%d → %d  %.0fms", job_id[:8], page_num, len(out),
             (time.monotonic() - t0) * 1000)
    return {"blocks": out}


@router.post("/edit-paragraph/{job_id}")
async def edit_paragraph(job_id: str, body: dict, _=Depends(verify_secret)):
    """Replace a paragraph block: clear the old region (bg-matched) and reflow
    the new text into it via insert_textbox (grow-then-shrink to fit)."""
    t0 = time.monotonic()
    page_num      = body.get("page")
    bbox          = body.get("bbox")
    new_text      = (body.get("new_text") or "").strip()
    font          = str(body.get("font", ""))
    size          = float(body.get("size", 12))
    flags         = int(body.get("flags", 0))
    color         = body.get("color", [0.0, 0.0, 0.0])
    align         = body.get("align", "left")
    resolved_font = body.get("resolved_font")

    if page_num is None or not bbox:
        raise HTTPException(400, "page and bbox required")

    doc = pymupdf.open(str(get_pdf_path(job_id)))
    if page_num < 0 or page_num >= len(doc):
        doc.close()
        raise HTTPException(400, "invalid page")
    page = doc[page_num]
    rect = pymupdf.Rect(bbox)
    fontname = resolved_font if resolved_font else _match_font(font, flags)
    col = tuple(float(v) for v in _unpack_color(color)[:3])

    # Fit-test BEFORE any destructive change, so an overflow is non-destructive.
    final_rect, final_size, fitted = rect, size, True
    if new_text:
        final_rect, final_size, fitted = _fit_paragraph(page, rect, new_text, fontname, size, align)
        if not fitted:
            doc.close()
            raise HTTPException(status_code=409, detail={
                "fit": False,
                "message": "Text does not fit even at minimum size — shorten it or split the paragraph.",
            })

    bg = _detect_bg(page, rect)
    page.add_redact_annot(rect + (-1, -1, 1, 1), fill=bg)
    page.apply_redactions()
    if new_text:
        page.insert_textbox(final_rect, new_text, fontname=fontname,
                            fontsize=final_size, color=col, align=_ALIGN.get(align, 0))

    save_pdf(doc, job_id)
    grew = final_rect.y1 > rect.y1 + 0.5
    log.info("para   job=%s page=%s grew=%s size=%.1f  %.0fms", job_id[:8], page_num,
             grew, final_size, (time.monotonic() - t0) * 1000)
    return {"ok": True, "grew": grew, "size": final_size}
