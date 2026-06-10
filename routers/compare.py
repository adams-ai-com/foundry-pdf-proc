import difflib
import pymupdf
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from deps import verify_secret, get_pdf_path, read_limited, MAX_PDF_BYTES

router = APIRouter()


@router.post("/compare/{job_id}")
async def compare_pdfs(
    job_id: str,
    file: UploadFile = File(...),
    _=Depends(verify_secret),
):
    """
    Compare the stored PDF against an uploaded PDF.
    Returns per-page diff regions: {page, rect, kind: 'added'|'removed', text}.
    Uses word-level positions from PyMuPDF + difflib SequenceMatcher.
    """
    pdf_path = get_pdf_path(job_id)
    other_bytes = await read_limited(file, MAX_PDF_BYTES)

    doc_a = pymupdf.open(str(pdf_path))
    doc_b = pymupdf.open(stream=other_bytes, filetype="pdf")

    results = []
    max_pages = max(len(doc_a), len(doc_b))

    for i in range(max_pages):
        words_a = []
        words_b = []

        if i < len(doc_a):
            raw = doc_a[i].get_text("words")  # (x0,y0,x1,y1,word,block,line,word_idx)
            words_a = [(w[4], (w[0], w[1], w[2], w[3])) for w in raw]

        if i < len(doc_b):
            raw = doc_b[i].get_text("words")
            words_b = [(w[4], (w[0], w[1], w[2], w[3])) for w in raw]

        texts_a = [w[0] for w in words_a]
        texts_b = [w[0] for w in words_b]

        matcher = difflib.SequenceMatcher(None, texts_a, texts_b, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            if tag in ('replace', 'delete'):
                for idx in range(i1, i2):
                    if idx < len(words_a):
                        results.append({
                            "page": i,
                            "rect": list(words_a[idx][1]),
                            "kind": "removed",
                            "text": words_a[idx][0],
                        })
            if tag in ('replace', 'insert'):
                for idx in range(j1, j2):
                    if idx < len(words_b):
                        results.append({
                            "page": i,
                            "rect": list(words_b[idx][1]),
                            "kind": "added",
                            "text": words_b[idx][0],
                        })

    doc_a.close()
    doc_b.close()

    total_changes = len(results)
    added   = sum(1 for r in results if r["kind"] == "added")
    removed = sum(1 for r in results if r["kind"] == "removed")

    return {
        "changes": results,
        "summary": {"total": total_changes, "added": added, "removed": removed},
    }
