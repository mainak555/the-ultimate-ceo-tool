# Chat Session Readiness Cards

This document defines the required pattern for chat-session readiness cards in
the Home chat history. The behavioral contract is strict. Visual UI details may
be overridden by use case when the core contract remains unchanged.

## Scope

Applies to in-history readiness surfaces in:

- `server/templates/server/partials/chat_session_history.html`
- `server/static/server/js/home.js`
- `server/static/server/scss/main.scss`

Current readiness surfaces:

- Human gate badge (`.chat-status-badge--gate`)
- MCP OAuth readiness panel (`.chat-oauth-panel`)
- Restart readiness panel (`.chat-restart-panel`)
- Remote participants panel (`.chat-remote-panel`)

## Core Pattern (mandatory)

1. Readiness cards render inside chat history, not in global modals.
2. Exactly one active blocking readiness card is shown at a time.
3. Readiness cards are state-driven by `session.status` and run events.
4. Cards expose explicit unblock actions in the same card context.
5. Cards are resilient to page reload and HTMX session swap.

## State-to-Card Mapping

Server-rendered mapping (`chat_session_history.html`):

- `awaiting_input` -> `.chat-status-badge.chat-status-badge--gate`
- `awaiting_mcp_oauth` -> `.chat-oauth-panel`
- `completed|stopped` with `has_agent_state=true` -> `.chat-restart-panel`

Runtime mapping (`home.js`):

- `event: gate` -> append gate badge and enter gate mode
- `event: awaiting_mcp_oauth` -> show OAuth readiness panel
- `event: done|stopped` -> append status badge and restart panel (when applicable)

## Mandatory DOM/Data Contract

### Gate badge

- Class: `.chat-status-badge--gate`
- Must carry `data-gate-context` JSON for reload restore.
- JSON fields:
  - `round`
  - `max_rounds`
  - `chat_mode`

### OAuth panel

- Root class: `.chat-oauth-panel`
- Data attrs:
  - `data-session-id`
  - `data-project-id`
- Rows container: `.chat-oauth-panel__rows`
- Loading marker: `.chat-oauth-panel__loading`
- Row class: `.chat-oauth-panel__row` with `data-server-name`
- Authorize action class: `.chat-oauth-authorize-btn`

### Restart panel

- Root class: `.chat-restart-panel` with `data-session-id`
- Continue action: `.chat-restart-btn--continue`
- Continue-with-context action: `.chat-restart-btn--with-context`
- Submit context action: `.chat-restart-btn--submit`

## Lifecycle Contract

1. `startRun()` removes stale readiness surfaces (`.chat-status-badge`,
   `.chat-restart-panel`, `.chat-oauth-panel`, `.chat-remote-panel`) before
   initiating a run.
2. OAuth gate can be triggered from:
   - Pre-run HTTP 409: `{status:"awaiting_mcp_oauth", servers:[...]}`
   - Mid-run SSE event `awaiting_mcp_oauth`
3. OAuth readiness must support:
   - WS push (`state` / `update` / `complete`)
   - Popup postMessage fallback
4. On full authorization, the run is replayed automatically.
5. Quorum replay behavior is mode-specific:
    - `first_win`: host receives `quorum_committed` and auto-replays the run via
       `startRun("", [])` (pending task is popped server-side).
    - `all`: host receives `quorum_progress` updates until all expected responses
       are present, then host final Continue commits and resumes.
6. On `DOMContentLoaded` and `htmx:afterSwap`, readiness state is restored by
   scanning server-rendered cards in history.

## UI Override Policy (allowed)

You may override these by use case:

- Title/hint copy
- Icons/emoji
- Button labels
- Accent color choices using existing SCSS tokens
- Internal card layout that preserves required hooks

## UI Override Policy (not allowed)

Do not change without full contract migration:

- Required CSS hooks and data attributes listed above
- SSE event names (`gate`, `awaiting_mcp_oauth`, `done`, `stopped`)
- API response contract for OAuth gate (`409` + `status:"awaiting_mcp_oauth"`)
- WebSocket readiness message types (`state`, `update`, `complete`)
- In-history placement of readiness cards

## Implementation Checklist

- Update template + JS + SCSS together when adding a readiness variant.
- Keep behavior idempotent when cards are inserted multiple times.
- Ensure non-ready states clear obsolete readiness cards.
- Verify both initial page load and HTMX swaps restore readiness correctly.
- Keep styling token-based (no provider-specific hardcoded colors).

## Related References

- `docs/UI.md`
- `docs/mcp_integration.md`
- `.agents/skills/active_session_coordination/SKILL.md`
- `.agents/skills/mcp_tool_integration/SKILL.md`
- `.agents/skills/remote_user_quorum/SKILL.md`
