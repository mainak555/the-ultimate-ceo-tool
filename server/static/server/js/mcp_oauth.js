/**
 * mcp_oauth.js — MCP OAuth 2.0 helper module
 *
 * The OAuth pre-run gate is now driven by the SERVER:
 *   - POST /chat/sessions/<id>/run/ returns 409 + {status:"awaiting_oauth", servers:[...]}
 *     when one or more reachable MCP servers require OAuth and have no
 *     session-scoped Redis token, OR
 *   - The SSE stream emits an `awaiting_oauth` event when the same condition
 *     is detected mid-run (token expired between turns).
 *
 * Both paths cause home.js to swap in the chat-history partial, which renders
 * the `.chat-oauth-panel` card (template-driven; survives page reload).
 *
 * This module provides the small helpers home.js needs to drive that card:
 *   - openAuthPopup(serverName, sessionId, projectId, secretKey)
 *   - fetchStatus(sessionId, secretKey)  → JSON {servers:[...], all_authorized}
 */

(function () {
  "use strict";

  const CHECK_PATH = (sessionId) => `/mcp/oauth/check/${sessionId}/`;
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

  /**
   * Fetch authorization status from the backend.
   * Returns { servers: [{server_name, label, authorized}], all_authorized, project_id }.
   */
  async function fetchStatus(sessionId, secretKey) {
    const resp = await fetch(CHECK_PATH(sessionId), {
      headers: { "X-App-Secret-Key": secretKey },
    });
    if (!resp.ok) throw new Error(`MCP OAuth check failed: ${resp.status}`);
    return resp.json();
  }

  window.McpOAuth = { openAuthPopup, fetchStatus };
})();
