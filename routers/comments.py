import json
import uuid
import time
from fastapi import APIRouter, Depends, HTTPException
from deps import verify_secret, STORE

router = APIRouter()


def _path(job_id: str):
    return STORE / job_id / "comments.json"


def _load(job_id: str) -> list:
    p = _path(job_id)
    return json.loads(p.read_text()) if p.exists() else []


def _save(job_id: str, comments: list):
    _path(job_id).write_text(json.dumps(comments))


@router.get("/comments/{job_id}")
async def list_comments(job_id: str, _=Depends(verify_secret)):
    return {"comments": _load(job_id)}


@router.post("/comments/{job_id}")
async def add_comment(job_id: str, body: dict, _=Depends(verify_secret)):
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    comment = {
        "id":        str(uuid.uuid4()),
        "page":      int(body.get("page", 0)),
        "rect":      body.get("rect"),
        "text":      text,
        "author":    str(body.get("author", "Operator")),
        "timestamp": time.time(),
        "resolved":  False,
        "replies":   [],
    }
    comments = _load(job_id)
    comments.append(comment)
    _save(job_id, comments)
    return {"comment": comment}


@router.post("/comments/{job_id}/{comment_id}/reply")
async def add_reply(job_id: str, comment_id: str, body: dict, _=Depends(verify_secret)):
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    comments = _load(job_id)
    for c in comments:
        if c["id"] == comment_id:
            reply = {
                "id":        str(uuid.uuid4()),
                "text":      text,
                "author":    str(body.get("author", "Operator")),
                "timestamp": time.time(),
            }
            c["replies"].append(reply)
            _save(job_id, comments)
            return {"reply": reply}
    raise HTTPException(404, "Comment not found")


@router.patch("/comments/{job_id}/{comment_id}")
async def update_comment(job_id: str, comment_id: str, body: dict, _=Depends(verify_secret)):
    comments = _load(job_id)
    for c in comments:
        if c["id"] == comment_id:
            if "resolved" in body:
                c["resolved"] = bool(body["resolved"])
            if "text" in body:
                c["text"] = str(body["text"]).strip()
            _save(job_id, comments)
            return {"comment": c}
    raise HTTPException(404, "Comment not found")


@router.delete("/comments/{job_id}/{comment_id}")
async def delete_comment(job_id: str, comment_id: str, _=Depends(verify_secret)):
    comments = _load(job_id)
    new = [c for c in comments if c["id"] != comment_id]
    if len(new) == len(comments):
        raise HTTPException(404, "Comment not found")
    _save(job_id, new)
    return {"ok": True}
