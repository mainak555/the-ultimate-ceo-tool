---
name: mongo-collection-constants
description: >
  Use when adding, changing, or reviewing MongoDB access code. Enforces the
  project-wide rule that runtime code must never hardcode collection names.
  Collection-name literals are defined once in server/db.py and reused via
  imported constants at every get_collection(...) call site.
---

# Skill: Mongo Collection Constants

## Purpose

Prevent drift, typos, and partial refactors by centralizing MongoDB collection
names in one module and reusing those constants everywhere.

## Mandatory Rule

Runtime code must never inline collection-name literals.

Use this pattern:

```python
from .db import PROJECT_SETTINGS_COLLECTION, get_collection

col = get_collection(PROJECT_SETTINGS_COLLECTION)
```

Do not use this pattern in runtime code:

```python
col = get_collection("project_settings")
```

## Source Of Truth

Collection constants live in server/db.py.

Current constants:

- PROJECT_SETTINGS_COLLECTION
- CHAT_SESSIONS_COLLECTION
- CHAT_ATTACHMENTS_COLLECTION

## Scope

Apply this rule to:

- server/services.py
- server/views.py
- server/trello_service.py
- server/jira_service.py and type-owned jira services
- server/attachment_service.py
- any future server or agents runtime module that calls get_collection(...)

Literal names are only allowed in:

- constant declarations in server/db.py
- documentation examples
- one-off migration scripts outside runtime paths

## Checklist For Any Mongo Change

- [ ] No runtime call site uses get_collection("...") with a literal.
- [ ] Required collection constant exists in server/db.py.
- [ ] Call sites import the constant from server/db.py.
- [ ] docs/db_schema.md stays aligned with canonical collection names.
- [ ] AGENTS.md rule set stays aligned with this contract.

## Adding A New Collection

1. Add `<NAME>_COLLECTION = "<name>"` to server/db.py.
2. If indexes are needed, create them in ensure_indexes() in server/db.py.
3. Import and use the constant at all call sites.
4. Document the collection in docs/db_schema.md.

## Suggested Verification Commands

```powershell
rg -n 'get_collection\("[a-z_]+"\)' server agents
rg -n '"project_settings"|"chat_sessions"|"chat_attachments"' server agents
```

Expected outcome:

- Runtime code should not contain get_collection("...") with literals.
- Direct collection-name literals should exist only in server/db.py constants
  (plus documentation files).
