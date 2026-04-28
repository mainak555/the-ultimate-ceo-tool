# Kubernetes deployment (Helm)

Production-grade Helm chart for product-discovery, optionally with the
**mcp-gateway** sidecar Deployment that hosts MCP servers behind streamable HTTP.

## Layout

```
helm/product-discovery/
  Chart.yaml
  values.yaml              # defaults
  values.example.yaml      # ready-to-edit example
  templates/
    _helpers.tpl
    configmap.yaml         # env + mcp.json
    secret.yaml            # secret env (only if existingSecret unset)
    deployment-app.yaml
    deployment-mcp-gateway.yaml   # rendered when mcpGateway.enabled
    service.yaml
    ingress.yaml
    NOTES.txt
```

## Install

```powershell
helm install discovery deployments/k8s/helm/product-discovery `
    --namespace discovery --create-namespace `
    --values deployments/k8s/helm/product-discovery/values.example.yaml
```

## Required setup before install

- **Mongo**: this chart never installs Mongo. Provide a connection string via
  `secretEnv.MONGODB_URI` (rendered into a Secret) or via `existingSecret`.
- **Redis**: this chart does not install Redis. Provide `secretEnv.REDIS_URI`
  (managed Redis recommended) because active run coordination is fail-fast
  when Redis is unavailable.
- **Container images**: build and push:
  - app image (`deployments/compose/Dockerfile.app` or
    `deployments/standalone/Dockerfile`) → `image.repository:tag`
  - mcp-gateway image (`deployments/compose/Dockerfile.mcp-gateway`) →
    `mcpGateway.image.repository:tag`

## Disabling the MCP sidecar

Set `mcpGateway.enabled=false`. In that case all MCP usage must be via
`StdioServerParams` running inside the app container (which would then
require a Node-enabled app image — use the standalone Dockerfile for that).

## Reaching the MCP gateway from the app

In Project Config → Shared MCP Tools:

```json
{
  "mcpServers": {
    "fs": {
      "transport": "http",
      "url": "http://discovery-product-discovery-mcp-gateway:9000/filesystem/mcp"
    }
  }
}
```

The exact service name is printed in `NOTES.txt` after install.

## Secret management

Prefer `existingSecret` with values supplied by external secret managers
(SealedSecrets, External Secrets, Vault, etc.) over inline `secretEnv` in
`values.yaml`.

## Horizontal scaling — session affinity is mandatory

`_TEAM_CACHE` in `agents/runtime.py` is process-local. When `replicaCount > 1`
every SSE stream request and HITL resume POST for a given `session_id` must
land on the same pod. Without stickiness a resume hits a different pod,
rebuilds the team from scratch, and loses the in-progress turn counter.

### Option A — cookie-based affinity on nginx-ingress (recommended)

Add these annotations to your Ingress resource (via `values.yaml` ingress.annotations):

```yaml
ingress:
  enabled: true
  annotations:
    nginx.ingress.kubernetes.io/affinity: "cookie"
    nginx.ingress.kubernetes.io/session-cookie-name: "SERVERID"
    nginx.ingress.kubernetes.io/session-cookie-expires: "172800"
    nginx.ingress.kubernetes.io/session-cookie-max-age: "172800"
    nginx.ingress.kubernetes.io/session-cookie-samesite: "Lax"
```

### Option B — ClientIP affinity on the Service (coarser, NAT-hostile)

```yaml
# In templates/service.yaml or via values override
service:
  sessionAffinity: ClientIP
  sessionAffinityConfig:
    clientIP:
      timeoutSeconds: 86400
```

### Cross-instance cancel (works without stickiness)

The stop endpoint writes a Redis cancel key. The owning pod's heartbeat loop
polls this key via `session_coordination.py` and calls `cancel_team()` locally,
so cancel signals propagate cross-pod correctly regardless of routing.

### Crash recovery

If the owning pod dies the Redis lease expires after
`REDIS_RUN_LEASE_TTL_SECONDS`. The next request causes a cache miss;
`get_or_build_team()` rebuilds the team and `load_state()` restores context
from the MongoDB `chat_sessions.agent_state` checkpoint.

