/**
 * mcp_oauth.js — MCP OAuth 2.0 pre-run gate
 *
 * Exposes window.McpOAuth with one public method:
 *
 *   McpOAuth.checkAndAuthorize(sessionId, projectId, secretKey, csrfToken)
 *     → Promise  (resolves when all OAuth servers are authorized, rejects on Cancel)
 *
 * Flow:
 *  1. GET /mcp/oauth/check/<sessionId>/   — discover which servers need auth
 *  2. If all_authorized → resolve immediately
 *  3. Else show modal listing unauthorized servers, each with an "Authorize" button
 *  4. Authorize button → window.open() popup → provider consent page → callback
 *  5. Callback sends postMessage({type:"mcp_oauth_done", server_name, success})
 *  6. When all authorized → close modal → resolve
 *  7. "Cancel" button → close modal → reject("cancelled")
 *
 * The modal polls /mcp/oauth/check/ every 3 s as a postMessage fallback.
 */

(function () {
  "use strict";

  const POLL_INTERVAL_MS = 3000;
  const CHECK_PATH = (sessionId) => `/mcp/oauth/check/${sessionId}/`;
  const START_PATH = `/mcp/oauth/start/`;

  let _pollTimer = null;
  let _modalOverlay = null;
  let _messageListener = null;

  /**
   * Fetch authorization status from the backend.
   * Returns { servers: [{server_name, label, authorized}], all_authorized }.
   */
  async function _fetchStatus(sessionId, secretKey) {
    const resp = await fetch(CHECK_PATH(sessionId), {
      headers: { "X-App-Secret-Key": secretKey },
    });
    if (!resp.ok) throw new Error(`MCP OAuth check failed: ${resp.status}`);
    return resp.json();
  }

  /** Remove the modal overlay and stop polling. */
  function _teardown() {
    if (_pollTimer) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
    if (_messageListener) {
      window.removeEventListener("message", _messageListener);
      _messageListener = null;
    }
    if (_modalOverlay && _modalOverlay.parentNode) {
      _modalOverlay.parentNode.removeChild(_modalOverlay);
    }
    _modalOverlay = null;
  }

  /** Open the provider consent popup for one server. */
  function _openAuthPopup(serverName, sessionId, projectId, secretKey) {
    const params = new URLSearchParams({
      flow: "run",
      server_name: serverName,
      session_id: sessionId,
      project_id: projectId,
      skey: secretKey,
    });
    window.open(
      `${START_PATH}?${params.toString()}`,
      `mcp_oauth_${serverName}`,
      "width=860,height=720,toolbar=0,menubar=0,location=0,status=0"
    );
  }

  /** Update a single server row to show "Authorized ✓" state. */
  function _markAuthorized(serverName) {
    const row = _modalOverlay &&
      _modalOverlay.querySelector(`[data-mcp-server="${CSS.escape(serverName)}"]`);
    if (!row) return;
    row.classList.add("mcp-oauth-modal__server--authorized");
    const btn = row.querySelector(".mcp-oauth-modal__authorize-btn");
    if (btn) {
      btn.textContent = "Authorized ✓";
      btn.disabled = true;
    }
  }

  /** Build and display the authorization modal. Returns a Promise. */
  function _showModal(servers, sessionId, projectId, secretKey) {
    return new Promise((resolve, reject) => {
      // Track per-server authorization locally
      const statusMap = {};
      servers.forEach((s) => {
        statusMap[s.server_name] = s.authorized;
      });

      // Build overlay DOM
      const overlay = document.createElement("div");
      overlay.className = "mcp-oauth-modal__overlay";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.setAttribute("aria-label", "MCP OAuth Authorization Required");

      const unauthorized = servers.filter((s) => !s.authorized);

      overlay.innerHTML = `
        <div class="mcp-oauth-modal__dialog">
          <div class="mcp-oauth-modal__header">
            <h2 class="mcp-oauth-modal__title">MCP Authorization Required</h2>
            <p class="mcp-oauth-modal__subtitle">
              The following MCP servers require OAuth authorization before the run can start.
              Click <strong>Authorize</strong> for each server, grant access in the popup, then continue.
            </p>
          </div>
          <ul class="mcp-oauth-modal__server-list">
            ${servers.map((s) => `
              <li class="mcp-oauth-modal__server${s.authorized ? " mcp-oauth-modal__server--authorized" : ""}"
                  data-mcp-server="${s.server_name}">
                <span class="mcp-oauth-modal__server-name">${s.label || s.server_name}</span>
                <button
                  type="button"
                  class="btn btn--sm btn--primary mcp-oauth-modal__authorize-btn"
                  ${s.authorized ? "disabled" : ""}
                  data-server-name="${s.server_name}">
                  ${s.authorized ? "Authorized ✓" : "Authorize"}
                </button>
              </li>
            `).join("")}
          </ul>
          <div class="mcp-oauth-modal__footer">
            <button type="button" class="btn btn--secondary mcp-oauth-modal__cancel-btn">Cancel</button>
          </div>
        </div>
      `;

      document.body.appendChild(overlay);
      _modalOverlay = overlay;

      // Authorize button click → open popup
      overlay.querySelectorAll(".mcp-oauth-modal__authorize-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const name = btn.dataset.serverName;
          _openAuthPopup(name, sessionId, projectId, secretKey);
        });
      });

      // Cancel → reject
      overlay.querySelector(".mcp-oauth-modal__cancel-btn").addEventListener("click", () => {
        _teardown();
        reject("cancelled");
      });

      /** Check whether all servers are now authorized and resolve if so. */
      function _checkAllDone() {
        if (Object.values(statusMap).every(Boolean)) {
          _teardown();
          resolve();
        }
      }

      // postMessage listener (primary signal from popup callback page)
      _messageListener = function (event) {
        const data = event.data || {};
        if (data.type === "mcp_oauth_done" && data.success && data.server_name) {
          statusMap[data.server_name] = true;
          _markAuthorized(data.server_name);
          _checkAllDone();
        }
      };
      window.addEventListener("message", _messageListener);

      // Polling fallback — re-fetch status every 3 s
      _pollTimer = setInterval(async () => {
        try {
          const status = await _fetchStatus(sessionId, secretKey);
          status.servers.forEach((s) => {
            if (s.authorized && !statusMap[s.server_name]) {
              statusMap[s.server_name] = true;
              _markAuthorized(s.server_name);
            }
          });
          if (status.all_authorized) {
            _teardown();
            resolve();
          }
        } catch (_) {
          /* network hiccup — keep polling */
        }
      }, POLL_INTERVAL_MS);
    });
  }

  /**
   * Main entry point called by home.js before starting an agent run.
   *
   * Resolves immediately if:
   *   - The project has no OAuth-protected MCP servers, OR
   *   - All servers already have valid Redis tokens for this session.
   *
   * Shows the authorization modal otherwise and resolves once all servers
   * are authorized or rejects with "cancelled" when the user cancels.
   */
  async function checkAndAuthorize(sessionId, projectId, secretKey) {
    let status;
    try {
      status = await _fetchStatus(sessionId, secretKey);
    } catch (err) {
      // If the check endpoint fails, log and proceed (non-blocking for run)
      console.warn("[McpOAuth] status check failed — continuing without OAuth gate:", err);
      return;
    }

    if (!status.servers || status.servers.length === 0) return;
    if (status.all_authorized) return;

    return _showModal(status.servers, sessionId, projectId, secretKey);
  }

  window.McpOAuth = { checkAndAuthorize };
})();
