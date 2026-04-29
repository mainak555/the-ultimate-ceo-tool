# Docker Compose deployment

Two-container topology with an **MCP gateway sidecar** so the app container
stays Python-only and Node lives only in the gateway.

```
┌────────────┐  HTTP   ┌──────────────────┐
│   app      │ ──────▶ │   mcp-gateway    │
│ (Python)   │         │  (Node + mcp-    │
│            │         │   proxy + MCP    │
│            │         │   server pkgs)   │
└────────────┘         └──────────────────┘
       │
       ▼  (your existing Mongo cluster — or use --profile local-mongo)
┌────────────┐
│   Mongo    │
└────────────┘
```

## Quickstart

```powershell
# 1) Copy and edit the MCP server catalog
cp deployments/compose/mcp.json.example deployments/compose/mcp.json

# 2) Create your env file at the repo root
cp .env.example .env  # or author one — see required vars below

# 3) Build and run
docker compose -f deployments/compose/docker-compose.yml up --build
```

App will be on http://localhost:8000, MCP gateway on http://localhost:9000.

Redis is started by default at `redis://redis:6379/0` and is required for
active-session run coordination (distributed lease + cancel signaling).

## Multi-replica scaling — session affinity required

The app's team cache (`_TEAM_CACHE`) is process-local. If you scale the `app`
service to multiple replicas (`docker compose up --scale app=N`) you must put
a sticky-session reverse proxy in front of it so each `session_id` always
routes to the same container.

**Nginx upstream example** (add an `nginx` service to the compose file):

```nginx
upstream app_backend {
    ip_hash;  # or: hash $cookie_sessionid consistent;
    server app_1:8000;
    server app_2:8000;
}
```

Without stickiness a HITL resume request may land on a different replica,
triggering a cache miss — the team is rebuilt from MongoDB `agent_state` and
the in-progress turn counter resets.

Cross-instance **cancel** works without stickiness: the stop endpoint writes a
Redis key; the owning container polls it via `session_coordination.py` and
calls `cancel_team()` locally.

The default single-replica `docker compose up` needs no changes.

## With a local Mongo (dev only)

```powershell
docker compose -f deployments/compose/docker-compose.yml --profile local-mongo up --build
```

This adds a `mongo` service backed by a named volume and points the app at it
via `MONGODB_URI=mongodb://mongo:27017`. **Do not use the local profile in
production** — use a managed Mongo cluster instead.

## Configuring MCP servers

Edit `mcp.json` to declare any MCP servers (filesystem, fetch, custom HTTP
servers, etc.). The gateway exposes each server as a streamable HTTP endpoint
at `http://mcp-gateway:9000/<server-name>/mcp`.

To attach an MCP server to an assistant in the app:

- **Shared scope** — Project Config → Shared MCP Tools (JSON):
  ```json
  {
    "mcpServers": {
      "fs": { "transport": "http", "url": "http://mcp-gateway:9000/filesystem/mcp" }
    }
  }
  ```
- **Dedicated scope** — Per-agent textarea, same shape.

## Required environment variables

See [../standalone/README.md](../standalone/README.md) for the canonical list
of app env vars. All apply identically here, including the Redis variables
used by active session coordination.
