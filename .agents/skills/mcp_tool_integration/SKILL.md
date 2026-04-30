---
name: mcp-tool-integration
description: Use when adding, changing, or reviewing MCP (Model Context Protocol) tool wiring — per-agent `mcp_tools` scope, `mcp_configuration`, project-level `shared_mcp_tools`, new transports, `agents/mcp_tools.py` runtime wiring, or new deployment topologies under `deployments/`. Enforces validation, layering (`server/` never imports AutoGen MCP), redaction (no `args`/`env`/`headers` in logs/spans), lifecycle cleanup via `evict_team()`, and SSE rejection.
---

# MCP Tool Integration Skill

Use this skill when adding, changing, or reviewing anything that touches
MCP (Model Context Protocol) tool wiring for assistant agents.

## When this skill applies

- Adding or modifying the per-agent `mcp_tools` scope or `mcp_configuration`.
- Changing project-level `shared_mcp_tools`.
- Adding a new MCP transport (stdio / streamable HTTP).
- Editing `agents/mcp_tools.py`, `agents/team_builder.py`, or
  `agents/runtime.py` MCP integration points.
- Adding a new deployment topology under `deployments/`.

## Mandatory contracts

### Data model

- Per-agent: `mcp_tools` ∈ {`none`, `shared`, `dedicated`} **and**
  `mcp_configuration` (dict; `{}` when not dedicated).
- Project-level: `shared_mcp_tools` (dict; `{}` when no shared servers).
- Top-level shape of any MCP config: `{"mcpServers": {<name>: <entry>}}`.

### Validation

- All validation lives in `server/schemas.py`.
- `dedicated` agents with empty `mcp_configuration` → `ValueError`.
- Any `shared` agent with empty `shared_mcp_tools` → `ValueError`.
- `transport: "sse"` is rejected with an explicit deprecation message.
- Server entries must have either `command` (stdio) or `url` (HTTP).

### Runtime wiring

- All workbench construction goes through `agents/mcp_tools.py`.
- `team_builder.py` calls `resolve_mcp_servers_for_agent()` +
  `build_mcp_workbenches()` only — never instantiates `McpWorkbench` directly.
- Workbenches are passed to `AssistantAgent(workbench=..., reflect_on_tool_use=True)`.
  **`reflect_on_tool_use=True` is mandatory** for every agent that receives an MCP
  workbench. Without it, AutoGen omits the second LLM call after tool execution and
  yields a raw `ToolCallSummaryMessage` instead of a synthesised `TextMessage` —
  the result is invisible in the SSE chat and produces no `LLMCallEvent` span.
- `agents/runtime.py::evict_team()` MUST call
  `close_session_workbenches(session_id)`.
- Active run exclusivity/cancel across multiple workers is coordinated by
  `agents/session_coordination.py` (Redis lease + heartbeat + cancel signal).
  Redis stores only ephemeral coordination state; MongoDB remains durable for
  `chat_sessions.agent_state` resume checkpoints.
- Run start paths must be fail-fast when Redis is unavailable (do not silently
  degrade to in-process-only coordination).

### Layering

- `server/` may **never** import from `autogen_ext.tools.mcp`. All AutoGen
  imports live under `agents/`.
- The frontend may not parse or transform MCP JSON beyond syntax validation
  in `project_config.js`.

### Observability (see [docs/observability.md](../../docs/observability.md))

- Logger: `agents.mcp_tools` (`logging.getLogger(__name__)`).
- Event names: `agents.mcp.created`, `agents.mcp.closed`, `agents.mcp.failed`.
- **Never** log `args`, `env`, `headers`, or full `url`. Allowed payload
  fields: `scope`, `server_count`, `server_names`, `fingerprint`,
  `session_id`, `phase`.
- All payloads carried into spans must go through `set_payload_attribute()`
  + `redact_payload()`.

### Deployment

- Standalone (`deployments/standalone/`) bundles Node in the app image.
- Compose / K8s use a Node-based mcp-gateway sidecar exposing servers over
  streamable HTTP.
- Any new deployment target must follow the same split.

### Documentation parity

- Schema or validation changes → update [docs/mcp_integration.md](../../docs/mcp_integration.md).
- New transport → update both `_validate_mcp_server_entry()` and
  `_build_server_params()`.
- Deployment changes → update `deployments/README.md` and the relevant
  per-topology README.

### Model capability prerequisite (`function_calling`)

- Any agent with `mcp_tools` ∈ {`shared`, `dedicated`} forwards tools to the
  model client. The model's resolved `model_info.function_calling` MUST be
  `true`, otherwise AutoGen raises
  `ValueError: Model does not support function calling`.
- For Azure OpenAI, Azure Anthropic, and Google Gemini providers,
  `model_info` is **always** injected by `agents/factory.py`, so the
  `agent_models.json` entry MUST declare `"function_calling": true`
  explicitly. The factory default is `false`.
- For direct `openai` / `anthropic` providers, AutoGen auto-detects known
  model names; declare `model_info` explicitly only for unrecognized model
  identifiers.
- When introducing a new model that should be MCP-capable, update both
  [`agent_models.json`](../../agent_models.json) and the README "Models &
  `model_info`" section if a new family/provider example is added.
- Models that genuinely lack tool calling (reasoning-only, audio, embedding)
  must be paired only with agents whose `mcp_tools = "none"`. Never set
  `function_calling: true` on a model that cannot honor it.

## Anti-patterns (block in review)

- Using `print()` or logging server entries directly.
- Spawning `McpWorkbench` outside `agents/mcp_tools.py`.
- Adding SSE support back without explicit upstream re-deprecation reversal.
- Storing MCP credentials in plaintext code (use env vars or secret managers).
- Embedding raw secrets directly in `shared_mcp_tools` / `mcp_configuration`
  instead of `mcp_secrets` + `{KEY_NAME}` placeholders.
- Substituting `mcp_secrets` anywhere outside `agents/mcp_tools.py`.
- Computing the OTel `fingerprint` over the substituted (post-secret) servers
  dict — must always be over the placeholder dict.
- Logging or rendering secret values in any template or log line.
- Skipping `evict_team()` cleanup.

## Secrets contract

Project-level field `mcp_secrets: {KEY: value}` is the **only** approved
location for credential material referenced by MCP servers. Rules:

1. **Schema** (`server/schemas.py::validate_mcp_secrets`):
   - keys match `^[A-Z][A-Z0-9_]*$` (UPPER_SNAKE),
   - values are non-empty strings,
   - no duplicates,
   - every `{KEY}` placeholder referenced by `shared_mcp_tools` or any agent's
     `mcp_configuration` must resolve to a defined key.
2. **Round-trip** (`server/services.py`):
   - `normalize_project()` returns `{KEY: SECRET_MASK}` for the UI;
   - `_restore_masked_secrets()` restores `SECRET_MASK` to the DB value on
     save; missing keys are dropped (user deletion).
3. **UI** (`config_form.html` + `project_config.js`): follow
   [`.agents/skills/key_value_form_pattern/SKILL.md`](../key_value_form_pattern/SKILL.md);
   readonly view (`config_readonly.html`) MUST NOT render values.
4. **Runtime** (`agents/mcp_tools.py::_substitute_secrets`): substitute strings
   recursively across `command`, `args`, `env` values, `url`, `headers`
   values, immediately before `McpWorkbench` construction.
5. **Tracing**: fingerprint hashes the placeholder `mcpServers` dict, NOT the
   substituted dict, so spans remain stable across secret rotation.

## OAuth 2.0 contract (HTTP MCP servers)

`mcp_oauth_configs` is a project-level dict storing app registrations
(`auth_url`, `token_url`, `client_id`, `client_secret`, `scopes?`) keyed by
`server_name`. Keys must cross-validate against actual `mcpServers` keys
(shared + dedicated) at save time — orphan keys raise `ValueError`.
`client_secret` follows the SECRET_MASK round-trip and must never be sent
back to the browser after the first save.

### Endpoints (single generic start handler)

| Method | Path | Purpose |
|---|---|---|
| GET | `/mcp/oauth/start/?flow=test\|run&server_name=...&project_id=...&[session_id=...]&skey=...` | Single entry point for both flows. Generates PKCE (S256, `secrets.token_urlsafe(64)` verifier), persists state metadata to Redis (300 s TTL), 302-redirects to `auth_url`. |
| GET | `/mcp/oauth/callback/` | Provider redirect-back. Atomic `getdel` on `state`, exchanges code at `token_url`, branches on stored `flow`, renders shared `server/templates/server/oauth_flow.html`. |
| GET | `/mcp/oauth/check/<session_id>/` | JSON status of all OAuth servers required for the session. Header secret only. |

Do NOT add per-flow endpoints (`/oauth/test/`, `/oauth/authorize/`, etc.).
The single handler discriminated by `?flow=` is the blessed pattern.

### Popup secret-key rule

`/mcp/oauth/start/` is reachable from `window.open()`, which cannot set
request headers. `_has_valid_oauth_secret(request)` accepts the secret as
either `X-App-Secret-Key` header or `?skey=` query param. **Every other
MCP endpoint (including `/check/`) remains header-only.** Do not extend the
query-param fallback to non-popup endpoints.

### Outcome rendering

Only one template is permitted for OAuth popup outcomes:
`server/templates/server/oauth_flow.html`. It is rendered by
`_render_outcome()` and `_render_error()` in `server/mcp_views.py`.
Auto-close defaults: 2 s on `flow=run` success, 5 s on `flow=test` success,
**30 s on error** (so users can read provider error messages). Never
inline popup HTML inside views — always go through the helpers.

### Run-time injection

- Session-scoped token Redis key: `{ns}:mcp_oauth:{session_id}:{server_name}:token`.
- Test status (config-form-only) Redis key: `{ns}:mcp_oauth_test:{project_id}:{server_name}:status` (300 s TTL). The test flow MUST NOT write to the session token key.
- TTL derivation order: JWT `exp` (no signature verify) → `expires_in` → env default `MCP_OAUTH_TOKEN_TTL_MAX_SECONDS` (default 43200 s = 12 h). The resolved TTL is **capped** at the same env value via `min()` in `set_mcp_oauth_token()` so long-lived provider tokens cannot pin the Redis entry beyond the configured ceiling.
- `agents/mcp_tools.py::_build_server_params(name, entry, session_id, has_oauth)` injects `Authorization: Bearer <token>` into the streamable-HTTP `headers` dict. Missing token → `ValueError` (surfaced as a run error).
- No mid-session refresh (v1 limitation); a 401 mid-run requires re-authorize on the next run.
- `purge_mcp_oauth_tokens(session_id)` runs on chat-session delete (SCAN-based).

### Logging contract (server/mcp_views.py)

Logger: `logging.getLogger(__name__)`. Required event names — every branch
MUST be observable from server logs alone:

- `agents.mcp.oauth_start`
- `agents.mcp.oauth_callback_received`
- `agents.mcp.oauth_callback_provider_error`
- `agents.mcp.oauth_callback_state_missing`
- `agents.mcp.oauth_callback_state_recovered`
- `agents.mcp.oauth_token_exchange_start`
- `agents.mcp.oauth_token_exchange_network_error`
- `agents.mcp.oauth_token_exchange_http_error` (must include `status_code` and a `body_snippet` truncated to 500 chars)
- `agents.mcp.oauth_token_exchange_ok`
- `agents.mcp.oauth_token_missing`
- `agents.mcp.oauth_test_authorized` / `agents.mcp.oauth_authorized`

**Forbidden in logs and span attributes**: `code`, `code_verifier`,
`client_secret`, `access_token`, raw `?skey=` value, full `Authorization`
header. Allowed: `server_name`, `project_id`, `session_id`, `flow`,
`token_url`, `state_prefix` (first 8 chars), `ttl_seconds`, `token_type`,
`status_code`, provider `error` / `error_description`, response body
snippet (≤ 500 chars, via `set_payload_attribute()`).

### Tracing contract (core/tracing.py)

Three nested spans per round-trip:

- `mcp.oauth.start` — attrs: `mcp.oauth.flow`, `server_name`, `project_id`,
  `session_id`, `state_prefix`.
- `mcp.oauth.callback` — attrs: `has_code`, `has_state`, `provider_error`,
  `flow`, `server_name`, `state_recovered`.
  - child `mcp.oauth.token_exchange` — attrs: `token_url`, `server_name`,
    `flow`, `http.status_code`, `mcp.oauth.token_ttl_seconds` (success).
    Non-2xx body snippets go through `set_payload_attribute(span, "output.value", snippet)`.

Outbound POST to `token_url` is also captured by `OTEL_INSTRUMENT_HTTP` as a
sibling span in the same trace.

### OAuth anti-patterns (block in review)

- Adding `/mcp/oauth/test/`, `/mcp/oauth/authorize/`, or any per-flow start route.
- Inline popup HTML in `mcp_views.py` (e.g. `_popup_html`, `_error_popup_html`) — must use `oauth_flow.html`.
- Reading the secret from `?skey=` on any endpoint other than `/mcp/oauth/start/`.
- Logging or span-attaching `code`, `code_verifier`, `client_secret`, `access_token`, or raw `Authorization` header.
- Writing the test flow's success to the session token Redis key.
- Auto-close timer < 30 s on the error branch of `oauth_flow.html`.
- Computing the OTel `fingerprint` after secret substitution (must hash the placeholder dict).

