# Skill: Remote User Chat Page

## Purpose

Documents the architecture and implementation rules for the **public remote-user
chat page** (`server/templates/server/remote_user.html` +
`server/static/server/js/remote_user.js`).  Read this skill before modifying
either file or the `.remote-user-page` SCSS block.

---

## Design Principle — Maximum CSS Reuse

The remote-user page is a **standalone public page** (token-gated, no secret key,
no toolbar).  Its message bubbles and input panel **reuse the same shared CSS
classes** used by the home chat panel:

| UI element | Class to use | Source |
|---|---|---|
| Message scroll container | `.chat-messages` | `main.scss` |
| Agent bubble | `.chat-bubble.chat-bubble--ai` | `main.scss` |
| User bubble | `.chat-bubble.chat-bubble--human` | `main.scss` |
| Input panel wrapper | `.chat-input-panel` | `main.scss` |
| Input row | `.chat-input-row` | `main.scss` |
| Textarea | `.chat-input__textarea` | `main.scss` |
| Send button | `.btn.btn--primary.chat-send-btn` | `main.scss` |
| Attach button | `.chat-attach-btn` | `main.scss` |
| Attachment chip list | `.chat-attachment-list` | `main.scss` |

**No new CSS classes should be added for these UI elements.**  Only
`.remote-user-page__*` modifier classes (header, badge, error, waiting,
evict overlay) are owned by the remote-user page block.

---

## Template Rules (`remote_user.html`)

1. **Load line must include `md_extras`**:
   ```django
   {% load compress static md_extras %}
   ```
   This gives access to the `markdownify` filter used to render server-side
   discussion history.

2. **Bubble structure must match `chat_session_history.html` exactly.**

   Agent bubble:
   ```html
   <div class="chat-bubble chat-bubble--ai" data-raw-content="{{ d.content }}">
     <div class="chat-bubble__avatar">{{ d.agent_name|slice:":1"|upper }}</div>
     <div class="chat-bubble__body">
       <div class="chat-bubble__meta">
         <span class="chat-bubble__name">{{ d.agent_name|default:"Agent" }}</span>
         <span class="chat-bubble__time">
           <time class="local-time" data-utc="{{ d.timestamp }}">{{ d.timestamp }}</time>
         </span>
       </div>
       <div class="chat-bubble__content">{{ d.content|markdownify }}</div>
     </div>
   </div>
   ```

   User bubble:
   ```html
   <div class="chat-bubble chat-bubble--human" data-raw-content="{{ d.content }}">
     <div class="chat-bubble__meta">
       <span class="chat-bubble__name">{{ user_name|default:"You" }}</span>
       <span class="chat-bubble__time">
         <time class="local-time" data-utc="{{ d.timestamp }}">{{ d.timestamp }}</time>
       </span>
     </div>
     <div class="chat-bubble__content">{{ d.content|markdownify }}</div>
   </div>
   ```

3. **Copy buttons must NOT appear** — this is a public page (no secret key).

4. **Input panel IDs** use the `remote-` prefix to avoid conflicts with home.js:
   - `#remote-attach-btn` — paperclip attach button
   - `#remote-chat-input` — `.chat-input__textarea`
   - `#remote-send-btn` — send button
   - `#remote-attach-input` — hidden `<input type="file" hidden multiple>`
   - `#remote-compose-attachments` — `.chat-attachment-list` (hidden by default)

5. **JS bootstrap block** sets three window globals that remote_user.js reads:
   ```html
   <script>
     window._remoteUserToken = "{{ token }}";
     window._remoteUserName  = "{{ user_name }}";
     window._remoteSessionId = "{{ session_id }}";
   </script>
   ```
   Place this block **before** the compressed JS include.

6. **No toolbar row** — the remote user page has no project selector,
   session controls, or agent-state UI.

---

## JS Rules (`remote_user.js`)

All code lives inside a single self-executing IIFE `(function(){ "use strict"; … })();`.

### Bubble factories

`buildAgentBubble(msg)` and `buildUserBubble(text)` must produce identical DOM
structure to the server-rendered bubbles above (same class names, same nesting).
Use `marked.parse()` for client-side markdown rendering of WebSocket-delivered
messages.

### `appendBubble(el)` contract

1. Remove `.remote-user-page__waiting` placeholder if present.
2. Append `el` to `#remote-chat-messages`.
3. Call `renderLocalTimes()` to format any `<time class="local-time">` elements.
4. Scroll the container to bottom.

### WebSocket message types

| `type` | Action |
|---|---|
| `history` | Inject server history only when `.remote-user-page__waiting` placeholder is present (i.e. no server-rendered discussions). Skip user messages (already shown server-side or not applicable). |
| `message` | Call `appendBubble(buildAgentBubble(m))` for non-user messages. |
| `evict` | Close WS, call `showEvictionOverlay()`. |
| `error` | Call `showEvictionOverlay()`. |

The `history` handler must populate bubbles using the same builder functions used
for live WS messages — do not duplicate HTML string generation.

### `sendMessage()` contract

1. Read `#remote-chat-input`, trim. Return early if empty.
2. Call `appendBubble(buildUserBubble(text))` immediately.
3. Clear the textarea and reset its height.
4. If `_ws` is open, `_ws.send(JSON.stringify({type:"message", content:text}))`.

### Textarea auto-grow

```js
input.addEventListener("input", function () {
  input.style.height = "";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});
```

`Enter` without `Shift` triggers `sendMessage()`; `Shift+Enter` inserts a newline.

### `_ws` module variable

Declare `var _ws = null;` at module scope so the `evict` handler can call
`_ws.close()` from inside the `onmessage` closure.

---

## SCSS Rules (`server/static/server/scss/main.scss`)

The `.remote-user-page { }` block owns only page-chrome elements:

| Sub-rule | Description |
|---|---|
| `&__header` | Top bar: flex row with title + badge |
| `&__title` | Left-side project name |
| `&__badge` | Right-side user-name pill |
| `&__main` | `flex:1; display:flex; flex-direction:column; overflow:hidden` |
| `&__waiting` | Centred muted placeholder text |
| `&__error` | Centred error state |
| `&__error-icon`, `&__error-text` | Error display |
| `&__evict-overlay`, `&__evict-card`, etc. | Eviction overlay |
| `.remote-panel-quorum-label`, `.remote-panel-quorum-select` | Quorum dropdown in the home gate panel |

**Do NOT add** `&__messages`, `&__input-bar`, or `&__input-hint` sub-rules —
these were removed because the shared classes cover those roles entirely.

---

## Ownership Boundary

| Layer | Owns |
|---|---|
| `server/remote_user_views.py` | Token validation, context building (`discussions`, `user_name`, `project_name`, `session_id`) |
| `server/remote_user_urls.py` | `/remote/join/<token>/`, `/remote/join/<token>/online/`, quorum URL |
| `server/consumers.py` `RemoteChatConsumer` | WS lifecycle: `history`, `message`, `evict`, `error` event dispatch |
| `agents/session_coordination.py` | Remote-user Redis helpers (token CRUD, quorum, online status) |
| `server/templates/server/remote_user.html` | Public page template (no secret key, no toolbar) |
| `server/static/server/js/remote_user.js` | Client JS: WS, bubble factories, send, auto-grow, attach preview |
| `main.scss` `.remote-user-page { }` | Page chrome only — shared chat classes are used for bubbles/input |
