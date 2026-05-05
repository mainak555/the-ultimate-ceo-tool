# Frontend JavaScript Architecture

This project uses small feature-focused JavaScript modules.
The goal is to prevent `app.js` from becoming a catch-all file.

## Module Ownership

| Module | Scope | Allowed Responsibilities | Not Allowed |
|---|---|---|---|
| `server/static/server/js/app.js` | Shared SPA shell behavior across pages | HTMX secret-key header injection, shared helpers, generic cross-page hooks | Feature-specific config/chat/integration workflows |
| `server/static/server/js/markdown_viewer.js` | Shared markdown rendering utility | Common markdown-to-HTML rendering for Home and export reference panes | Feature-specific modal/session workflows |
| `server/static/server/js/chat_copy_utils.js` | Shared chat utility for Home/Remote/Guest | Chat bubble copy button HTML, raw-markdown copy payload with attachments, clipboard fallback, copied-state feedback, delegated copy binding | Page-specific chat/session workflow logic |
| `server/static/server/js/provider_registry.js` | Shared provider capability registry | Register and resolve provider capabilities (`openExportModal`, `syncConfigState`) | Provider-specific UI behavior |
| `server/static/server/js/project_config.js` | Config page only (`config_form.html`) | Project-config form state sync, agent-card manipulation, config-page secret-gated controls | Home chat runtime behavior |
| `server/static/server/js/mcp_json_editor.js` | Config page only (`config_form.html`) | MCP JSON code-editor mount/lifecycle, format/validate controls, textarea sync for submit | MCP schema business validation, server-side transport logic |
| `server/static/server/js/home.js` | Home page only (`home.html`) | Chat UI interactions, chat session actions, SSE rendering, human-gate flow, secret-gated export control visibility | Config-page form and integration setup behavior |
| `server/static/server/js/remote_user.js` | Remote public page only (`remote_user.html`) | Remote chat rendering, turn-based composer, attachment interactions, copy-to-clipboard parity for server/live bubbles | Home/config workflows |
| `server/static/server/js/guest_user.js` | Guest public page only (`guest_user.html`) | Readonly chat rendering, live WS updates, copy-to-clipboard parity for server/live bubbles | Compose/send or config workflows |
| `server/static/server/js/trello_config.js` | Config page only (`config_form.html`) | Trello token generation, token status sync, workspace/board/list cascade defaults, create board/list modal | Chat export modal behavior |
| `server/static/server/js/trello.js` | Home chat page export flow | Export modal open/close, extraction preview, destination selection, push to Trello | Config-page settings and token generation UX |

## Script Loading Rules

1. Load only the scripts a page needs.
2. Keep feature modules independent from each other.
3. Shared helpers stay in `app.js`; feature modules can expose a small namespace on `window` when needed.

Current template usage:
- `config.html`: `app.js`, `provider_registry.js`, `markdown_viewer.js`, `config_readonly_markdown.js`, `mcp_json_editor.js`, `project_config.js`, `trello_config.js`, `jira.js`, `jira_config.js`
- `home.html`: `app.js`, `provider_registry.js`, `markdown_viewer.js`, `home.js`, `trello.js`

## Event Contract

1. `app.js` owns shared helpers only and may call optional feature hooks when present.
2. Feature modules should provide one idempotent sync entry point for re-render scenarios:
   - Example: `window.TrelloConfig.syncFromForm()`
3. Feature modules must be resilient to HTMX swaps (`htmx:afterSwap`) and no-op when their DOM is absent.
4. Shared modules should call provider capabilities through `window.ProviderRegistry` and never directly depend on `window.<ProviderName>` globals.

## Provider Adapter Contract

Each export provider module should register capabilities:

```javascript
window.ProviderRegistry.register("provider_name", {
   openExportModal: function (context) {},
   syncConfigState: function (context) {},
});
```

Where `context` includes `sessionId`, `discussionId`, `secretKey`, and `csrfToken` when applicable.

## Adding a New Frontend Feature

1. Create a new module under `server/static/server/js/<feature>.js`.
2. Add a short file header describing scope and non-goals.
3. Keep selectors and listeners scoped to that feature's DOM.
4. Load the new script only on pages that render the feature.
5. Update docs:
   - `docs/UI.md` (user-facing interaction flow)
   - `docs/ARCHITECTURE.md` (module boundary summary)
   - Feature doc if one exists (for example `docs/trello_integration.md`)
6. If the feature is a new export provider, implement adapter registration in that provider module and do not edit shared orchestration to add provider switches.

## Review Checklist

- Is this logic feature-specific rather than shared?
- If yes, does it live outside `app.js`?
- Does the module cleanly no-op when the related DOM is missing?
- Are HTMX swap re-initialization and state sync handled?
- Were docs updated with ownership and loading changes?
