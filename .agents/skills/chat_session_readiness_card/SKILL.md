---
name: chat-session-readiness-card
description: Use when adding, changing, or reviewing chat session readiness cards in Home chat history. Enforces exact state/event/data-hook behavior while allowing use-case-specific UI copy/layout overrides.
---

# Chat Session Readiness Card Skill

Use this skill when touching readiness cards in chat history UI, including:

- gate badge (`.chat-status-badge--gate`)
- OAuth readiness panel (`.chat-oauth-panel`)
- remote participants panel (`.chat-remote-panel`)
- restart readiness panel (`.chat-restart-panel`)

## When this skill applies

- Editing `server/templates/server/partials/chat_session_history.html`
- Editing readiness-card logic in `server/static/server/js/home.js`
- Editing readiness-card styles in `server/static/server/scss/main.scss`
- Adding a new chat-session readiness state/card
- Refactoring OAuth or gate readiness flow in chat history

## Source of truth

Read and follow:

- `docs/chat_session_readiness_cards.md`
- `docs/UI.md`
- `docs/mcp_integration.md` (for OAuth gate details)

## Mandatory contracts

### Behavioral contract is strict

- Readiness cards are in-history surfaces, not global modal replacements.
- State/event names and API contracts remain stable.
- Required class hooks/data attributes stay intact unless all consumers are
  migrated together.

### UI can be overridden by use case

Allowed overrides:

- card title/hint text
- iconography
- button label text
- token-based visual treatment
- internal card structure that keeps required hooks

OAuth visual-state guardrail:

- Keep `.chat-oauth-panel__row` borders neutral across pending/authorized
  states.
- Represent OAuth authorization state via status chip modifiers only
  (`.chat-oauth-panel__status--pending` / `--authorized`) and matching labels.
- Do not reintroduce row-level `--authorized` border color classes.

Not allowed overrides:

- removing required selectors or data attributes used by JS restore logic
- renaming SSE events (`gate`, `awaiting_mcp_oauth`, `done`, `stopped`)
- changing OAuth 409 gate payload shape
- moving readiness behavior out of chat history without replacing all restore logic

### State/render contract

Server render (`chat_session_history.html`):

- `awaiting_input` renders `.chat-status-badge--gate[data-gate-context]`
- `awaiting_mcp_oauth` renders `.chat-oauth-panel[data-session-id][data-project-id]`
- `awaiting_remote_users` renders `.chat-remote-panel[data-session-id][data-project-id][data-quorum]`
- `completed|stopped` + `has_agent_state` renders `.chat-restart-panel`

Client runtime (`home.js`):

- Remove stale readiness cards before a new run starts.
- Handle OAuth gate from both pre-run 409 and mid-run SSE event.
- Handle remote participants gate from pre-run 409 `status:"awaiting_remote_users"`.
- Preserve deferred remote-disconnect behavior: disconnect during `running` does
  not interrupt the current run; block the next run behind `.chat-remote-panel`.
- Restore readiness state on both `DOMContentLoaded` and `htmx:afterSwap`.

## Review checklist

- Does the new card follow the shared in-history readiness pattern?
- Are restore hooks preserved for reload and HTMX swap?
- Are OAuth/gate semantics untouched unless intentionally migrated end-to-end?
- Are styles token-based and consistent with chat panel rhythm?
- Does OAuth readiness use chip-only state indication (no row border state)?
- Are any behavior changes documented in `docs/chat_session_readiness_cards.md`?

## Anti-patterns (block in review)

- Duplicating readiness behavior in a separate floating modal while leaving
  existing in-history hooks partially active.
- Introducing polling for OAuth readiness when WS + postMessage already exist.
- Changing class names/data attrs without updating restore and delegated handlers.
- Hardcoding provider/use-case colors instead of SCSS tokens.
