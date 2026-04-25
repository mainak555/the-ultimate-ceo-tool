# Export Schema Contracts

## Purpose

Every export popup provider must define and maintain a stable extracted JSON schema contract.

- Extraction prompts can vary by project.
- Export popups and backend push logic depend on predictable JSON structure.
- Schema drift must be treated as a compatibility risk.

## Mandatory Documentation Locations

For every export provider (Trello, Jira types, and future providers), maintain both:

1. A provider section in `README.md` that explains the required extraction output schema for admins.
2. A provider-specific integration doc in `docs/` that defines full contract details for developers.

## Provider Contract Checklist

Each provider documentation set must include:

1. **Export JSON Schema Contract**
   - Root object shape and required keys
   - Field-level types, required/optional status, and normalization rules
   - Constraints (for example non-empty title fields)
2. **Payload Storage Contract**
   - Exact persistence path under `discussions[].exports.<provider_key>`
   - Saved payload envelope shape (if provider uses one)
3. **Endpoint Contract**
   - Extract / load-saved / save / reference / push endpoints
   - Request and response shapes for each endpoint
4. **Validation and Fallback Behavior**
   - Behavior when extraction returns null/missing arrays
   - Behavior for unknown fields and partial failures

## Change Management Rule

Any PR that changes an export provider schema, parser, or push payload behavior must update:

1. `README.md` provider contract section
2. The corresponding provider integration doc in `docs/`

Code and documentation changes must land in the same PR.

## Current Scope

- Trello: documented in `README.md` and `docs/trello_integration.md`
- Jira Software: documented in `README.md` and `docs/jira_integration.md`