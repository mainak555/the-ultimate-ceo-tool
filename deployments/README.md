# Deployments

Three supported topologies. Pick the one that matches your hosting target.

| Folder | When to use | MCP topology |
| --- | --- | --- |
| [standalone/](standalone/README.md) | Single-container hosts (Vercel, HuggingFace Spaces, fly.io, local dev) | Node bundled in the app image; MCP servers run as stdio child processes |
| [compose/](compose/README.md) | Docker Compose / single VM | App container is Python-only; **mcp-gateway** sidecar (Node) hosts MCP servers over streamable HTTP |
| [k8s/](k8s/README.md) | Kubernetes (Helm) | Same sidecar split as compose, with full Helm chart |

## MCP transport policy

- **stdio** — supported in all topologies. Standalone runs them in-process;
  compose/k8s run them inside the mcp-gateway container.
- **streamable HTTP** — supported in all topologies; preferred for sidecar
  topologies (compose/k8s).
- **SSE** — explicitly **not supported** (deprecated upstream by MCP).
  Configurations using `transport: "sse"` will be rejected at save time.

## Mongo

The chart and compose files never package a production Mongo. Provide a
managed cluster via `MONGODB_URI`. A local-only Mongo container is available
in compose under `--profile local-mongo` for development convenience.

## Redis active-session coordination

Active chat session execution uses Redis for distributed run coordination
(single active lease per `session_id`, heartbeat renewal, cross-instance
cancel signaling). MongoDB remains the durable source for persisted
`agent_state` and discussion history.

- `REDIS_URI` is required in production deployments.
- Run start is fail-fast when Redis is unavailable.
- Compose includes a local Redis service by default for developer use.
