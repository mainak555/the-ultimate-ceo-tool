# Standalone deployment

Single-container image. Use this for local development and single-container hosts
(Vercel, HuggingFace Spaces, fly.io single-app, etc.).

## Build

```powershell
docker build -f deployments/standalone/Dockerfile -t product-discovery:standalone .
```

## Run

```powershell
docker run --rm -p 8000:8000 --env-file .env product-discovery:standalone
```

## Required environment variables

| Var | Required | Notes |
| --- | --- | --- |
| `APP_SECRET_KEY` | yes | gates write access |
| `MONGODB_URI` | yes | external Mongo connection string |
| `MONGODB_NAME` | no | defaults to `product_discovery` |
| `REDIS_URI` | yes | external Redis used for active session run coordination |
| `REDIS_NAMESPACE` | no | defaults to `product_discovery` |
| `REDIS_RUN_LEASE_TTL_SECONDS` | no | defaults to `300` |
| `REDIS_RUN_HEARTBEAT_SECONDS` | no | defaults to `20` |
| `REDIS_CANCEL_SIGNAL_TTL_SECONDS` | no | defaults to `120` |
| `OPENAI_API_KEY` / per-provider keys | as needed | per `agent_models.json` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | optional | enables OTLP tracing |

## MCP transport notes

- **Standalone images use stdio MCP servers in-process** via the bundled Node.js
  LTS (Node 20). `npx`-based servers (e.g. `@modelcontextprotocol/server-filesystem`)
  are spawned by AutoGen as child processes inside this container.
- Streamable HTTP MCP servers (`{"transport": "http", "url": "..."}`) are also
  supported but require an external HTTP MCP server reachable from this container.
- For sidecar-based MCP deployment use [../compose/README.md](../compose/README.md)
  or [../k8s/README.md](../k8s/README.md).

## Notes

- No embedded MongoDB. Set `MONGODB_URI` to your existing Mongo cluster.
- No embedded Redis. Set `REDIS_URI` to your existing Redis instance.
- Static assets are collected at build time (`collectstatic`).
- Container exposes port `8000`.
