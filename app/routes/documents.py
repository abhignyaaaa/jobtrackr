from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Application, Document
from ..services.storage import UploadError, get_storage, new_key, validate_upload
from ..web import check_csrf, redirect, render, require_user

router = APIRouter()


def _storage_or_404():
    storage = get_storage()
    if storage is None:
        raise HTTPException(status_code=404, detail="Document storage is not enabled")
    return storage


def _owned_doc(db: Session, user_id: int, doc_id: int) -> Document:
    doc = db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/documents")
def list_documents(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    _storage_or_404()
    docs = db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.uploaded_at.desc())).all()
    apps = db.execute(select(Application.id, Application.company, Application.title)
                      .where(Application.user_id == user.id).order_by(Application.company)).all()
    return render(request, "documents/list.html", docs=docs, apps=apps,
                  max_mb=settings.max_upload_mb, max_docs=settings.max_docs_per_user)


@router.post("/documents")
async def upload_document(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    storage = _storage_or_404()
    # Reject oversized bodies before parsing the multipart form.
    limit = settings.max_upload_mb * 1024 * 1024 + 64 * 1024
    try:
        if int(request.headers.get("content-length", "0")) > limit:
            return redirect("/documents", f"File is larger than {settings.max_upload_mb} MB.", "error")
    except ValueError:
        pass
    await check_csrf(request)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return redirect("/documents", "Choose a file to upload.", "error")
    count = db.scalar(select(func.count()).select_from(Document).where(Document.user_id == user.id)) or 0
    if count >= settings.max_docs_per_user:
        return redirect("/documents", f"You can store up to {settings.max_docs_per_user} documents.", "error")
    data = await upload.read(settings.max_upload_mb * 1024 * 1024 + 1)
    try:
        vf = validate_upload(upload.filename or "", data)
    except UploadError as exc:
        return redirect("/documents", str(exc), "error")

    app_id = None
    raw_app = str(form.get("application_id") or "")
    if raw_app.isdigit():
        app_id = db.scalar(select(Application.id).where(Application.id == int(raw_app),
                                                        Application.user_id == user.id))
    label = str(form.get("label") or "").strip()[:120] or vf.original_name
    key = new_key(user.id, vf.ext)
    storage.put(key, vf.data, vf.content_type)
    db.add(Document(user_id=user.id, application_id=app_id, label=label, original_name=vf.original_name,
                    storage_key=key, content_type=vf.content_type, size_bytes=len(vf.data)))
    db.commit()
    return redirect("/documents", "Document uploaded.")


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    storage = _storage_or_404()
    doc = _owned_doc(db, user.id, doc_id)
    url = storage.presigned_url(doc.storage_key, doc.original_name, doc.content_type)
    if url:
        return RedirectResponse(url, status_code=303)
    return Response(storage.get(doc.storage_key), media_type=doc.content_type, headers={
        "Content-Disposition": f'attachment; filename="{doc.original_name}"',
        "Cache-Control": "private, no-store",
    })


@router.post("/documents/{doc_id}/delete")
async def delete_document(doc_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    storage = _storage_or_404()
    await check_csrf(request)
    doc = _owned_doc(db, user.id, doc_id)
    storage.delete(doc.storage_key)
    db.delete(doc)
    db.commit()
    return redirect("/documents", "Document deleted.")
