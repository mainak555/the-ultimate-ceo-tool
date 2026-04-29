#!/bin/sh
# MCP Gateway entrypoint.
#
# Reads ${MCP_CONFIG_PATH} (default /config/mcp.json) describing one or more
# MCP servers and exposes each one as a Streamable HTTP endpoint on
# ${MCP_GATEWAY_PORT} (default 9000) under the path /<server-name>/mcp.
#
# Format of mcp.json:
# {
#   "mcpServers": {
#     "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"] },
#     "fetch":      { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-fetch"] }
#   }
# }
set -e

CONFIG_PATH="${MCP_CONFIG_PATH:-/config/mcp.json}"
PORT="${MCP_GATEWAY_PORT:-9000}"

if [ ! -f "$CONFIG_PATH" ]; then
    echo "[mcp-gateway] no config file at $CONFIG_PATH; sleeping (provide one via volume mount)"
    exec sleep infinity
fi

echo "[mcp-gateway] starting mcp-proxy on port $PORT using $CONFIG_PATH"
exec npx -y mcp-proxy --config "$CONFIG_PATH" --port "$PORT" --host 0.0.0.0
