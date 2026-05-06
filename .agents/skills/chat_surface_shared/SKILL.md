# Skill: Shared Chat Surface Contracts

## Purpose

Defines the shared visual and DOM contracts for chat surfaces across:

- Home chat panel (including HITL state in `home.js`)
- Public remote user page (`remote_user.html`)
- Public guest page (`guest_user.html`)

Use this skill before changing shared chat markup/classes or public page headers.

## Scope Boundary

This skill covers layout/surface contracts only:

- Shared class reuse (`.chat-messages`, bubbles, input panel primitives)
- Bubble DOM parity between server-rendered and JS-rendered messages
- Header contracts for remote and guest pages
- SCSS ownership boundaries for page-specific wrapper blocks

For send/enter/attachment interactions, use:
`../chat_compose_attachment_contract/SKILL.md`.

## Shared Class Reuse Contract

All chat surfaces must reuse shared classes from `server/static/server/scss/main.scss`:

- Message container: `.chat-messages`
- User bubble: `.chat-bubble.chat-bubble--human`
- Agent bubble: `.chat-bubble.chat-bubble--ai`
- Bubble meta fields: `.chat-bubble__meta`, `.chat-bubble__name`, `.chat-bubble__time`
- Bubble content: `.chat-bubble__content`
- Attachment row: `.chat-message-attachments`, `.chat-message-attachment`

Do not introduce page-specific replacements for these classes.

## Bubble DOM Parity Contract

Server-rendered templates and client-side builders must produce matching structure.

Required parity points:

- `data-raw-content` holds raw markdown source text.
- Timestamps use `<time class="local-time" data-utc="...">`.
- Agent bubbles keep avatar/body nesting used by home history rendering.
- Attachment rows render under markdown content, not above it.
- Every bubble includes a copy button in the meta row:
	`<button type="button" class="chat-bubble__copy-btn" ...>`.

When changing bubble markup in one surface, update other surfaces/builders in the same PR.

## Message Copy Parity Contract

Copy behavior is shared across Home, Remote, and Guest surfaces.

Required behavior:

- Copy source is `data-raw-content` (raw markdown), never rendered HTML.
- If attachments exist, append this exact markdown block:
	- `**Attachments:**`
	- one `- [filename](absolute_url)` line per `.chat-message-attachment` anchor in display order
	- escape `[` and `]` in filename text; if `href` is missing, fallback to `- filename`
- Clipboard flow uses `navigator.clipboard.writeText()` with textarea +
	`document.execCommand("copy")` fallback.
- On success, `.chat-bubble__copy-btn` toggles to
	`.chat-bubble__copy-btn--copied` for 2 seconds, then resets.

Changing copy format/selectors/feedback in one surface requires matching updates
to all surfaces in the same PR.

## Public Header Contract (Remote + Guest)

Remote and guest pages must both use a two-column header pattern:

- Left: page title text (`session` or `project`-scoped title)
- Right: role badge (`Remote Participant` or `Guest`)

The header uses page-wrapper element naming:

- Remote page: `.remote-user-page__header`, `__title`, `__badge`
- Guest page: `.guest-user-page__header`, `__title`, `__badge`

This keeps role identity explicit while preserving a unified public-page layout language.

## Readonly vs Interactive Surface Rules

- Guest page is readonly: header + chat history only.
- Remote page is interactive: header + chat history + composer.
- Home/HITL uses the main app shell and shared chat primitives.

Do not add compose controls to guest page.

## Home Project Context Contract

When `chat_session_history.html` renders the Home project context state (no active
session selected), keep participant and chip behavior aligned with runtime data.

Required behavior:

- Participants cards include all assistant agents.
- If human gate is enabled, include the human gate owner card.
- If `human_gate.remote_users` is non-empty, include one card per remote user.
- Team metadata renders as separate chips in one row:
	- base chip: team type + max iterations
	- quorum chip: shown only when remote users exist
- Quorum chip text must come from `server/util.py::QUORUM_OPTIONS` labels
	(single source of truth), not hardcoded template strings.

Implementation notes:

- Keep assistant cards as the only clickable cards for prompt viewer behavior.
- Preserve existing class hooks in `main.scss` for project context cards/chips.

## Display Name Contract

Chat bubble name labels are viewer-scoped and must remain consistent across
server-rendered history and JS-rendered live messages.

Required behavior:

- Home/HITL: user-role bubbles display `You`.
- Remote page: display `You` only when the user-role message sender matches
	the currently joined remote participant identity for that page.
- Remote page: user-role messages from other participants display their sender
	names (not `You`).
- Guest page: all user-role messages display sender names; guest never gets a
	viewer-relative `You` label.

Implementation notes:

- Apply the same rule in template history and live message builders.
- Preserve `.chat-bubble__name` and existing bubble DOM structure.
- Do not change copy behavior (`data-raw-content`) when adjusting labels.

## SCSS Ownership Boundary

Only wrapper-specific chrome belongs to page wrapper blocks:

- `.remote-user-page { ... }`
- `.guest-user-page { ... }`

Allowed wrapper concerns:

- Header, title, badge
- Waiting/error/evict overlays
- Page-level flex and overflow behavior

Disallowed wrapper concerns:

- Re-implementing bubble/input core styles already covered by shared chat classes.

## File Ownership Map

- `server/templates/server/remote_user.html`: remote page structure
- `server/templates/server/guest_user.html`: guest page structure
- `server/static/server/js/remote_user.js`: remote bubble builders/live rendering
- `server/static/server/js/guest_user.js`: guest readonly live rendering
- `server/static/server/scss/main.scss`: shared chat classes + wrapper blocks
- `server/templates/server/partials/chat_session_history.html`: canonical home history bubble structure
