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
3. Session deletion must remove both blob bytes and metadata rows.
4. Azure auth uses `AZURE_STORAGE_CONTAINER_SAS_URL` (container SAS URL with query token), not account connection strings.
5. Main chat composer and HITL gate input must both support attach button, drag/drop, and paste.
6. HITL notes and user messages are rendered as markdown in both live and persisted history.
7. Image attachments must render thumbnails (the content URL) in history. Non-image attachments must render a per-extension SVG icon from `/static/server/assets/icons/file-{ext}.svg`, falling back to `file-document.svg` for unrecognised extensions. The `_enrich_attachments_for_display` function in `server/views.py` owns both mappings; adding a new allowed extension also requires adding its SVG icon.

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
1. Upload endpoint validates type/count/size and secret key.
2. Run/respond endpoints accept repeated `attachment_ids`.
3. History partial and SSE-rendered bubbles display attachments.
4. Session delete path performs prefix cleanup and metadata cleanup.
5. API/UI/DB docs and README are updated in the same change.
6. SAS token scope includes needed blob permissions (read/write/create/list/delete) for upload, content fetch, and session cleanup.
7. Compose Send button is disabled immediately on click and re-enabled only on upload/send failure; `chatInput` stays editable throughout.
8. HITL gate Continue button must set `panel.dataset.submitting="1"` and `continueBtn.disabled=true` before the async upload starts; `_evalGateContinue` must bail out if `panel.dataset.submitting==="1"` so an in-flight upload cannot re-enable the button.
9. HITL gate Attach button uses class `chat-attach-btn` (circular, 2.25rem) rather than `btn--secondary` with text label, matching the compose-area attach button style.
