"""
MCP (Model Context Protocol) tool wiring for assistant agents.

Resolves the per-agent MCP scope (`none` / `shared` / `dedicated`) into one or
more `McpWorkbench` instances and exposes lifecycle hooks that the team
runtime cache uses to dispose of spawned subprocesses on team eviction.

Transport support:
  - stdio (default; entry shape: {command, args, env})
  - streamable HTTP (entry shape: {transport: "http", url, headers})

SSE is intentionally NOT supported (deprecated upstream).

Secrets:
  - Project-level `mcp_secrets` ({KEY: value}) are referenced from any string
    inside `mcpServers` entries via `{KEY_NAME}` placeholders and substituted
    at runtime by `_substitute_secrets()` immediately before `McpWorkbench`
    construction. The fingerprint logged in span attributes is computed over
    the **placeholder** form (pre-substitution) so it remains stable across
    secret rotations and never contains credential material.

Logging contract (see .agents/skills/observability_logging/SKILL.md):
  - `agents.mcp.created`  — INFO; payload contains scope + server names only.
  - `agents.mcp.closed`   — INFO; payload contains scope + server names only.
  - `agents.mcp.failed`   — EXCEPTION; never logs `args`/`env`/`headers`.
  - `args`, `env`, and `headers` MUST be redacted/omitted from every log line.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
from typing import TYPE_CHECKING, Any

from core.tracing import traced_function

if TYPE_CHECKING:
    from autogen_ext.tools.mcp import McpWorkbench

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


# session_id → list[McpWorkbench] — owned by the team runtime cache so that
# `evict_team()` can dispose them on team teardown.
_SESSION_WORKBENCHES: dict[str, list[Any]] = {}


def _mcp_stop_timeout_seconds() -> float:
    """Return per-workbench stop timeout in seconds (default 5)."""
    raw = (os.getenv("MCP_STOP_TIMEOUT_SECONDS", "") or "").strip()
    if not raw:
        return 5.0
    try:
        value = float(raw)
        return value if value > 0 else 5.0
    except ValueError:
        return 5.0


def _server_fingerprint(servers: dict) -> str:
    """Stable hash of a normalized mcpServers dict (used for de-dup/caching)."""
    blob = json.dumps(servers, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _substitute_secrets(node: Any, secrets: dict) -> Any:
    """
    Recursively replace `{KEY}` placeholders in every string scalar with
    `secrets[KEY]`. Unknown placeholders are left intact (validation rejects
    them at save time, so they should not occur at runtime).
    """
    if not secrets:
        return node
    if isinstance(node, str):
        def _repl(match: re.Match) -> str:
            key = match.group(1)
            return secrets.get(key, match.group(0))
        return _PLACEHOLDER_RE.sub(_repl, node)
    if isinstance(node, dict):
        return {k: _substitute_secrets(v, secrets) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute_secrets(v, secrets) for v in node]
    return node


def _resolve_stdio_command(server_name: str, command: str, env: dict | None = None) -> str:
    """
    Resolve a stdio command to an executable path when possible.

    This fails fast with a readable ValueError when the command is not
    available on the current host, so users get an actionable message instead
    of a deep MCP/asyncio subprocess traceback.
    """
    if not command:
        raise ValueError(f"MCP server '{server_name}' stdio command is empty.")

    command = command.strip()

    # Preserve explicit paths as-is when they exist.
    expanded = os.path.expandvars(os.path.expanduser(command))
    if os.path.isabs(expanded) or any(sep in expanded for sep in ("/", "\\")):
        if os.path.exists(expanded):
            return expanded
        raise ValueError(
            f"MCP server '{server_name}' stdio command path does not exist on this host. "
            "Use a valid executable path or install the required runtime."
        )

    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    resolved = shutil.which(command, path=merged_env.get("PATH"))
    if resolved:
        return resolved

    raise ValueError(
        f"MCP server '{server_name}' stdio command is not executable on this host. "
        "Ensure the command is installed and available on PATH. "
        "On Windows, install Node.js for npx-based servers or provide an explicit path "
        "to the launcher (for example, C:\\Program Files\\nodejs\\npx.cmd)."
    )


def _build_server_params(
    name: str,
    entry: dict,
    session_id: str | None = None,
    has_oauth: bool = False,
):
    """Map a validated mcpServers[name] entry to autogen_ext server params.

    When ``has_oauth=True`` the project carries an OAuth 2.0 app registration
    for this server.  If a session-scoped Bearer token is stored in Redis it is
    merged into the outbound headers.  A missing token raises ValueError with a
    clear message so the caller can surface it before the run starts.
    """
    from autogen_ext.tools.mcp import StdioServerParams, StreamableHttpServerParams

    transport = (entry.get("transport") or "").strip().lower()
    if transport == "http" or "url" in entry:
        headers: dict = dict(entry.get("headers") or {})

        if has_oauth:
            if not session_id:
                raise ValueError(
                    f"MCP server '{name}' requires OAuth authorization but no session_id "
                    "was provided at team-build time. This is a programming error."
                )
            from .session_coordination import get_mcp_oauth_token
            token = get_mcp_oauth_token(session_id, name)
            if not token:
                raise ValueError(
                    f"MCP server '{name}' requires OAuth authorization. "
                    "Please authorize via the chat run prompt before starting a run."
                )
            headers["Authorization"] = f"Bearer {token}"

        return StreamableHttpServerParams(
            url=entry["url"],
            headers=headers if headers else None,
        )
    resolved_command = _resolve_stdio_command(
        server_name=name,
        command=entry["command"],
        env=entry.get("env") or None,
    )
    return StdioServerParams(
        command=resolved_command,
        args=list(entry.get("args") or []),
        env=dict(entry.get("env") or {}),
    )


def resolve_mcp_servers_for_agent(agent_cfg: dict, project: dict) -> dict:
    """
    Return the merged mcpServers dict that applies to a given agent based
    on its scope. Empty dict when no MCP tools should be attached.

    Secrets are NOT substituted here — placeholders are preserved so that the
    span fingerprint computed in `build_mcp_workbenches()` remains stable
    across secret rotations. Substitution happens just before
    `McpWorkbench` construction.
    """
    scope = (agent_cfg.get("mcp_tools") or "none").strip().lower()
    if scope == "dedicated":
        cfg = agent_cfg.get("mcp_configuration") or {}
        return cfg.get("mcpServers") or {}
    if scope == "shared":
        cfg = project.get("shared_mcp_tools") or {}
        return cfg.get("mcpServers") or {}
    return {}


@traced_function("agents.mcp.workbench_built")
def build_mcp_workbenches(
    servers: dict,
    scope: str,
    secrets: dict | None = None,
    session_id: str | None = None,
    oauth_configs: dict | None = None,
) -> list[Any]:
    """
    Construct one `McpWorkbench` per server entry. The workbenches are NOT
    started here — autogen lazily starts each workbench on first tool call,
    or `register_session_workbenches()` can attach them to a session for
    deterministic teardown via `close_session_workbenches()`.

    `secrets` (project-level `mcp_secrets`) are substituted into every string
    value of each server entry before `McpWorkbench` construction. The
    fingerprint reported in logs is computed over the **placeholder** dict so
    it stays stable when secret values rotate and never carries credentials.

    `oauth_configs` (project-level `mcp_oauth_configs`) indicate which HTTP
    servers require OAuth Bearer token injection.  When a matching session-scoped
    token exists in Redis it is merged into the Authorization header.  A missing
    token raises ValueError so the pre-run gate can surface the error before
    AutoGen starts.

    Returns an empty list if `servers` is empty.
    """
    if not servers:
        return []

    from autogen_ext.tools.mcp import McpWorkbench

    fingerprint = _server_fingerprint(servers)
    secrets = secrets or {}
    oauth_configs = oauth_configs or {}

    workbenches: list[Any] = []
    server_names: list[str] = []
    for name, entry in servers.items():
        resolved_entry = _substitute_secrets(entry, secrets)
        has_oauth = name in oauth_configs
        params = _build_server_params(
            name, resolved_entry, session_id=session_id, has_oauth=has_oauth
        )
        workbenches.append(McpWorkbench(server_params=params))
        server_names.append(name)

    logger.info(
        "agents.mcp.created",
        extra={
            "scope": scope,
            "server_count": len(workbenches),
            "server_names": server_names,
            "fingerprint": fingerprint,
        },
    )
    return workbenches


def register_session_workbenches(session_id: str, workbenches: list[Any]) -> None:
    """Track workbenches that should be torn down when the session is evicted."""
    if not workbenches:
        return
    _SESSION_WORKBENCHES.setdefault(session_id, []).extend(workbenches)


def close_session_workbenches(session_id: str) -> None:
    """Stop and discard all MCP workbenches associated with a session."""
    workbenches = _SESSION_WORKBENCHES.pop(session_id, [])
    if not workbenches:
        return

    async def _stop_all():
        timeout_s = _mcp_stop_timeout_seconds()
        for wb in workbenches:
            try:
                await asyncio.wait_for(wb.stop(), timeout=timeout_s)
            except RuntimeError as exc:
                # Lazy-started workbenches may never have been started in a
                # session. Stopping those should be a no-op, not an error.
                if "not started" in str(exc).lower():
                    continue
                logger.exception(
                    "agents.mcp.failed",
                    extra={"session_id": session_id, "phase": "stop"},
                )
            except asyncio.TimeoutError:
                logger.exception(
                    "agents.mcp.failed",
                    extra={"session_id": session_id, "phase": "stop_timeout"},
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "agents.mcp.failed",
                    extra={"session_id": session_id, "phase": "stop"},
                )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Schedule and forget — eviction is fire-and-forget by design.
            loop.create_task(_stop_all())
        else:
            loop.run_until_complete(_stop_all())
    except RuntimeError:
        asyncio.run(_stop_all())

    logger.info(
        "agents.mcp.closed",
        extra={"session_id": session_id, "workbench_count": len(workbenches)},
    )


def close_all_workbenches() -> None:
    """Stop and discard all tracked MCP workbenches across every session."""
    for session_id in list(_SESSION_WORKBENCHES.keys()):
        close_session_workbenches(session_id)
