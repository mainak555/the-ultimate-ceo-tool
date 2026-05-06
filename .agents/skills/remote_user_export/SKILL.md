# Skill: remote_user_export

## Purpose

Implementation reference for the remote-user export feature. Required reading before:

- Adding, changing, or reviewing the "Can Export" checkbox on the host readiness card
- Changing export key generation, auth wiring, or Redis key lifecycle
- Changing WebSocket event handling for `remote_export_enabled` / `remote_export_disabled`
- Changing how session-scoped Trello / Jira endpoint auth works
- Changing `ignore_remote_user` or `purge_remote_user_session_keys` purge behavior

---

## Feature Overview

The remote user export feature lets a host grant individual remote users the ability to
open the standard Trello/Jira export modal from agent message bubbles on their public
chat page. The admin `APP_SECRET_KEY` is **never** exposed to remote users.

Key properties:

- One Redis export key pair per `(session_id, user_name)`.
- Key TTL = `REDIS_REMOTE_USER_TOKEN_TTL_SECONDS` (default 6 h, same as invite tokens).
- All session-scoped Trello + Jira endpoints accept either `APP_SECRET_KEY` **or** the export key.
- WebSocket events propagate enable/disable state in real time — no page reload needed on the remote page.
- Ignoring a user automatically revokes their export key.
- Session delete purges all export key entries alongside other session keys.

---

## Redis Key Schema

All keys use namespace `{NS}` = `f"{REDIS_NAMESPACE}:{env}"`.

| Key | Value | TTL |
|-----|-------|-----|
| `{NS}:remote_user:{session_id}:{user_name}:export_key` | UUID export key string (forward lookup) | `REDIS_REMOTE_USER_TOKEN_TTL_SECONDS` |
| `{NS}:remote_export:key:{export_key}` | JSON `{"session_id": str, "user_name": str}` (reverse lookup) | `REDIS_REMOTE_USER_TOKEN_TTL_SECONDS` |

Both keys are written atomically (pipeline) by `generate_remote_user_export_key` and deleted
atomically by `revoke_remote_user_export_key`. `purge_remote_user_session_keys` deletes both
entries per user (fetching the forward key first to get the UUID, then deleting the reverse key).

---

## agents/session_coordination.py — Export Key Helpers

All export key helpers live in `agents/session_coordination.py` and are exported via `__all__`.
Server-layer code must import from there — never create a second Redis client.

| Function | Signature | Description |
|----------|-----------|-------------|
| `generate_remote_user_export_key` | `(session_id, user_name) → str` | Generate and store a UUID export key for the user. Idempotent — overwrites any previous key. Returns the key string. |
| `get_remote_user_export_key` | `(session_id, user_name) → str \| None` | Return the current export key for a user, or `None` if none exists or it has expired. |
| `get_remote_export_key_data` | `(export_key) → dict \| None` | Reverse-lookup: given a key string, return `{"session_id": str, "user_name": str}` or `None`. Used by `verify_session_export_key` in `services.py`. |
| `revoke_remote_user_export_key` | `(session_id, user_name)` | Delete both forward and reverse keys. Safe to call even when no key exists. |
| `get_all_remote_user_export_states` | `(session_id, user_names) → dict[str, bool]` | Pipelined check — returns `{user_name: bool}` indicating whether each user currently has an active export key. |

---

## server/services.py — Auth Helpers

```python
def verify_session_export_key(key: str, session_id: str) -> bool:
    """Return True if key is a valid impersonated export key for session_id."""

def has_valid_session_auth(request, session_id: str) -> bool:
    """Return True if X-App-Secret-Key matches admin APP_SECRET_KEY OR a valid session export key."""
```

### Usage rule

`has_valid_session_auth` is the **only** auth check allowed for session-scoped Trello and Jira
endpoints. Never use `verify_secret_key` alone on these endpoints — it would block remote users
who hold a valid export key.

Admin-only endpoints (project config, project-scoped Jira/Trello, `allow_remote_user_export` itself)
continue to use `_has_valid_secret` / `verify_secret_key`.

---

## server/util.py — Export Provider Registry

```python
SUPPORTED_EXPORT_PROVIDERS = ("trello", "jira", "pdf", "n8n")

EXPORT_PROVIDER_LABELS: dict[str, str]  # display labels, including per Jira sub-type

def normalize_export_agents(raw_agents) -> list[str]: ...

def build_export_meta(project: dict) -> dict | None:
    """Build {"enabled": True, "providers": [...]} from project integrations, or None."""

def filter_export_providers(export_meta: dict | None, agent_name: str) -> list[dict]:
    """Return providers visible for a given agent (respects per-provider export_agents allowlist)."""
```

`build_export_meta` and `filter_export_providers` are the single source of truth for which
providers are available per-project and per-agent. Views (host SSE and `remote_user_join`) must
use these helpers — never hardcode provider logic in views.

---

## SSE Export Metadata Shape

Each assistant message SSE record includes an `export` field when integrations are active:

```json
{
  "id": "<discussion_id>",
  "role": "assistant",
  "source": "<agent_name>",
  "content": "...",
  "export": {
    "enabled": true,
    "providers": [
      {"name": "trello",         "label": "Trello",         "export_agents": []},
      {"name": "jira_software",  "label": "Jira Software",  "export_agents": ["product_owner"]}
    ]
  }
}
```

`export` is `null` / absent when integrations are disabled or no providers are configured.
`export_agents` allowlist: empty list = all agents may export for this provider; non-empty = only
listed agent names show export buttons.

---

## WebSocket Event Shapes

### `remote_export_enabled`

Published by `allow_remote_user_export` via `publish_remote_user_event(session_id, payload)`.

```json
{"type": "remote_export_enabled", "user_name": "product_owner", "export_key": "<uuid>"}
```

Received by:
- `RemoteUserReadinessConsumer` → forwarded verbatim to the host WebSocket group.
- `RemoteChatConsumer` → forwarded to the remote WebSocket **only** when `payload["user_name"] == user_name` (own-user filter applied; other users' events are dropped silently).

### `remote_export_disabled`

```json
{"type": "remote_export_disabled", "user_name": "product_owner"}
```

Same routing as `remote_export_enabled`.

---

## Host UI — home.js

### Readiness card grid

`.remote-user-row` uses a 5-column grid: `auto 1fr auto auto auto`
(status dot | name | ignore | invite | export).

### Can Export checkbox

Each row renders:

```html
<label class="remote-user-row__export-label">
  <input type="checkbox" class="remote-user-row__export-cb"
         data-user-name="<name>" [checked] [disabled]>
  Can Export
</label>
```

- `checked` when `panel._users[name].export_allowed === true` (set from the WS `state` message,
  which merges `get_all_remote_user_export_states` into each user dict).
- `disabled` when the user is offline or ignored.

### Checkbox change handler (delegated on `.chat-remote-panel`)

```
POST /chat/sessions/{sid}/remote-users/{name}/allow-export/
Body:   {"enable": true|false}
Header: X-App-Secret-Key: <adminKey>
```

### WS message handlers

```js
// remote_export_enabled
panel._users[name].export_allowed = true;
_updateRemoteUserExportRow(panel, name, true);

// remote_export_disabled
panel._users[name].export_allowed = false;
_updateRemoteUserExportRow(panel, name, false);
```

`_updateRemoteUserExportRow(panel, userName, enabled)` locates the row's `.remote-user-row__export-cb`
and sets its `.checked` state.

---

## Remote User UI — remote_user.html + remote_user.js

### Window globals (set at page load via template script block)

```js
window._remoteExportKey       = "{{ export_key|default:'' }}";
window._remoteProjectId       = "{{ project_id|default:'' }}";
window._csrfToken             = "{{ csrf_token }}";
window._remoteExportProviders = {{ flat_export_providers_json|default:'[]'|safe }};
```

`_remoteExportProviders` is the unfiltered list of all providers for the project (no agent-name
filtering). Used as fallback when a bubble's `data-export-providers` attribute is absent.

### AI bubble attributes

Server-rendered AI bubbles carry:

```html
<div class="chat-bubble chat-bubble--ai"
     data-raw-content="..."
     data-discussion-id="{{ d.id|default:'' }}"
     data-export-providers="{{ d.export_providers_json|default:'[]' }}">
```

`export_providers_json` is computed per-discussion in `remote_user_join` (always computed when
`export_meta` exists, regardless of whether an export key is present). `visible_export_providers`
is the subset rendered immediately on page load if an export key exists at that point.

### Key functions in remote_user.js

| Function | Description |
|----------|-------------|
| `getExportKey()` | Returns `window._remoteExportKey \|\| ""` |
| `_buildExportDropdownHtml(providers, discussionId)` | Returns dropdown HTML if key is present and providers are non-empty; else `""` |
| `openRemoteExportModal(provider, sessionId, discussionId)` | Calls `window.ProviderRegistry.openExportModal(...)` with the impersonated key as `secretKey` |
| `_injectExportDropdowns()` | Iterates all `.chat-bubble--ai` elements; reads `data-export-providers`, falls back to `window._remoteExportProviders`; builds and inserts dropdown HTML per bubble |
| `_removeExportDropdowns()` | Removes all `.chat-bubble--ai .chat-bubble__actions` elements |

### Live bubble handling (WS `message` events)

`buildAssistantBubble(msg)` reads providers from the SSE payload:

```js
var providers = (msg.export && msg.export.providers) || msg.providers || null;
var discussionId = msg.id || msg.discussion_id || "";
```

Always sets `el.dataset.exportProviders` and `el.dataset.discussionId` on the bubble element,
regardless of whether an export key is currently active. This ensures `_injectExportDropdowns()`
can read correct per-agent-filtered providers when the key arrives later.

### WS handlers for export events

```js
// remote_export_enabled
window._remoteExportKey = msg.export_key;
_injectExportDropdowns();   // adds dropdowns to all existing bubbles immediately

// remote_export_disabled
window._remoteExportKey = "";
_removeExportDropdowns();   // removes all dropdowns immediately
```

---

## Export Modal Context for Remote Users

```js
window.ProviderRegistry.openExportModal({
  provider:     "<provider_name>",  // e.g. "trello", "jira_software"
  sessionId:    window._sessionId,
  discussionId: discussionId,
  secretKey:    getExportKey(),     // impersonated export key — NOT the admin key
  csrfToken:    window._csrfToken,
  projectId:    window._remoteProjectId,
});
```

The `secretKey` field carries the impersonated export key. All session-scoped Trello/Jira
endpoints accept it because they check `has_valid_session_auth(request, session_id)`.

---

## server/consumers.py — WebSocket Integration

### RemoteUserReadinessConsumer (host side)

- `_handle()`: calls `get_all_remote_user_export_states(session_id, all_names)` in the same
  pipeline as the status lookup; merges `"export_allowed": bool` into each user dict before
  emitting the `state` WebSocket message.
- `_listen_redis()`: handles `remote_export_enabled` / `remote_export_disabled` events
  by forwarding them verbatim to the host WebSocket group.

### RemoteChatConsumer (remote user side)

- `_listen_redis()`: subscribes to the session readiness channel; handles `remote_export_enabled`
  and `remote_export_disabled` by forwarding to the remote WebSocket **only** when
  `payload["user_name"] == user_name` (own-user filter). Events for other users are dropped.

---

## server/remote_user_views.py — Key Logic

### `allow_remote_user_export(request, session_id, user_name)`

Admin-only (`_has_valid_secret` check). Reads `{"enable": true|false}` from request body.

- `enable=True`:
  1. Calls `generate_remote_user_export_key(session_id, user_name)` → key string.
  2. Publishes `{"type": "remote_export_enabled", "user_name": ..., "export_key": ...}`
     to the session's readiness Redis channel.
- `enable=False`:
  1. Calls `revoke_remote_user_export_key(session_id, user_name)`.
  2. Publishes `{"type": "remote_export_disabled", "user_name": ...}`.

### `ignore_remote_user`

Also calls `revoke_remote_user_export_key(session_id, user_name)` before publishing the ignore
status event, ensuring export keys never outlive a user's active participation.

### `remote_user_join` — enrichment loop

For each assistant discussion entry:

- `row["export_providers_json"]` — per-agent-filtered providers JSON string (always computed
  when `export_meta` exists, **regardless** of whether an export key is present). Stored on the
  bubble's `data-export-providers` attribute for use by `_injectExportDropdowns()`.
- `row["visible_export_providers"]` — subset rendered immediately on page load (only when
  `export_key` is present at render time; otherwise `[]`).
- `flat_export_providers_json` — unfiltered list of all providers for the project (no agent
  name filtering). Passed as `window._remoteExportProviders` for the `_injectExportDropdowns`
  fallback path.

---

## Purge Lifecycle

`purge_remote_user_session_keys(session_id, user_names)` has been extended to delete both export
key entries per user:

1. Fetch the forward key → get the UUID export key string.
2. Delete the forward key (`{NS}:remote_user:{session_id}:{user_name}:export_key`).
3. Delete the reverse key (`{NS}:remote_export:key:{export_key}`) using the fetched UUID.

This runs inside the same pipeline as the other session key cleanup so it is atomic per user.

---

## SCSS — Export Checkbox Styles (main.scss)

Within the `.remote-user-row` BEM block, using tokens only:

```scss
// Grid updated to 5 columns:
// auto 1fr auto auto auto — status | name | ignore | invite | export

&__export-label {
  display: inline-flex;
  align-items: center;
  gap: $space-xs;
  font-size: $font-size-sm;
  color: $color-text-muted;
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
}

&__export-cb {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  cursor: pointer;
}
```

No hardcoded color values. No `margin-left` on the label — the grid provides alignment.

---

## Checklist — Before Changing Remote User Export

- [ ] Read this skill in full
- [ ] For new UI patterns on the remote/host chat surface, also read `.agents/skills/chat_surface_shared/SKILL.md`
- [ ] For export modal context shape and adapter rules, also read `.agents/skills/export_provider_adapter/SKILL.md`
- [ ] For Redis key naming, also read `.agents/skills/remote_user_quorum/SKILL.md`
- [ ] Verify: check → uncheck → check "Can Export" checkbox → dropdowns appear/disappear on remote page without reload
- [ ] Verify: page reload while export key is active → dropdowns appear immediately from server-rendered context
- [ ] Verify: ignore user while export is enabled → export key revoked, dropdowns disappear on remote page
- [ ] Verify: session delete → both export key entries (`export_key` forward + `remote_export:key:*` reverse) are purged from Redis
- [ ] Verify: session-scoped Trello/Jira endpoint called with export key → `has_valid_session_auth` returns `True`
- [ ] Verify: session-scoped endpoint called with a key for a different session → `has_valid_session_auth` returns `False`
