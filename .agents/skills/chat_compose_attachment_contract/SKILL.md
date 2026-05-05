# Skill: Chat Compose and Attachment Contracts

## Purpose

Defines shared interaction contracts for sending messages and handling composer attachments across:

- Home/HITL compose surface (`home.js`)
- Remote user compose surface (`remote_user.js`)

Guest page is readonly and intentionally excluded from compose behavior.

## Scope Boundary

This skill covers behavior and interaction flow:

- Send semantics (button, Enter, Shift+Enter)
- Composer enable/disable rules by run state
- Attachment selection, drag/drop, paste, and staged-chip rendering
- Upload/bind/delete request contracts and error handling

For shared layout/DOM/header contracts, use:
`../chat_surface_shared/SKILL.md`.

## Send Behavior Contract

Common rules for interactive compose surfaces:

- `Enter` sends when Shift is not pressed.
- `Shift+Enter` inserts newline.
- Send button must be disabled when message is invalid by mode.
- Textarea autosizes up to a bounded max height.

Mode-specific rules:

- Home/HITL: gate mode may allow empty text when attachment IDs are present.
- Remote user: send is tied to turn/composer state and quorum flow.

## Attachment Interaction Contract

Interactive compose surfaces must support:

- File picker via attach button
- Drag/drop on compose surface
- Clipboard paste for files/images

Staging pipeline contract:

1. Files are validated client-side (count/type/size constraints).
2. Files are uploaded before submit.
3. Server returns attachment descriptors containing IDs and display metadata.
4. UI shows chips and allows removal while staged.
5. Submit includes `attachment_ids` aligned with session/token scope.

## Attachment Rendering Contract

Composer chips must use shared semantics:

- Name, size/meta, thumbnail/icon treatment
- Remove action per staged attachment
- Clear disable/error states while uploads are in progress

Persisted message attachment rows must remain under bubble content and follow shared classes.

## Error and Race Contract

- Upload failures must surface clear non-fatal feedback.
- Partial upload states must not leak stale IDs into submit payload.
- Concurrent send actions should be guarded by disabled state.
- Server race/lock responses (quorum scenarios) must leave composer state recoverable.

## Security and Scope Contract

- Home/HITL endpoints require `X-App-Secret-Key`.
- Remote user endpoints are token-gated and must not require secret key.
- Attachment operations must remain session-scoped.

## File Ownership Map

- `server/static/server/js/home.js`: home/HITL compose + attachments
- `server/static/server/js/remote_user.js`: remote compose + attachments + quorum send integration
- `server/attachment_service.py`: upload/bind/extract/delete backend contracts
- `server/views.py`: host compose endpoints
- `server/remote_user_views.py`: remote token-gated compose endpoints
