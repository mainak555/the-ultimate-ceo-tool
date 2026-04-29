---
name: datetime-storage
description: >
  Use when writing any code that stores, reads, normalises, or displays a
  date/time value in MongoDB (BSON), a Python service, or a JS/HTML template.
  Enforces the project-wide rule: store BSON Date, display browser-local time.
---

# Skill: Datetime Storage & Display

## The One Rule

> **Store UTC BSON Date objects in MongoDB. Display browser-local time in the UI.**

Never store datetime values as ISO strings or bare `HH:MM` strings in MongoDB
documents. Never format a datetime to a string before writing to the DB.
Never display a raw UTC string to the user.

---

## Python (write path)

### 1. Produce datetimes with `_utc_now()`

```python
# server/services.py exposes this helper — import it or call it directly
from datetime import datetime, timezone

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
```

Use `_utc_now()` everywhere a datetime is written to MongoDB:

```python
doc["created_at"] = _utc_now()   # BSON Date ✓
doc["updated_at"] = _utc_now()   # BSON Date ✓
```

### 2. Never call `.isoformat()` or `.strftime()` before writing to MongoDB

```python
# BAD — stores a string, not a BSON Date
doc["timestamp"] = datetime.now(timezone.utc).isoformat()

# GOOD — stores a BSON Date
doc["timestamp"] = _utc_now()
```

### 3. When you need an ISO string for a JSON response, derive it after writing

```python
now_dt  = _utc_now()
now_iso = now_dt.isoformat()          # for the JSON / SSE payload only
doc["timestamp"] = now_dt             # BSON Date → MongoDB
return {"timestamp": now_iso}         # ISO string → JSON response
```

---

## Python (read / normalize path)

Use the shared coercion helper `_coerce_dt_to_iso()` from `server/services.py`
whenever a datetime field is read from MongoDB and must be serialised for
template rendering or a JSON response.

```python
def _coerce_dt_to_iso(value) -> str:
    """datetime → ISO string; string passthrough; None → ''."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)  # PyMongo naive UTC coercion
        return value.isoformat()
    return str(value) if value else ""
```

Apply it in every `normalize_*` function:

```python
"created_at": _coerce_dt_to_iso(doc.get("created_at")),
"updated_at": _coerce_dt_to_iso(doc.get("updated_at")),
"token_generated_at": _coerce_dt_to_iso(raw_trello.get("token_generated_at")),
"saved_at": _coerce_dt_to_iso(agent_state.get("saved_at")),
```

### PyMongo naive UTC coercion

PyMongo returns `datetime` objects **without** `tzinfo` (they are UTC by
convention). Always apply `.replace(tzinfo=timezone.utc)` before calling
`.isoformat()` to ensure the serialised string carries `+00:00`.

---

## Template (render path)

Wrap every datetime value in a `<time data-utc="...">` element. Leave the
inner text as the raw ISO string (fallback for bots/screen readers). The
`renderLocalTimes()` function in `app.js` will overwrite it with the
browser-local formatted time.

```html
<!-- session list / chat header — show date + time -->
<time class="local-time" data-utc="{{ session.created_at }}">{{ session.created_at }}</time>

<!-- message bubbles — show time only -->
<time class="local-time" data-utc="{{ msg.timestamp }}">{{ msg.timestamp }}</time>
```

Do **not** use Django template filters (`|date`, `|time`) on these values —
they format in server timezone, not the user's local timezone.

---

## JavaScript (write path — live-injected bubbles)

When injecting a DOM element that needs a timestamp (user bubble, gate
bubble, SSE agent bubble), use `new Date().toISOString()` for the
`data-utc` attribute and call `window.renderLocalTimes()` afterwards.

```js
var ts = new Date().toISOString();
appendBubble(
  '<div class="chat-bubble chat-bubble--human">'
  + '<span class="chat-bubble__time">'
  + '<time class="local-time" data-utc="' + ts + '">' + ts + '</time>'
  + '</span>'
  + '</div>'
);
window.renderLocalTimes();
```

For SSE-delivered agent messages, the server already emits an ISO string in
`data.timestamp` — pass it directly to `data-utc`:

```js
var ts = data.timestamp || "";
// ... build bubble HTML with data-utc="' + ts + '"
window.renderLocalTimes();
```

---

## JavaScript (`renderLocalTimes` — display path)

Defined in `server/static/server/js/app.js`, exposed as `window.renderLocalTimes`.
Called automatically on `DOMContentLoaded` and `htmx:afterSwap`. Other modules
call it manually after dynamic DOM injection.

```js
function renderLocalTimes() {
  document.querySelectorAll("time[data-utc]").forEach(function (el) {
    var iso = el.dataset.utc;
    if (!iso) return;
    var d = new Date(iso);
    if (isNaN(d.getTime())) {
      el.textContent = iso;   // graceful fallback for legacy bare "HH:MM" strings
      return;
    }
    var hasDate = iso.indexOf("T") !== -1;
    var opts = hasDate
      ? { dateStyle: "medium", timeStyle: "short" }
      : { timeStyle: "short" };
    el.textContent = d.toLocaleString(navigator.language, opts);
  });
}
```

**No external library** is required — native `Intl.DateTimeFormat` (via
`Date.toLocaleString`) covers all target browsers.

---

## Canonical Field Types (from `docs/db_schema.md`)

| Collection | Field | Type in MongoDB |
|---|---|---|
| `project_settings` | `created_at` | `datetime` (UTC BSON Date) |
| `project_settings` | `updated_at` | `datetime` (UTC BSON Date) |
| `project_settings` | `integrations.trello.token_generated_at` | `datetime` (UTC BSON Date) |
| `chat_sessions` | `created_at` | `datetime` (UTC BSON Date) |
| `chat_sessions` | `discussions[].timestamp` | `datetime` (UTC BSON Date) |
| `chat_sessions` | `agent_state.saved_at` | `datetime` (UTC BSON Date) |
| `chat_sessions` | `discussions[].exports.*.updated_at` | `datetime` (UTC BSON Date — coerced to ISO string on read) |
| `chat_sessions` | `discussions[].exports.*.last_push.pushed_at` | `datetime` (UTC BSON Date — coerced to ISO string on read) |
| `chat_attachments` | `uploaded_at` | `datetime` (UTC BSON Date) |
| `chat_attachments` | `bound_at` | `datetime` (UTC BSON Date) |

---

## Migration / Backward Compatibility

**No migration is required for new code**, but existing documents in MongoDB
may contain legacy string values in fields that should now be BSON Dates.

The `_coerce_dt_to_iso()` helper and `_normalize_discussion()` in
`server/services.py` handle both datetime objects and legacy strings
transparently at read time.

---

## Checklist for New Datetime Fields

Before adding a new datetime field to any MongoDB document:

- [ ] Type in `docs/db_schema.md` is `datetime (UTC BSON Date)`
- [ ] Written with `_utc_now()` (not `.isoformat()`, not `.strftime()`)
- [ ] Read path calls `_coerce_dt_to_iso()` in the `normalize_*` function
- [ ] Template wraps the value in `<time class="local-time" data-utc="...">` 
- [ ] JS injection uses `new Date().toISOString()` + `window.renderLocalTimes()`
- [ ] If returned in a JSON response, convert to ISO string *after* the DB write
