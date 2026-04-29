# Chat Attachment Storage — Design Reference

This document explains why the attachment pipeline uses three separate storage layers,
what each layer owns, and how data flows through each operation. Read this before
modifying `server/attachment_service.py`, `server/storage_backends.py`, or any
code that touches `chat_attachments` in MongoDB.

---

## Three-Layer Storage Rationale

The pipeline deliberately spreads data across three stores. They are **not** redundant —
each serves a distinct access pattern.

| Layer | Store | What lives here | Lifetime |
|---|---|---|---|
| **Blob bytes** | Azure Blob `sessions/{session_id}/attachments/{attachment_id}/{filename}` | Raw file bytes for every attachment | Until session is deleted |
| **Metadata registry** | MongoDB `chat_attachments` | Filename, mime-type, size, `blob_key`, `is_image`, `session_id`, `project_id`, `message_id` | Until session is deleted |
| **Extracted text cache** | Redis `{REDIS_NAMESPACE}:attachment:{session_id}:{attachment_id}:text` | Plain text extracted from PDF/DOCX/XLSX/CSV etc. | TTL — default 24 h (`REDIS_ATTACHMENT_TTL_SECONDS`) |
| **Display snapshot** | MongoDB `chat_sessions.discussions[].attachments[]` | Render-only subset: `id`, `filename`, `mime_type`, `size_bytes`, `is_image`, `content_url` | Embedded — gone when session doc is deleted |

### Why not store extracted text in MongoDB?

1. **Document size cap.** MongoDB has a 16 MB per-document BSON limit. A session with
   10 large Excel workbooks could push a session document toward (or past) that limit.
2. **TTL.** Extracted text is an ephemeral runtime artefact — only useful while a run
   is active or may resume. Redis native TTL discards it automatically; MongoDB requires
   a separate cleanup job or TTL index.
3. **Query overhead.** Without TTL, old extracted text accumulates permanently and is
   returned on every `find_one` for the session, wasting memory and bandwidth.

### Why not cache images in Redis?

Image blobs are raw bytes (JPEG, PNG, HEIC …). They are too large for Redis and Redis is
not a blob store. Images are downloaded from Azure Blob on each agent run via
`load_images_for_agents()` and passed directly as `MultiModalMessage` pixel data.
No Redis key is written for images.

### Why does `discussions[].attachments` duplicate metadata?

When the chat history partial renders, it iterates `discussions[]`. Without the embedded
snapshot, every message with attachments would require a separate `chat_attachments`
lookup — one `$in` query per message per page load (30 messages = 30 round-trips).
Embedding a small display-only snapshot makes history rendering a single document read.
The embedded list deliberately omits `blob_key`, `project_id`, and `uploaded_at` —
it carries only what the template needs to draw the chip.

---

## Data Model

### MongoDB `chat_attachments` document

```json
{
  "attachment_id": "uuid",
  "project_id":   "ObjectId hex",
  "session_id":   "ObjectId hex",
  "message_id":   "uuid | null",
  "filename":     "safe_name.pdf",
  "extension":    "pdf",
  "mime_type":    "application/pdf",
  "size_bytes":   102400,
  "is_image":     false,
  "blob_key":     "sessions/{session_id}/attachments/{attachment_id}/safe_name.pdf",
  "uploaded_at":  "<BSON Date UTC>"
}
```

> `extracted_text` and `extraction_status` are **never** written here.
> MongoDB is metadata-only; all text content lives in Redis.

### Redis keys

```
{REDIS_NAMESPACE}:attachment:{session_id}:{attachment_id}:text   # STRING — full extracted text
{REDIS_NAMESPACE}:attachment:{session_id}:index                  # SET   — tracks attachment_ids with cached text
```

Both keys share the same TTL (`REDIS_ATTACHMENT_TTL_SECONDS`, default 86 400 s).
The index SET lets `purge_session_attachment_cache()` find all text keys for a session
in O(1) without a `SCAN`.

### Embedded display snapshot (`discussions[].attachments[]`)

```json
{
  "id":          "uuid",
  "filename":    "report.pdf",
  "mime_type":   "application/pdf",
  "size_bytes":  102400,
  "is_image":    false,
  "extension":   "pdf",
  "content_url": "/chat/sessions/<id>/attachments/<att_id>/content/"
}
```

---

## Sequence Diagrams

### 1 — Upload

```mermaid
sequenceDiagram
    participant Browser
    participant views.py
    participant attachment_service
    participant AzureBlob
    participant MongoDB

    Browser->>views.py: POST /chat/sessions/<id>/attachments/ (multipart)
    views.py->>attachment_service: upload_session_attachments(session, files)
    attachment_service->>attachment_service: _validate_files() — type/size/count
    loop each file
        attachment_service->>AzureBlob: upload_bytes(key, bytes, content_type)
        AzureBlob-->>attachment_service: OK
        attachment_service->>MongoDB: insert_one(chat_attachments) — metadata only
    end
    attachment_service-->>views.py: list[descriptor]
    views.py-->>Browser: 200 JSON — attachment_id, filename, … (no blob_key)
```

No text extraction happens at upload time. The browser receives only display metadata.

---

### 2 — Agent Run (text documents)

```mermaid
sequenceDiagram
    participant Browser
    participant views.py
    participant attachment_service
    participant Redis
    participant AzureBlob
    participant AgentTeam

    Browser->>views.py: POST /chat/sessions/<id>/run/ (text + attachment_ids[])
    views.py->>attachment_service: bind_attachments_to_message(session_id, msg_id, ids)
    attachment_service->>MongoDB: update_many — set message_id on attachment rows
    MongoDB-->>attachment_service: OK
    views.py->>attachment_service: build_attachment_context_block(session_id, ids)
    loop each non-image attachment
        attachment_service->>Redis: GET text cache key
        alt cache HIT
            Redis-->>attachment_service: extracted text (< 1 ms)
        else cache MISS
            attachment_service->>AzureBlob: download_bytes(blob_key)
            AzureBlob-->>attachment_service: raw bytes
            attachment_service->>attachment_service: _extract_text_for_extension(ext, bytes)
            alt extraction succeeded (no exception)
                attachment_service->>Redis: SET text + index with TTL
            else extraction raised exception (transient failure)
                attachment_service->>attachment_service: skip SET — next run retries
            end
        end
    end
    attachment_service-->>views.py: "--- Attachments: …" context block
    views.py->>AgentTeam: run_stream(task = user_text + context_block)
    AgentTeam-->>Browser: SSE stream
```

---

### 3 — Agent Run (vision images)

```mermaid
sequenceDiagram
    participant views.py
    participant attachment_service
    participant AzureBlob
    participant AgentTeam

    views.py->>attachment_service: load_images_for_agents(session_id, ids)
    loop each image attachment
        attachment_service->>AzureBlob: download_bytes(blob_key)
        AzureBlob-->>attachment_service: raw image bytes
        Note over attachment_service: PIL.Image.open → autogen_core.Image
    end
    attachment_service-->>views.py: list[(filename, bytes, mime_type)]
    views.py->>AgentTeam: run_stream(task = MultiModalMessage([text, img1, img2 …]))
```

Images are never Redis-cached. Each run/resume downloads fresh from blob.

---

### 4 — Session Delete

```mermaid
sequenceDiagram
    participant views.py
    participant services.py
    participant attachment_service
    participant Redis
    participant AzureBlob
    participant MongoDB

    views.py->>services.py: delete_chat_session(session_id)
    services.py->>attachment_service: delete_session_attachments(session_id)

    attachment_service->>Redis: SMEMBERS index key → attachment_id list
    attachment_service->>Redis: DEL text keys + index key
    Redis-->>attachment_service: OK

    attachment_service->>AzureBlob: delete_prefix("sessions/{session_id}/")
    Note over AzureBlob: Always runs even if no metadata rows exist.<br/>Prevents orphaned blobs from interrupted uploads.
    AzureBlob-->>attachment_service: deleted_blob_count

    attachment_service->>MongoDB: delete_many(chat_attachments, {session_id})
    MongoDB-->>attachment_service: deleted_metadata_count

    attachment_service-->>services.py: logged summary
    services.py->>MongoDB: delete_one(chat_sessions, {_id})
    MongoDB-->>services.py: OK
    services.py-->>views.py: done
```

Delete order is always **Redis → Blob → MongoDB**. Blob deletion always runs for a valid
`session_id` regardless of whether metadata rows exist.

---

## Storage Layer Decision Guide

| Question | Answer |
|---|---|
| Serve raw file bytes to the browser? | Look up `blob_key` in `chat_attachments`, then `download_bytes()`. |
| Pass document text to an agent? | `build_attachment_context_block()` — Redis-cached, lazy. |
| Pass images to a vision model? | `load_images_for_agents()` — blob download, no cache. |
| Render attachment chips in history? | Read `discussions[].attachments[]` — already embedded, no extra query. |
| Find all attachments for a session? | Query `chat_attachments` by `session_id`. |
| Add a new extractable file type? | Add to `_ALLOWED_EXTENSIONS`, add branch in `_extract_text_for_extension`, add SVG icon under `server/static/server/assets/icons/file-{ext}.svg`. |
| Swap Azure for S3? | Implement `StorageStrategy`, register in `build_storage_strategy()`. No other changes needed. |

---

## Key Invariants

1. `blob_key` is **never** exposed to the browser or embedded in `discussions[]`.
2. `extracted_text` / `extraction_status` are **never** written to MongoDB.
3. Blob prefix delete **always runs** for a valid `session_id` — a storage provider
   error logs a warning but does not abort metadata cleanup.
4. Delete order is always: **Redis → Blob → MongoDB**.
5. `message_id` in `chat_attachments` is `null` until `bind_attachments_to_message()`
   is called; unbound rows are still cleaned up on session delete.
6. **Extraction failures are never cached.** If `build_attachment_context_block` raises
   an exception during blob download or text extraction, the result is not written to
   Redis so the next run retries. Genuinely empty documents (e.g. scanned PDFs with no
   text layer) are cached normally to avoid repeated blob downloads.
7. **`discussions[].content` for user messages stores raw task text only** — never the
   `text_with_context` string that includes the extracted attachment block. Extracted
   attachment text is rebuilt at runtime from Blob → Redis; persisting it in MongoDB
   would duplicate ephemeral data, inflate document size, and corrupt the export
   reference text used by Trello/Jira modals.
