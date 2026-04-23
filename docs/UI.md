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

## Chat Restart Discoverability

- Chat session list rows (`chat_session_list.html`) show a compact `Restartable` badge when `session.has_agent_state` is true.
- The chat history panel (`chat_session_history.html`) shows a restart panel for `completed`/`stopped` sessions with saved state.
- Restart behavior:
  - `Continue from last`: resumes with no additional user instruction.
  - `Add context and continue`: appends user-provided context before resume.

## Configuration Surface

- **Assistant agents**: each card stores `name`, `model`, `system_prompt`, and `temperature`. The project `objective` is automatically appended to each agent's resolved system prompt at runtime.
- **Human gate**: single optional section with enable toggle, `name`, and `interaction_mode` (`approve_reject` or `feedback`). `approve_reject` pauses after each round and lets the user approve (continue) or reject (provide feedback). `feedback` always collects free-text feedback before continuing.
- **Team**: nested config with `type` and `max_iterations`. Supported types:
  - `round_robin` — agents take turns in fixed order.
  - `selector` — a dedicated model client routes between agents each turn. Requires `model`, `system_prompt` (supports `{roles}`, `{history}`, `{participants}`), `temperature` (default `0.0`), and `allow_repeated_speaker`. Selector fields are wrapped in an `.agent-card` container (edit) / `.agent-card--readonly` card (readonly) with header "Selector Agent" / "Selector", matching assistant agent cards.
- **Integrations → Trello → Export Agents**: checkboxes (`name="integrations[trello][export_agents]"`) rendered inside `#integrations-trello-fields` as the first element (above App Name). Leaving all unchecked means every agent's messages show the export button. Synced dynamically by `syncExportAgentCheckboxes()` whenever agent names change.
- **Home chat export controls**: per-agent output export dropdowns are rendered from `project.integrations.<provider>` where `enabled=true` and filtered by each provider's `export_agents` allowlist (`[]` = all agents). These controls are visible only when the Secret Key input has a value (edit/create mode behavior).
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
| **Chat input** | `home.html` — `#chat-input` | JS `disabled` + placeholder | Disabled with hint | Enabled |
| **Delete (chat session)** | `chat_session_list.html` — `.chat-session-item__delete` | JS `hidden` | Hidden | Visible |
| **Export dropdown (chat output card)** | `chat_session_history.html` + SSE-rendered bubbles — `.chat-bubble__actions` | JS `hidden` via `updateChatAuthState()` | Hidden | Visible |
| **New-session modal** | `home.html` — `#new-session-modal` | JS closes if key removed | Auto-closed | Openable |

All write-endpoint views (`project_create`, `project_delete`, `project_clone`, `project_detail POST`, `chat_session_create`, `chat_session_delete`) also enforce the secret on the server and return a 403 response if the header is missing or invalid.

Project delete safety:
- Project delete is blocked when chat sessions exist for the project.
- The UI can show a disabled delete control for such projects, but server-side validation is authoritative.
- No cascade delete of chats is performed.

### JS Functions

- **`project_config.js` / `updateSubmitState()`** — runs on page load, after HTMX swaps, and on secret-key input changes. Handles `type="submit"` buttons, `.js-requires-secret` buttons on `.config-form`, and `.sidebar__delete` visibility/disabled state.
- **`home.js` / `updateChatAuthState()`** — runs on page load, after HTMX swaps, and on secret-key input changes. Handles home chat controls (`#chat-send-btn`, `#chat-input`, `.chat-session-item__delete`, `.chat-session-item__edit`, edit modal safety).
- **`home.js` / `updateChatAuthState()`** — runs on page load, after HTMX swaps, and on secret-key input changes. Handles home chat controls (`#chat-send-btn`, `#chat-input`, `.chat-session-item__delete`, `.chat-session-item__edit`, `.chat-bubble__actions` export visibility, edit modal safety).

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
