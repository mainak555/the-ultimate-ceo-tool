# UI & Templates

## Page Layout

The app is a single-page application using HTMX for partial page updates.

```
┌─────────────────────────────────────────────────────────┐
│  HEADER                                                 │
│  [Projects ▾] [New Session] [Secret Key ___] [Configurations] │
├──────────────┬──────────────────────────────────────────┤
│  SIDEBAR     │  MAIN CONTENT (#main-content)            │
│  (project    │                                          │
│   list)      │  - Config form (create/edit)             │
│              │  - Config readonly view                  │
│  #sidebar-   │  - Placeholder text                     │
│   list       │                                          │
│              │                                          │
└──────────────┴──────────────────────────────────────────┘
```

## HTMX Interaction Flow

1. **Page load**: `config.html` renders the full shell and server-renders the sidebar list.
2. **Open project**: clicking a sidebar item or project dropdown entry sends `hx-get="/projects/<project_id>/"` and swaps `#main-content`.
3. **Secret key handling**: `app.js` injects `X-App-Secret-Key` into every HTMX request when the header input has a value.
4. **Readonly vs edit**: opening a project without a valid secret key returns `config_readonly.html`; opening it with a valid secret key returns `config_form.html`.
5. **Click "Configurations"**: browser navigates to `/projects/`, and the page auto-loads a blank create form into `#main-content`.
6. **Submit form**: `hx-post="/projects/<project_id>/"` swaps `#main-content` with the updated form. Response includes `HX-Trigger: refreshSidebar`, which causes sidebar re-fetch.
7. **Delete project**: `hx-post="/projects/<project_id>/delete/"` removes sidebar row on success; if chats exist, server returns an error and deletion is blocked.
8. **Click "Clear"**: clears all form fields visually and resets assistant cards to one empty card.

## Template Hierarchy

```
config.html                        ← Full HTML document, loaded once
├── partials/header.html           ← Included server-side via {% include %}
├── #sidebar-list                  ← Swapped by HTMX (sidebar.html)
└── #main-content                  ← Swapped by HTMX:
    ├── partials/config_form.html  ← Create/edit mode
    │   └── partials/_agent_card.html  ← Repeated per agent
    └── partials/config_readonly.html  ← Read-only mode
```

## CSS Class Conventions

- **BEM-like**: `.block__element` (e.g., `.header__title`, `.agent-card__header`)
- **Modifiers**: `.block--modifier` (e.g., `.btn--primary`, `.agent-card--readonly`)
- **Utilities**: `.badge`, `.alert`, `.form-group`, `.form-row`

## Reusable Export Popup Pattern

All export providers must follow the same structural pattern so users get a consistent workflow:

1. Vertical split workbench inside modal body.
2. Left pane: provider export workspace (destination + editable export payload).
3. Right pane: raw markdown reference rendered from `discussion.content`.
4. Footer actions: Extract, Save, Export, Cancel.

Behavior contract:

1. On open: load saved provider payload for `discussion_id` and load raw markdown reference independently.
2. Extract is explicit (never auto-runs on modal open).
3. Save persists edited payload to discussion-level exports.
4. Export uses current edited payload state.

Provider-specific changes should be limited to labels, endpoints, and payload mapping; layout/lifecycle stays the same.

## Aesthetic Consistency Contract

Cross-page UI controls must maintain shared visual language:

1. Delete buttons: same icon style, hover semantics, and danger-color behavior.
2. Color scheme: use shared SCSS tokens (`$color-*`) and existing button modifiers.
3. Spacing/typography: preserve current card/modal rhythm from `main.scss`.
4. Provider skins: may add subtle accents, but cannot override baseline destructive/control semantics.

Canonical styling rules for new SCSS work live in [docs/scss_style_guide.md](scss_style_guide.md). Follow that guide for token usage, component semantics, and responsive guardrails.

## Dynamic Agent Cards (JS)

`app.js` handles shared behavior only:
- Secret key helper access and HTMX header injection.
- Shared toast dismiss behavior after HTMX swaps.

`project_config.js` handles (config page only):
- Add/remove/reindex assistant agent cards.
- Human gate and team type field visibility and iteration constraints.
- Integrations section visibility and export-agent checkbox sync.
- Config-page secret-gated button state and sidebar delete control visibility.

`trello_config.js` handles (config page only):
- Trello integration toggle field state in `#integrations-trello-fields`.
- Trello token generation button state and popup auth flow.
- Token status refresh and hidden token metadata sync.
- Trello cascade defaults (`workspace -> board -> list`) and inline create board/list modal.

`trello.js` handles (home chat page only):
- Trello export modal for extracting and pushing chat output to Trello.

Jira export modals (home chat page only) are handled by provider-specific modules:
- `jira_software.js` — Jira Software export modal.
- `jira_service_desk.js` — Jira Service Desk export modal.
- `jira_business.js` — Jira Business export modal.

All three Jira modules share utilities from `jira.js` and register independently with `window.ProviderRegistry`.

Shared markdown rendering for Home and export reference panes must go through `server/static/server/js/markdown_viewer.js`.

`home.js` also handles (home chat page only):
- Restart controls for sessions with persisted agent state.
- Two restart modes: continue from last state, or add context and continue.
- Human gate: when the SSE `gate` event fires, a non-interactive status badge (`.chat-status-badge--gate`) is appended to chat and the bottom input bar enters **gate mode** — placeholder updates to show the round, Stop button stays visible, Send routes to `sendRespond("continue")`. No separate panel widget is injected.
- When a session with `status == "awaiting_input"` is loaded from the server, `chat_session_history.html` renders the gate status badge (carrying `data-gate-context`). Both the `DOMContentLoaded` bootstrap (initial page load) and `htmx:afterSwap` (session switch) scan for this badge and call `setGateMode()` to restore the Stop button and input placeholder without an extra API call.
- Attachment interactions for the composer: file-picker attach button, drag/drop, and clipboard paste. In gate mode the compose attachment pipeline is reused (no separate gate attachment state).
- Attachment chip rendering during composition and thumbnail rendering in chat history for image attachments.
- **Copy-to-clipboard** on every chat message (see [Chat Message Copy](#chat-message-copy) below).
- **Stop button idempotency**: the Stop button is disabled immediately on first click to prevent double-submission. `setRunningState()` always resets `disabled` when hiding the button so the next run starts with a fresh enabled state.

## Chat Message Copy

Every chat bubble (both user and agent) carries a copy icon button (`.chat-bubble__copy-btn`) in the top-right of its meta row.

### Behaviour

- Clicking the button copies the message content to the clipboard.
- **Text format**: the raw markdown source from `discussions[].content` is used — never the rendered HTML — so the recipient receives clean markdown.
- **Attachments**: if the bubble has attachment chips (`.chat-message-attachment__name`), filenames are appended below the message text as a markdown list:
  ```
  **Attachments:**
  - invoice.pdf
  - photo.png
  ```
- **Image messages**: image content is not separately base64-encoded; the attachment filename is appended as above.
- After a successful copy the icon changes to a check mark (`.chat-bubble__copy-btn--copied`, green `$color-success`) for 2 seconds, then reverts.
- Uses `navigator.clipboard.writeText()` with an `execCommand("copy")` textarea fallback for older browsers.

### Implementation

| Concern | Location |
|---|---|
| `data-raw-content` attribute | `chat_session_history.html` (server-rendered) + `appendHumanBubble()` / `handleSSEEvent()` in `home.js` (live SSE) |
| Button HTML | `_buildCopyBtn()` helper in `home.js`; inline SVG in template |
| Click handler (delegated) | `document.body` listener in `home.js` — works for both server-rendered and live-streamed bubbles |
| SCSS | `.chat-bubble__copy-btn` and `.chat-bubble__copy-btn--copied` in `main.scss` |

### Copy Button Visibility

The copy button is visible in **non-readonly sessions only** (it is part of the normal chat bubble markup and not gated by the secret key). The button is intentionally always visible so users can copy agent responses without needing write access.

## Chat Restart Discoverability

- Chat session list rows (`chat_session_list.html`) show a compact `Restartable` badge when `session.has_agent_state` is true.
- The chat history panel (`chat_session_history.html`) shows a restart panel for `completed`/`stopped` sessions with saved state.
- Restart behavior:
  - `Continue from last`: resumes with no additional user instruction.
  - `Add context and continue`: appends user-provided context before resume.
- **Send box when restart panel is visible**: when `.chat-restart-panel` is present, the `chatSendBtn` handler ignores `activeSessionIdInput` and treats the session as empty — the user's text creates a **new session** and run. The restart panel's own `data-session-id` attribute independently owns "Continue from last" / "Add context and continue".

## Configuration Surface

- **Assistant agents**: each card stores `name`, `model`, `system_prompt`, and `temperature`. The project `objective` is automatically appended to each agent's resolved system prompt at runtime.
- **Human gate**: single optional section with enable toggle and `name`. When the run pauses at a gate, the bottom input bar enters gate mode — the placeholder updates to show the round number, the Stop button stays visible, and clicking Send routes to `POST /respond/` with `action=continue`. The separate gate panel widget has been removed. There are no Approve/Reject shortcuts; users type their response directly.
- **Human gate — Remote Users (multi-assistant only)**: a nested fieldset inside the Human Gate section exposes a `quorum` selector (`yes` / `first_win` / `team_config`) and a repeating list of remote users (`{id, name, description}`) using the shared key/value form pattern. Hidden in single-assistant mode and when the gate is disabled. The local user is the **session leader** (owns MCP authorizations and starts every run); remote users only join the chat session and respond at the gate. Per-user enable/disable is a runtime concern (lobby/readiness in Phase 2) and is not a stored config field. Validation enforces unique non-empty names and rejects any remote users when there is exactly one assistant. Quorum semantics are leader-aware at runtime: `yes` requires remotes + leader continue, `first_win` accepts leader continue or first required remote response, and `team_config` may target remote IDs plus optional `leader`/`gate` selector token.
- **Human gate markdown contract**: notes sent via Continue are rendered as markdown in live bubbles and persisted history. This uses the shared markdown renderer path (`window.MarkdownViewer.render()` in `home.js`, `markdownify` in server-rendered history).
- **Team**: nested config with `type` and `max_iterations`. Supported types:
  - `round_robin` — agents take turns in fixed order.
  - `selector` — a dedicated model client routes between agents each turn. Requires `model`, `system_prompt` (supports `{roles}`, `{history}`, `{participants}`), `temperature` (default `0.0`), and `allow_repeated_speaker`. Selector fields are wrapped in an `.agent-card` container (edit) / `.agent-card--readonly` card (readonly) with header "Selector Agent" / "Selector", matching assistant agent cards.
- **Single-assistant chat mode**: when assistant count is exactly 1, Team Setup is hidden and Human Gate is forced on. In this mode the run pauses after each assistant turn and continues until the human selects Stop. In single-assistant gate mode Send is disabled until the user types text (text is mandatory — empty Continue is rejected).
- **Integrations → Trello → Export Agents**: checkboxes (`name="integrations[trello][export_agents]"`) rendered inside `#integrations-trello-fields` as the first element (above App Name). Leaving all unchecked means every agent's messages show the export button. Synced dynamically by `syncExportAgentCheckboxes()` whenever agent names change.
- **Home chat export controls**: per-agent output export dropdowns are rendered from `project.integrations.<provider>` where `enabled=true` and filtered by each provider's `export_agents` allowlist (`[]` = all agents). These controls are visible only when the Secret Key input has a value (edit/create mode behavior).
- **Home chat attachments**:
  - Composer supports attach button, drag/drop, and paste for supported file types.
  - In gate mode the same compose attachment pipeline is used — files are attached to the Continue message.
  - Uploaded attachments render as chips before send/continue.
  - Persisted messages render attachment rows under the markdown content.
  - Image attachments render thumbnails in session history; non-image files render filename links.
  - The attach button (`#chat-attach-btn`) is disabled during an active run and re-enabled when the run ends (gate or completion).
- **Integrations → Trello → Token**: the token section (`#trello-token-section`) is **always visible** when Trello is enabled (both create and edit modes). The textbox is permanently `disabled readonly`. In create mode the Generate button is disabled and the hint reads "Save the Configuration first to generate the token". After the project is saved the Generate button becomes enabled (gated by `js-requires-secret`). Once a token is generated the textbox shows `••••••••` and the hint shows the generated datetime. On edit-mode reload a previously stored token displays identically. The cascade dropdowns (`#trello-cascade-section`) remain hidden until a valid token exists.
- **Integrations → Trello → Extraction Prompt**: the extraction `system_prompt` used to parse discussions into Trello cards. Rendered as a bare `form-group` textarea in edit mode (no card wrapper). In readonly mode it appears as an `.agent-card__detail` row inside the Trello card.
- **Model list**: loaded from root `agent_models.json` and always shown in ascending order.

### Agent Card Convention

All `system_prompt` fields for agents and the selector **must** be rendered inside `.agent-card` containers in both edit and readonly views. This ensures a consistent look across assistant agents and the selector agent.

Integration extraction prompts (e.g., Trello's extraction prompt) are **not** wrapped in a card — they appear as bare `form-group` elements in edit mode and as `agent-card__detail` rows inside the integration's card in readonly mode.

**Edit mode:**
- Wrap fields in a `<div class="agent-card">`.
- Header: `<div class="agent-card__header">` containing `<span class="agent-card__number">Card Title</span>`. Non-assistant cards omit the remove button.
- Place `form-group` elements (model, temperature, prompt textarea, etc.) inside the card body.

**Readonly mode:**
- Use `<div class="agent-card agent-card--readonly">`.
- Header: `<div class="agent-card__header">` with `<strong>Name</strong>` and `<span class="badge">Model</span>` (when applicable).
- Detail rows: `<div class="agent-card__detail"><strong>Label:</strong> value</div>`.
- System prompts: `<pre class="agent-card__prompt">{{ prompt }}</pre>` inside a detail row.

When adding a new integration that has its own `system_prompt` (e.g., a Jira extraction prompt), follow this pattern to keep the UI identical to existing cards.

## Secret Key Gating

All write operations require a valid `APP_SECRET_KEY`. The secret is entered once in the header input (`#global-secret-key`) and injected into every HTMX request via `X-App-Secret-Key` header in `app.js`.

### Affected UI Elements

| Element | Location | Mechanism | No key | With key |
|---|---|---|---|---|
| **Create / Update button** | `config_form.html` — `type="submit"` | JS `disabled` | Disabled + tooltip | Enabled |
| **Clone button** | `config_form.html` — `type="button"`, `.js-requires-secret` | JS `disabled` | Disabled + tooltip | Enabled |
| **Delete (project)** | `sidebar.html` — `.sidebar__delete` | JS `hidden` | Hidden | Visible |
| **New Chat button** | `home.html` — `#new-chat-btn` | JS `hidden` | Hidden | Visible |
| **Chat send button** | `home.html` — `#chat-send-btn` | JS `disabled` | Disabled + tooltip | Enabled |
| **Chat attach button** | `home.html` — `#chat-attach-btn` | JS `disabled` | Disabled + tooltip | Enabled |
| **Chat input** | `home.html` — `#chat-input` | JS `disabled` + placeholder | Disabled with hint | Enabled |
| **HITL attach button** | `#chat-attach-btn` (shared composer) | JS `disabled` during run, re-enabled in gate mode | Disabled during active run | Enabled in gate mode and idle |
| **Delete (chat session)** | `chat_session_list.html` — `.chat-session-item__delete` | JS `hidden` | Hidden | Visible |
| **Export dropdown (chat output card)** | `chat_session_history.html` + SSE-rendered bubbles — `.chat-bubble__actions` | JS `hidden` via `updateChatAuthState()` | Hidden | Visible |
| **Copy button (chat bubble)** | `chat_session_history.html` + SSE-rendered bubbles — `.chat-bubble__copy-btn` | Always visible (not secret-gated) | Visible | Visible |
| **New-session modal** | `home.html` — `#new-session-modal` | JS closes if key removed | Auto-closed | Openable |

All write-endpoint views (`project_create`, `project_delete`, `project_clone`, `project_detail POST`, `chat_session_create`, `chat_session_delete`) also enforce the secret on the server and return a 403 response if the header is missing or invalid.

Project delete safety:
- Project delete is blocked when chat sessions exist for the project.
- The UI can show a disabled delete control for such projects, but server-side validation is authoritative.
- No cascade delete of chats is performed.

### JS Functions

- **`project_config.js` / `updateSubmitState()`** — runs on page load, after HTMX swaps, and on secret-key input changes. Handles `type="submit"` buttons, `.js-requires-secret` buttons on `.config-form`, and `.sidebar__delete` visibility/disabled state.
- **`home.js` / `updateChatAuthState()`** — runs on page load, after HTMX swaps, and on secret-key input changes. Handles home chat controls (`#chat-send-btn`, `#chat-attach-btn`, `#chat-input`, `.chat-session-item__delete`, `.chat-session-item__edit`, `.chat-bubble__actions` export visibility, edit modal safety). Also calls `_evalSendBtn()` at the end to enforce gate-mode send rules.
- **`home.js` / `setRunningState(running)`** — shows/hides the Stop button and disables/enables inputs during an active run. Always resets `chatStopBtn.disabled = false` when hiding the button so the next run starts clean.

### Adding a New Secret-Gated Button

To gate any new `type="button"` action button on the config form under the same rule:

1. Add class `js-requires-secret` to the `<button>` element in the template.
2. No JS changes required — `project_config.js` already selects `.config-form .js-requires-secret`.
3. Ensure the corresponding view checks `_has_valid_secret(request)` and returns 403 if missing.

### Read-Only Mode

When no secret key is present, visiting a project URL (`GET /projects/<id>/`) returns `config_readonly.html` instead of `config_form.html`. The readonly template shows all fields as plain text with no form, no Clone button, and no Delete controls. This is the default state on every fresh page load.

## Textarea Pre-fill Pattern

Large `<textarea>` fields (System Prompt, Selector Prompt) follow this convention:

- CSS classes: `input input--textarea input--sm input--prompt`
- `rows="12"`
- `placeholder="Paste the <field name>…"`
- Content uses an explicit `if/else` to pre-fill with a default/hint when no saved value exists:
  ```django
  {% if value %}{{ value }}{% else %}{{ hint_var }}{% endif %}
  ```
- **Do NOT use the `|default` filter** — it cannot distinguish between an unset value and an empty string submitted by the user.
- Hint variables (`default_system_prompt`, `selector_prompt_hint`) are injected by the view via the template context.
