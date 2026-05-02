---
name: shared-utility-reuse
description: >
  Use when adding or refactoring repeated helper logic across modules.
  Enforces shared utility extraction and no duplicate helper functions.
---

# Skill: Shared Utility Reuse

## Purpose

Keep helper behavior consistent and maintainable by extracting repeated,
cross-feature helper logic into shared utility modules.

## Mandatory Rules

1. Do not duplicate pure helper logic across views/services/modules.
2. If the same helper behavior appears in two or more places, extract it.
3. Feature modules may keep thin wrappers for feature-specific messages, but
   common mechanics must call shared helpers.
4. Preserve existing behavior at call sites unless a behavior change is
   explicitly requested and documented.

## Shared Utilities vs Feature Modules

Shared utility modules should own:
- datetime generation/coercion helpers
- normalization helpers (labels, scores, common shapes)
- JSON serialization helpers
- common auth-guard mechanics used by multiple features

Feature modules should own:
- provider/integration-specific business rules
- endpoint-specific response policies
- feature-specific validation and branching

## Extraction Checklist

- [ ] Confirm helper logic is duplicated in two or more places.
- [ ] Extract one canonical helper with a clear contract/docstring.
- [ ] Replace duplicate bodies with imports/calls to canonical helper.
- [ ] Remove dead duplicate helper implementations.
- [ ] Keep naming generic for cross-feature reuse.
- [ ] Verify behavior is unchanged at all call sites.

## Verification Commands

```powershell
rg -n "def (utc_now|coerce_confidence|normalize_labels|json_default|json_dumps|_has_valid_secret|_has_valid_session_access)\(" server
rg -n "function (_esc|esc)\(" server/static/server/js
rg -n "json\.dumps\(.*default=" server
```

Expected outcome:
- Canonical helpers are defined once per responsibility.
- Feature modules import shared helpers instead of repeating helper bodies.
