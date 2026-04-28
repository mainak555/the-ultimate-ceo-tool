---
name: chat-attachment-workflow
description: Implement and maintain chat attachment upload/bind/render/delete behavior across Home chat and Human-in-the-loop gate with Azure Blob storage and session-scoped isolation.
---

# Chat Attachment Workflow

## Purpose
Keep chat attachment behavior consistent across backend, frontend, and docs.

## Required Contracts
1. Storage key format is always `sessions/{session_id}/attachments/{attachment_id}/{filename}` — flat, permanent, no `message_id` segment. The key is assigned at upload time and is never renamed at bind.
2. Attachment metadata must include `session_id` and must be validated on every read/bind operation.
3. Session deletion must remove both blob bytes and metadata rows **plus** purge all Redis text-cache keys for the session (call `purge_session_attachment_cache(session_id)` before deleting blobs).
4. Azure auth uses `AZURE_STORAGE_CONTAINER_SAS_URL` (container SAS URL with query token), not account connection strings.
5. Main chat composer and HITL gate input must both support attach button, drag/drop, and paste.
6. HITL notes and user messages are rendered as markdown in both live and persisted history.
7. Image attachments must render thumbnails (the content URL) in history. Non-image attachments must render a per-extension SVG icon from `/static/server/assets/icons/file-{ext}.svg`, falling back to `file-document.svg` for unrecognised extensions. The `_enrich_attachments_for_display` function in `server/views.py` owns both mappings; adding a new allowed extension also requires adding its SVG icon.
8. Attachment text extraction is **lazy and Redis-cached** — nothing is extracted at upload time. Extraction happens on the first `build_attachment_context_block` call and the result is cached in Redis with a `REDIS_ATTACHMENT_TTL_SECONDS` TTL (default 24 h, minimum 1 h). Every subsequent resume is a Redis HIT (< 1 ms). No text content ever lands in MongoDB. Supported extractable types: PDF (up to 50 pages), DOCX, PPTX (up to 50 slides), XLSX/XLS (all sheets, tab-separated rows, blank rows skipped), CSV (up to 200 rows), TXT, MD, JSON.
9. MongoDB `chat_attachments` stores metadata only — fields `extracted_text` and `extraction_status` must never be written. The MongoDB document is: `{attachment_id, project_id, session_id, message_id, filename, extension, mime_type, size_bytes, is_image, blob_key, uploaded_at}`.
10. Redis text-cache keys follow the schema `{REDIS_NAMESPACE}:attachment:{session_id}:{attachment_id}:text` (string, full extracted text). A companion index SET at `{REDIS_NAMESPACE}:attachment:{session_id}:index` tracks which attachment IDs have cached text for that session, so session deletion can efficiently purge all keys. Both keys share the same TTL.
11. Image attachments are **never** Redis-cached. `load_images_for_agents(*, session_id, attachment_ids)` downloads raw bytes from blob at each run/resume and returns `list[tuple[str, bytes, str]]` (filename, raw_bytes, mime_type). The caller in `server/views.py` wraps them as `autogen_core.Image(PIL.Image.open(...))` inside a `MultiModalMessage(content=[task_text, img1, img2, ...], source="user")` and passes that to `team.run_stream` instead of a plain string.
12. `_build_agent_task_for_run(task_text, session_id, attachment_ids)` in `server/views.py` returns `str | MultiModalMessage`. If no image attachments exist it returns the plain task string unchanged. This function is called via `asyncio.to_thread` inside `event_stream()`.
13. The Redis client is shared from `agents.session_coordination.get_redis_client()`. `server/attachment_service.py` must never create its own Redis pool; it imports `get_redis_client` lazily inside `_get_redis()` to avoid circular imports. Redis failures in the attachment path must be silent (log a warning and fall through to blob download).

## Backend Pattern
1. Use Strategy + Factory for storage provider selection.
2. Keep provider-specific blob logic in `server/storage_backends.py`.
3. Keep upload/bind/context/deletion orchestration in `server/attachment_service.py`.
4. Keep HTTP-level parsing/response behavior in `server/views.py`.

## Frontend Pattern
1. `home.js` owns attachment state and upload lifecycle for compose + gate surfaces.
2. Render pre-send chips in `.chat-attachment-list`.
3. Render persisted attachments under message markdown with `.chat-message-attachments`.

## Observability
1. Use module logger (`logging.getLogger(__name__)`) in new Python modules.
2. Wrap public attachment service operations with `@traced_function`.
3. Never log credentials, blob connection strings, or secret keys.

## Checklist
1. Upload endpoint validates type/count/size and secret key. Allowed extensions: `png jpg jpeg webp gif bmp svg heic heif tif tiff pdf txt md csv json doc docx ppt pptx xls xlsx`. Adding a new extension requires: (a) adding it to `_ALLOWED_EXTENSIONS`, (b) adding an extraction branch in `_extract_text_for_extension`, (c) adding its SVG icon under `server/static/server/assets/icons/file-{ext}.svg`.
2. Run/respond endpoints accept repeated `attachment_ids`.
3. History partial and SSE-rendered bubbles display attachments.
4. Session delete path performs prefix cleanup, Redis cache purge, and metadata cleanup.
5. API/UI/DB docs and README are updated in the same change.
6. SAS token scope includes needed blob permissions (read/write/create/list/delete) for upload, content fetch, and session cleanup.
7. Compose Send button is disabled immediately on click and re-enabled only on upload/send failure; `chatInput` stays editable throughout.
8. HITL gate Continue button must set `panel.dataset.submitting="1"` and `continueBtn.disabled=true` before the async upload starts; `_evalGateContinue` must bail out if `panel.dataset.submitting==="1"` so an in-flight upload cannot re-enable the button.
9. HITL gate Attach button uses class `chat-attach-btn` (circular, 2.25rem) rather than `btn--secondary` with text label, matching the compose-area attach button style.
10. No `extracted_text` or `extraction_status` fields in MongoDB upload document.
11. `purge_session_attachment_cache(session_id)` is called before blob and MongoDB deletion in `delete_session_attachments`.
12. `_build_agent_task_for_run` in `views.py` falls back to returning the plain task string if PIL/autogen_core imports fail or all image downloads fail — agent run must never be blocked by image processing errors.
13. `REDIS_ATTACHMENT_TTL_SECONDS` appears in `config/settings.py` and in README env var table.
