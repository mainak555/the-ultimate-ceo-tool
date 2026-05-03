/**
 * mcp_oauth.js — MCP OAuth 2.0 helper module
 *
 * The OAuth pre-run gate is now driven by the SERVER:
 *   - POST /chat/sessions/<id>/run/ returns 409 + {status:"awaiting_mcp_oauth", servers:[...]}
 *     when one or more reachable MCP servers require OAuth and have no
 *     session-scoped Redis token, OR
 *   - The SSE stream emits an `awaiting_mcp_oauth` event when the same condition
 *     is detected mid-run (token expired between turns).
 *
 * Both paths cause home.js to render the `.chat-oauth-panel` card and open a
 * WebSocket to `ws/mcp/oauth/<session_id>/` for real-time readiness updates.
 * Polling has been removed; status updates arrive via Redis pub/sub → WS push.
 *
 * This module provides the single helper home.js needs to drive that card:
 *   - openAuthPopup(serverName, sessionId, projectId, secretKey)
 */

(function () {
  "use strict";

  const START_PATH = `/mcp/oauth/start/`;

  /** Open the provider consent popup for one server (run-time flow). */
  function openAuthPopup(serverName, sessionId, projectId, secretKey) {
    const params = new URLSearchParams({
      flow: "run",
      server_name: serverName,
      session_id: sessionId,
      project_id: projectId || "",
      skey: secretKey,
    });
    window.open(
      `${START_PATH}?${params.toString()}`,
      `mcp_oauth_${serverName}`,
      "width=860,height=720,toolbar=0,menubar=0,location=0,status=0"
    );
  }

  window.McpOAuth = { openAuthPopup };
})();
