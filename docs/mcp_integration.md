# MCP (Model Context Protocol) Integration

This document describes how MCP tools are configured per-project and per-agent,
how they are wired into AutoGen at runtime, and how they are deployed across
the supported topologies.

## Concepts

- **MCP server** — a process or HTTP endpoint exposing tools to LLM agents,
  per the [Model Context Protocol](https://modelcontextprotocol.io/) spec.
- **Workbench** — AutoGen's wrapper (`autogen_ext.tools.mcp.McpWorkbench`)
  that connects to one MCP server and surfaces its tools to an `AssistantAgent`.
- **Scope** — a per-agent enum that decides which workbench(es) to attach:
  - `none` — no MCP tools.
  - `shared` — use the project-level `shared_mcp_tools` configuration.
  - `dedicated` — use the per-agent `mcp_configuration`.

## Data model

Stored in MongoDB on the project document:

```jsonc
{
  "name": "...",
  "agents": [
    {
      "name": "Researcher",
      "model": "gpt-4o",
      "temperature": 0.4,
      "system_prompt": "...",
      "mcp_tools": "shared",            // "none" | "shared" | "dedicated"
      "mcp_configuration": {}           // {} unless mcp_tools == "dedicated"
    }
  ],
  "shared_mcp_tools": {
    "mcpServers": {
      "fs": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"] }
    }
  }
}
```

## JSON schema

Both `mcp_configuration` (per-agent, dedicated scope) and `shared_mcp_tools`
(project-level) use the same shape:

```jsonc
{
  "mcpServers": {
    "<server-name>": <server-entry>,
    ...
  }
}
```

Each `<server-entry>` is one of:

### Stdio (default)

```jsonc
{
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
  "env": { "OPTIONAL_VAR": "value" }
}
```

#### Stdio command resolution & troubleshooting

`agents/mcp_tools.py::_resolve_stdio_command()` preflights the configured
`command` before constructing the workbench, so a missing runtime fails fast
with a readable `ValueError` (event `agents.mcp.workbench_built` ends in
ERROR with description `MCP server '<name>' stdio command …`) instead of a
deep MCP/asyncio `WinError 2` traceback.

Resolution rules:

- If `command` looks like a path (absolute, or contains `/` or `\`) it must
  exist on disk as written (after `expandvars` / `expanduser`).
- Otherwise `command` is treated as a bare executable name and resolved via
  `shutil.which(command, path=env["PATH"])`.

When the resolver rejects an entry, fix the project's `shared_mcp_tools` (or
the agent's `mcp_configuration`) using one of the patterns below. The
[`tavily-remote-mcp`](https://www.npmjs.com/package/tavily-remote-mcp)
server is used as the example:

**Option A — bare command (preferred, portable across hosts):**

```jsonc
{
  "mcpServers": {
    "tavily-remote-mcp": {
      "command": "npx",
      "args": ["-y", "tavily-remote-mcp"],
      "env": { "TAVILY_API_KEY": "{TAVILY_API_KEY}" }
    }
  }
}
```

Requires Node.js on `PATH` inside the process that runs the agent worker
(standalone deployment bundles it; compose/k8s rely on the `mcp-gateway`
sidecar instead — see [Streamable HTTP](#streamable-http) below).

**Option B — explicit absolute path that actually exists on the host:**

```jsonc
{
  "mcpServers": {
    "tavily-remote-mcp": {
      "command": "C:\\Program Files\\nodejs\\npx.cmd",
      "args": ["-y", "tavily-remote-mcp"],
      "env": { "TAVILY_API_KEY": "{TAVILY_API_KEY}" }
    }
  }
}
```

Verify with `Test-Path "C:\Program Files\nodejs\npx.cmd"` (Windows) or
`test -x /usr/local/bin/npx` (POSIX) before saving.

**Option C — switch to streamable HTTP (no local stdio runtime needed):**

```jsonc
{
  "mcpServers": {
    "tavily-remote-mcp": {
      "transport": "http",
      "url": "https://mcp.tavily.com/mcp/?tavilyApiKey={TAVILY_API_KEY}",
      "headers": {}
    }
  }
}
```

In all three cases the credential lives in the project-level `mcp_secrets`
dict (`{"TAVILY_API_KEY": "tvly-…"}`) and is referenced via the
`{TAVILY_API_KEY}` placeholder — never inlined as a raw value (see
[Secrets management](#secrets-management-mcp_secrets)).

### Streamable HTTP

```jsonc
{
  "transport": "http",
  "url": "http://mcp-gateway:9000/filesystem/mcp",
  "headers": { "Authorization": "Bearer ..." }
}
```

### SSE — explicitly rejected

`{"transport": "sse", ...}` is rejected at save time. SSE has been deprecated
upstream by the MCP project. Use `transport: "http"` (Streamable HTTP) instead.

## Validation rules (`server/schemas.py`)

- `mcp_tools` must be one of `none` / `shared` / `dedicated`.
- `mcp_configuration` non-empty is required when `mcp_tools == "dedicated"`.
- Whole-project save fails when **any** agent has `mcp_tools == "shared"`
  but `shared_mcp_tools` is empty.
- Each server entry must have either `command` (stdio) or `url` (HTTP).
- `transport: "sse"` raises with an explicit deprecation message.

## Runtime wiring

Active session run exclusivity/cancellation is coordinated by
`agents/session_coordination.py` (Redis lease + heartbeat + cancel signal).
This coordination layer is independent of MCP server wiring: Redis tracks only
ephemeral run ownership, while durable conversation state and `agent_state`
resume checkpoints remain in MongoDB.

Order of operations in `agents/team_builder.py::build_team()`:

1. For each agent, `build_agent_runtime_spec(agent_cfg, project, objective)`
   calls `resolve_mcp_servers_for_agent()` to compute the effective
   `mcpServers` dict based on scope.
2. `build_mcp_workbenches()` constructs one `McpWorkbench` per server entry
   (mapping to `StdioServerParams` or `StreamableHttpServerParams`).
3. The workbenches are passed to `AssistantAgent(workbench=...)` (single
   workbench passed directly, multiple as a list).
4. The accumulated workbenches are stashed under `project["_runtime"]
   ["mcp_workbenches"]` so the runtime cache can register them.
5. `agents/runtime.py::get_or_create_team()` calls
   `register_session_workbenches(session_id, workbenches)`.
6. `evict_team(session_id)` calls `close_session_workbenches(session_id)`,
   which awaits `wb.stop()` on each workbench.

**Thread-pool safety of `close_session_workbenches`**: eviction may be called
from a Django thread-pool thread (e.g. `ThreadPoolExecutor`) where there is no
running event loop. The function uses `asyncio.get_running_loop()` (not the
deprecated `get_event_loop()`) to detect the async context:
- If a loop is running → `loop.create_task(_stop_all())` (fire-and-forget).
- If no loop is running (thread context) → `asyncio.run(_stop_all())`.

**Never-started workbenches**: a workbench that was constructed but whose
`start()` was never called (e.g. an agent that was evicted before its first
run) raises `TypeError` when awaited inside `stop()`. This is treated as a
silent no-op, not an error.

## Observability

- Logger: `agents.mcp_tools`.
- Events:
  - `agents.mcp.created` — INFO; payload: `scope`, `server_count`,
    `server_names`, `fingerprint`. Never includes `args`/`env`/`headers`.
  - `agents.mcp.closed` — INFO; payload: `session_id`, `workbench_count`.
  - `agents.mcp.failed` — EXCEPTION; payload: `session_id`, `phase`.
- Tracing: `build_mcp_workbenches()` is decorated with
  `@traced_function("agents.mcp.workbench_built")`. Span attributes follow the
  same redaction rule (server names + fingerprint only).
- **Per-tool-call spans**: `autogen_ext.tools.mcp.McpWorkbench.call_tool()`
  emits an OpenTelemetry `execute_tool <tool_name>` span per invocation,
  using the GenAI semantic conventions:
  `gen_ai.operation.name=execute_tool`, `gen_ai.system=autogen`,
  `gen_ai.tool.name`, `gen_ai.tool.call.id`,
  `gen_ai.tool.description` (when available). Exceptions are recorded with
  `span.record_exception()` and `ERROR` status.
  These spans are produced by AutoGen via `trace.get_tracer("autogen-core")`
  and automatically picked up by our global TracerProvider, so they nest as
  children of the calling agent's run span and ship to the OTLP backend
  alongside Django/HTTP/LLM spans in the same trace. The toggle is
  `OTEL_INSTRUMENT_AGENTS` (default on).

## Security

- `command`, `args`, `env`, and `headers` are **secret material by policy**:
  they may carry API keys, file paths, or credentials. Never log them and
  never set them as raw span attributes.
- Stdio MCP servers execute arbitrary commands inside the worker container.
  Only configure servers you trust.
- Streamable HTTP MCP servers should be reached over the cluster-internal
  network when possible (in compose/k8s, use the `mcp-gateway` service name).

## Secrets management (`mcp_secrets`)

Credential material referenced by MCP servers MUST live in the project-level
`mcp_secrets` dict and be injected into `mcpServers` entries via `{KEY_NAME}`
placeholders. The raw values never appear in `shared_mcp_tools` or per-agent
`mcp_configuration` JSON.

### Schema

```json
{
  "mcp_secrets": {
    "GITHUB_PAT": "ghp_xxxxxxxxxxxx",
    "DB_PASSWORD": "s3cret"
  },
  "shared_mcp_tools": {
    "mcpServers": {
      "github": {
        "transport": "http",
        "url": "https://mcp.example.com/github",
        "headers": { "Authorization": "Bearer {GITHUB_PAT}" }
      },
      "postgres": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres"],
        "env": { "PGPASSWORD": "{DB_PASSWORD}" }
      }
    }
  }
}
```

### Validation rules

- `mcp_secrets` keys match `^[A-Z][A-Z0-9_]*$` (UPPER_SNAKE).
- Values are non-empty strings.
- Every `{KEY}` placeholder used inside `shared_mcp_tools` or any agent's
  `mcp_configuration` must have a matching entry in `mcp_secrets` —
  `validate_project()` raises `ValueError` otherwise.

### Round-trip masking

- `normalize_project()` replaces every secret value with `SECRET_MASK`
  (`••••••••`) so the edit form re-renders password inputs without leaking
  values.
- `_restore_masked_secrets()` swaps `SECRET_MASK` back to the existing DB
  value on save. Keys absent from the submitted payload are treated as
  user deletions and dropped.
- The readonly view (`config_readonly.html`) MUST NOT render secret values —
  only an optional `🔒 N secrets configured` count badge listing key names.

### Runtime substitution

`agents/mcp_tools.py::_substitute_secrets()` recursively walks each server
entry just before constructing `McpWorkbench` and substitutes every
`{KEY_NAME}` occurrence in any string scalar (`command`, `args` items, `env`
values, `url`, `headers` values). Substitution lives **only** in
`agents/mcp_tools.py`; `server/` code never sees substituted values.

### Tracing fingerprint

`build_mcp_workbenches()` computes the `fingerprint` span attribute over the
**placeholder** `mcpServers` dict (pre-substitution), so the value remains
stable across secret rotations and never carries credential material.

## Deployment topologies

See [../deployments/README.md](../deployments/README.md) for the topology
matrix. Summary:

| Topology | App image | MCP location |
| --- | --- | --- |
| Standalone | Python + Node | stdio MCP servers in-process |
| Compose | Python only | mcp-gateway sidecar (Node, streamable HTTP) |
| K8s (Helm) | Python only | mcp-gateway Deployment (Node, streamable HTTP) |

When using the sidecar topology, agents reference servers via:

```jsonc
{
  "mcpServers": {
    "fs": { "transport": "http", "url": "http://mcp-gateway:9000/<server-name>/mcp" }
  }
}
```

## Adding a new MCP transport

1. Update `_validate_mcp_server_entry()` in `server/schemas.py`.
2. Update `_build_server_params()` in `agents/mcp_tools.py`.
3. Update this doc + [`.agents/skills/mcp_tool_integration/SKILL.md`](../.agents/skills/mcp_tool_integration/SKILL.md).
4. Document any deployment implications in `deployments/README.md`.

---

## OAuth 2.0 Authorization for HTTP MCP Servers

Some HTTP MCP servers require a Bearer token obtained via OAuth 2.0 (Authorization Code + PKCE).  
The project-level `mcp_oauth_configs` dict stores app registration details, and a pre-run gate
ensures tokens are present in Redis before the agent team is built.

### Data model

```json
{
  "mcp_oauth_configs": {
    "<server_name>": {
      "auth_url":      "https://provider.example.com/oauth/authorize",
      "token_url":     "https://provider.example.com/oauth/token",
      "client_id":     "app-client-id",
      "client_secret": "••••••••",
      "scopes":        "read write"
    }
  }
}
```

- `server_name` must match a key in `mcpServers` (shared or dedicated config). Orphan keys raise `ValueError` at save time.
- `client_secret` uses the SECRET_MASK round-trip — stored masked in normalized docs, restored from DB on save, never sent to the browser after first save.
- `auth_url` is the provider's authorization endpoint (user consent page). `token_url` is the provider's token endpoint (server-to-server POST). These are always two different URLs.
- `scopes` is optional; space-separated.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/mcp/oauth/start/` | Start OAuth flow — generates PKCE pair, stores state, redirects to `auth_url`. Discriminated by `?flow=test` (Project Config Test button) or `?flow=run` (pre-run authorize) |
| GET | `/mcp/oauth/callback/` | Provider callback — exchanges code for token, renders shared `oauth_flow.html` outcome page (postMessage + auto-close) |
| GET | `/mcp/oauth/check/<session_id>/` | Pre-run check — returns authorization status for all OAuth servers |

### PKCE flow sequence

```
User browser (popup)            Backend                      OAuth provider
      |                            |                               |
      |-- GET /mcp/oauth/start/?flow=run&server_name=...&session_id=...&skey=... -->|
      |                            | generate code_verifier, code_challenge    |
      |                            | store {state → metadata} in Redis (300s)  |
      |<-- 302 redirect to auth_url?response_type=code&...&code_challenge=... -|
      |------- user grants consent ----------------------------------------->|
      |<-- 302 redirect to /mcp/oauth/callback/?code=...&state=... -----------|
      |-- GET /mcp/oauth/callback/?code=...&state=... --->|
      |                            | get_and_delete_mcp_oauth_state(state)     |
      |                            | POST token_url code + code_verifier       |
      |                            |----------- exchange code for token ------>|
      |                            |<----------- {access_token, expires_in} ---|
      |                            | set_mcp_oauth_token(session_id, ...)      |
      |<-- popup page: postMessage({type:"mcp_oauth_done"}) + window.close() -|
```

### Callback URL registration

You must register the exact callback URL with your OAuth provider before using this feature:

```
{BASE_URL}/mcp/oauth/callback/
```

Example: `https://your-domain.com/mcp/oauth/callback/`

The config form shows the computed redirect URI in the section hint.

### Token TTL derivation

The token endpoint response is a JWT. The backend decodes the payload (no signature
verification — the token endpoint is TLS-protected) and reads the `exp` UTC epoch claim:

```
TTL = exp − now(UTC)
```

The Redis key is set with this exact TTL, so it expires precisely when the Bearer
token does. Cache hits during an active session are always valid without any
additional checks.

If the JWT cannot be decoded or `exp` is absent, the TTL falls back to a hardcoded
**3 h** (`_MCP_OAUTH_DEFAULT_TTL`). No external env-var cap is applied.

Tokens are stored in Redis with the derived TTL. There is **no mid-session refresh** (v1 limitation). If a token expires during a run, MCP calls return 401. The user must re-authorize for the next run.

### Test Authorization mode

The config form "Test Authorization" button opens the OAuth flow with `?flow=test` and a `project_id` scope (no `session_id`). On success a short-lived (600 s) status flag is written to Redis. This validates app credentials without starting a run; **no run-time session token is injected**.

### Popup secret-key handoff

`/mcp/oauth/start/` is reachable from a `window.open()` popup, which cannot set custom request headers. The endpoint therefore accepts the admin secret either as the `X-App-Secret-Key` header **or** as a `?skey=<APP_SECRET_KEY>` query parameter (`_has_valid_oauth_secret()` in `server/mcp_views.py`). All other MCP endpoints remain header-only. Because `?skey=` lands in browser history and may appear in HTTP server access logs, deployments must:

- terminate TLS in front of the app,
- scrub query strings from access logs (or accept the leak as in-scope for an admin-only deployment),
- never share an OAuth start URL outside the operator's own browser session.

### Outcome page

Both success and error branches of `/mcp/oauth/callback/` render the shared `server/templates/server/oauth_flow.html` popup. The template:

- posts `{type, flow, server_name, status, message}` to `window.opener` so the parent page (config form or pre-run modal) can react immediately,
- shows a countdown and a manual **Close** button,
- auto-closes after 2 s on `flow=run` success, 5 s on `flow=test` success, and **30 s on error** (long enough to read the provider's error message before the window disappears).

### Pre-run gate (frontend)

`window.McpOAuth.checkAndAuthorize(sessionId, projectId, secretKey)` is called by `home.js` before every run POST:

1. GET `/mcp/oauth/check/<sessionId>/` — check all OAuth server statuses.
2. If `all_authorized: true` → no-op, run proceeds.
3. Else → show authorization modal with per-server "Authorize" buttons.
4. postMessage from popup → mark server authorized in modal.
5. Poll `/mcp/oauth/check/` every 3 s as fallback.
6. When all authorized → resolve → run POST fires.
7. User clicks Cancel → reject → run is aborted.

### Runtime injection

`agents/mcp_tools.py::_build_server_params()` merges `Authorization: Bearer <token>` into the `StreamableHttpServerParams.headers` dict when `has_oauth=True` and `session_id` is provided. A missing token raises `ValueError` (surfaced as a run error).

### Redis key scheme

| Purpose | Key pattern |
|---------|-------------|
| Session-scoped run token | `{ns}:mcp_oauth:run:{session_id}:{server_name}:token` |
| PKCE state (one-time) | `{ns}:mcp_oauth_state:{state}:meta` (300 s TTL) |
| Test authorization result | `{ns}:mcp_oauth:test:{project_id}:{server_name}:status` (600 s TTL) |

### Session cleanup

`purge_mcp_oauth_tokens(session_id)` is called when a chat session is deleted. It uses SCAN to remove all `{ns}:mcp_oauth:run:{session_id}:*:token` keys.

### Observability — OAuth flow

Logger: `server.mcp_views`. Every branch of the start + callback handlers emits a structured event so opaque popup-window failures are diagnosable from the server console alone. Secrets (`code`, `code_verifier`, `client_secret`, `access_token`, `?skey=` value) are **never** logged.

| Event | Level | When |
|---|---|---|
| `agents.mcp.oauth_start` | INFO | Start handler entered with valid params; PKCE state written to Redis. |
| `agents.mcp.oauth_callback_received` | INFO | Callback entered; logs `has_code`, `has_state`, `state_prefix` (first 8 chars), provider `error` / `error_description`. |
| `agents.mcp.oauth_callback_provider_error` | WARN | Provider returned an `error=` query param (consent denied, invalid scope, etc.). |
| `agents.mcp.oauth_callback_state_missing` | WARN | Redis miss on `state` — TTL expired or already consumed. Most common silent failure. |
| `agents.mcp.oauth_callback_state_recovered` | INFO | Redis returned PKCE metadata; logs `flow`, `server_name`, `project_id`, `session_id`. |
| `agents.mcp.oauth_token_exchange_start` | INFO | About to POST `token_url`. |
| `agents.mcp.oauth_token_exchange_network_error` | EXCEPTION | `requests.RequestException` (DNS, timeout, TLS). |
| `agents.mcp.oauth_token_exchange_http_error` | WARN | Non-2xx from token endpoint; logs `status_code`, provider `error` / `error_description`, and a 500-char body snippet. |
| `agents.mcp.oauth_token_exchange_ok` | INFO | 2xx with `access_token`; logs `ttl_seconds`, `token_type`. |
| `agents.mcp.oauth_token_missing` | WARN | 2xx but no `access_token` field; logs `response_keys`. |
| `agents.mcp.oauth_test_authorized` | INFO | `flow=test` success — Redis status flag written. |
| `agents.mcp.oauth_authorized` | INFO | `flow=run` success — session token written; logs `ttl_seconds`. |

Tracing: three nested OTel spans per OAuth round-trip:

```
mcp.oauth.start                       [start handler]
  └─ HTTP GET /mcp/oauth/start/        [auto · OTEL_INSTRUMENT_HTTP]

mcp.oauth.callback                    [callback handler]
  ├─ attrs: flow, server_name, has_code, has_state, state_recovered
  └─ mcp.oauth.token_exchange         [child span around requests.post]
     ├─ attrs: token_url, server_name, flow, http.status_code,
     │         mcp.oauth.token_ttl_seconds (on success)
     └─ output.value                  [500-char body snippet on non-2xx,
                                       redacted via set_payload_attribute]
```

Spans are emitted via `core/tracing.py::traced_block()` and follow the standard redaction + truncation contract. The token-exchange POST itself is also picked up by `OTEL_INSTRUMENT_HTTP` as a sibling outbound HTTP span in the same trace.

