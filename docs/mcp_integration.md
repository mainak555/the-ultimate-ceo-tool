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
