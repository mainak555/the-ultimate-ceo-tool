"""Attachment upload, extraction, binding, and retrieval helpers."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from django.core.files.uploadedfile import UploadedFile

from core.tracing import traced_function

from .db import get_collection
from .storage_backends import build_storage_strategy

logger = logging.getLogger(__name__)

ATTACHMENTS_COLLECTION = "chat_attachments"
_MAX_ATTACHMENTS_PER_MESSAGE = 10
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MAX_EXTRACTED_TEXT_CHARS = 6000

_ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "heic", "heif", "tif", "tiff",
    "pdf", "txt", "md", "csv", "json", "doc", "docx", "ppt", "pptx",
}
_IMAGE_EXTENSIONS = {
    "png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "heic", "heif", "tif", "tiff",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_filename(name: str) -> str:
    candidate = (name or "file").strip() or "file"
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate)
    return candidate[:180]


def _file_ext(name: str) -> str:
    parts = (name or "").lower().rsplit(".", 1)
    return parts[1] if len(parts) == 2 else ""


def _extract_text_for_extension(ext: str, raw: bytes) -> str:
    ext = (ext or "").lower()
    try:
        if ext in {"txt", "md"}:
            return raw.decode("utf-8", errors="replace")
        if ext == "json":
            obj = json.loads(raw.decode("utf-8", errors="replace"))
            return json.dumps(obj, indent=2, ensure_ascii=False)
        if ext == "csv":
            rows = []
            reader = csv.reader(io.StringIO(raw.decode("utf-8", errors="replace")))
            for idx, row in enumerate(reader):
                rows.append(", ".join(row))
                if idx >= 50:
                    break
            return "\n".join(rows)
        if ext == "pdf":
            try:
                from pypdf import PdfReader
            except Exception:
                return ""
            reader = PdfReader(io.BytesIO(raw))
            out = []
            for page in reader.pages[:20]:
                out.append(page.extract_text() or "")
            return "\n".join(out)
        if ext == "docx":
            try:
                from docx import Document
            except Exception:
                return ""
            doc = Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs)
        if ext == "pptx":
            try:
                from pptx import Presentation
            except Exception:
                return ""
            prs = Presentation(io.BytesIO(raw))
            out = []
            for slide in prs.slides[:30]:
                for shape in slide.shapes:
                    text = getattr(shape, "text", "")
                    if text:
                        out.append(text)
            return "\n".join(out)
    except Exception:
        logger.exception("attachments.extract_failed", extra={"extension": ext})
        return ""
    return ""


def _validate_files(files: list[UploadedFile]) -> None:
    if not files:
        raise ValueError("At least one file is required.")
    if len(files) > _MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError(f"A maximum of {_MAX_ATTACHMENTS_PER_MESSAGE} files can be uploaded at once.")

    for item in files:
        ext = _file_ext(item.name)
        if ext not in _ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported file type '{ext or 'unknown'}' for '{item.name}'.")
        if (item.size or 0) > _MAX_ATTACHMENT_BYTES:
            raise ValueError(f"File '{item.name}' exceeds 20 MB limit.")


def _build_blob_key(*, session_id: str, message_id: str, attachment_id: str, filename: str) -> str:
    return f"sessions/{session_id}/messages/{message_id}/attachments/{attachment_id}/{filename}"


def _attachment_descriptor(doc: dict) -> dict:
    return {
        "id": doc.get("attachment_id", ""),
        "filename": doc.get("filename", ""),
        "mime_type": doc.get("mime_type", "application/octet-stream"),
        "size_bytes": int(doc.get("size_bytes") or 0),
        "is_image": bool(doc.get("is_image", False)),
        "extension": doc.get("extension", ""),
    }


@traced_function("service.attachments.upload")
def upload_session_attachments(*, session: dict, files: list[UploadedFile]) -> list[dict]:
    _validate_files(files)

    strategy = build_storage_strategy()
    col = get_collection(ATTACHMENTS_COLLECTION)
    session_id = session.get("session_id", "")
    project_id = session.get("project_id", "")
    placeholders = []

    for uploaded in files:
        filename = _clean_filename(uploaded.name)
        ext = _file_ext(filename)
        attachment_id = str(uuid4())
        message_id = str(uuid4())
        key = _build_blob_key(
            session_id=session_id,
            message_id=message_id,
            attachment_id=attachment_id,
            filename=filename,
        )

        raw = uploaded.read()
        strategy.upload_bytes(
            key=key,
            data=raw,
            content_type=(uploaded.content_type or "application/octet-stream"),
        )

        extracted = _extract_text_for_extension(ext, raw)
        if len(extracted) > _MAX_EXTRACTED_TEXT_CHARS:
            extracted = extracted[:_MAX_EXTRACTED_TEXT_CHARS]

        doc = {
            "attachment_id": attachment_id,
            "project_id": project_id,
            "session_id": session_id,
            "message_id": None,
            "staging_message_id": message_id,
            "filename": filename,
            "extension": ext,
            "mime_type": uploaded.content_type or "application/octet-stream",
            "size_bytes": int(uploaded.size or len(raw)),
            "is_image": ext in _IMAGE_EXTENSIONS,
            "blob_key": key,
            "uploaded_at": _utc_now(),
            "extracted_text": extracted,
            "extraction_status": "available" if extracted else "none",
        }
        col.insert_one(doc)
        placeholders.append(_attachment_descriptor(doc))

    logger.info(
        "attachments.uploaded",
        extra={"session_id": session_id, "count": len(placeholders)},
    )
    return placeholders


def _get_attachment_docs_for_session(session_id: str, attachment_ids: Iterable[str]) -> list[dict]:
    wanted = [str(x).strip() for x in (attachment_ids or []) if str(x).strip()]
    if not wanted:
        return []
    col = get_collection(ATTACHMENTS_COLLECTION)
    cursor = col.find({"session_id": session_id, "attachment_id": {"$in": wanted}})
    docs = list(cursor)
    index = {d.get("attachment_id"): d for d in docs}
    return [index[aid] for aid in wanted if aid in index]


@traced_function("service.attachments.bind_to_message")
def bind_attachments_to_message(*, session_id: str, message_id: str, attachment_ids: Iterable[str]) -> list[dict]:
    docs = _get_attachment_docs_for_session(session_id, attachment_ids)
    if not docs:
        return []

    col = get_collection(ATTACHMENTS_COLLECTION)
    attachment_ids_clean = [d["attachment_id"] for d in docs]
    col.update_many(
        {"session_id": session_id, "attachment_id": {"$in": attachment_ids_clean}},
        {"$set": {"message_id": message_id, "bound_at": _utc_now()}},
    )

    return [
        {
            "id": d.get("attachment_id", ""),
            "filename": d.get("filename", ""),
            "mime_type": d.get("mime_type", "application/octet-stream"),
            "size_bytes": int(d.get("size_bytes") or 0),
            "is_image": bool(d.get("is_image", False)),
            "extension": d.get("extension", ""),
            "content_url": f"/chat/sessions/{session_id}/attachments/{d.get('attachment_id', '')}/content/",
        }
        for d in docs
    ]


def build_attachment_context_block(*, session_id: str, attachment_ids: Iterable[str]) -> str:
    docs = _get_attachment_docs_for_session(session_id, attachment_ids)
    if not docs:
        return ""

    lines = ["", "---", "Attachments Context:"]
    for d in docs:
        lines.append(
            f"- {d.get('filename', 'file')} ({d.get('mime_type', 'application/octet-stream')}, {int(d.get('size_bytes') or 0)} bytes)"
        )
        extracted = (d.get("extracted_text") or "").strip()
        if extracted:
            lines.append("  Extracted text preview:")
            for row in extracted[:1200].splitlines()[:12]:
                lines.append(f"  {row}")
    return "\n".join(lines)


def get_attachment_content(*, session_id: str, attachment_id: str) -> tuple[bytes, str, str]:
    col = get_collection(ATTACHMENTS_COLLECTION)
    doc = col.find_one({"session_id": session_id, "attachment_id": attachment_id})
    if not doc:
        raise ValueError("Attachment not found.")

    strategy = build_storage_strategy()
    raw = strategy.download_bytes(key=doc.get("blob_key", ""))
    return raw, doc.get("mime_type", "application/octet-stream"), doc.get("filename", "attachment")


@traced_function("service.attachments.delete_session")
def delete_session_attachments(session_id: str) -> None:
    if not session_id:
        return
    try:
        ObjectId(session_id)
    except (InvalidId, TypeError):
        return

    col = get_collection(ATTACHMENTS_COLLECTION)
    if col.count_documents({"session_id": session_id}, limit=1) == 0:
        return

    strategy = build_storage_strategy()
    prefix = f"sessions/{session_id}/"
    deleted_blobs = strategy.delete_prefix(prefix=prefix)

    result = col.delete_many({"session_id": session_id})

    logger.info(
        "attachments.session_deleted",
        extra={
            "session_id": session_id,
            "deleted_blob_count": int(deleted_blobs),
            "deleted_metadata_count": int(result.deleted_count),
        },
    )
