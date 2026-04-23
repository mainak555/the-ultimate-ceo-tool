/**
 * export_modal_base.js — Shared shell for all provider export popups.
 *
 * Every provider implements an adapter object with:
 *   label                        String  — display name ("Trello", "Jira", …)
 *   pushLabel                    String? — optional Export button label override
 *   renderLeftPane(ctx)          ()=>HTML string for the left 70% editor pane
 *   referenceUrl(ctx)            ()=>URL string or null for right-pane markdown
 *   onOpen(ctx, baseAPI)         lifecycle — called after DOM is built and appended
 *   onExtract(ctx, baseAPI)      lifecycle — Extract Items button clicked
 *   onSave(ctx, baseAPI)         lifecycle — Save button clicked
 *   onPush(ctx, baseAPI)         lifecycle — Export button clicked
 *   syncFooter(ctx, baseAPI)     ()=>{ extractHidden, extractDisabled, saveDisabled,
 *                                       pushHidden, pushDisabled }
 *
 * Context object (from home.js → ProviderRegistry → adapter):
 *   { provider, sessionId, discussionId, secretKey, csrfToken, projectId }
 *
 * Base-owned element IDs (never declare these inside adapter renderLeftPane):
 *   #export-modal-overlay
 *   #export-modal-close
 *   #export-modal-extract-btn
 *   #export-modal-save-btn
 *   #export-modal-push-btn
 *   #export-modal-cancel-btn
 *   #export-modal-reference-markdown
 *   #export-modal-status
 *
 * Namespace: window.ExportModalBase
 */

(function () {
  "use strict";

  var OVERLAY_ID = "export-modal-overlay";

  var _ctx = {};
  var _adapter = null;

  // ---------------------------------------------------------------------------
  // Escape helper
  // ---------------------------------------------------------------------------

  function _esc(s) {
    var div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
  }

  // ---------------------------------------------------------------------------
  // baseAPI — passed to all adapter lifecycle callbacks
  // ---------------------------------------------------------------------------

  function setStatus(msg) {
    var el = document.getElementById("export-modal-status");
    if (el) el.textContent = msg || "";
  }

  function syncFooter() {
    if (!_adapter || typeof _adapter.syncFooter !== "function") return;
    var state = _adapter.syncFooter(_ctx, _baseAPI);
    if (!state) return;

    var extractBtn = document.getElementById("export-modal-extract-btn");
    var saveBtn    = document.getElementById("export-modal-save-btn");
    var pushBtn    = document.getElementById("export-modal-push-btn");

    if (extractBtn) {
      extractBtn.hidden   = !!state.extractHidden;
      extractBtn.disabled = !!state.extractDisabled;
    }
    if (saveBtn) {
      saveBtn.disabled = !!state.saveDisabled;
    }
    if (pushBtn) {
      pushBtn.hidden   = !!state.pushHidden;
      pushBtn.disabled = !!state.pushDisabled;
    }
  }

  function close() {
    var overlay = document.getElementById(OVERLAY_ID);
    if (overlay) overlay.remove();
    _ctx     = {};
    _adapter = null;
  }

  var _baseAPI = {
    setStatus: setStatus,
    syncFooter: syncFooter,
    close: close,
  };

  // ---------------------------------------------------------------------------
  // Right-pane reference loader
  // ---------------------------------------------------------------------------

  function _loadReference() {
    var el = document.getElementById("export-modal-reference-markdown");
    if (!el) return;

    if (!_adapter || typeof _adapter.referenceUrl !== "function") {
      el.textContent = "No reference available.";
      return;
    }

    var url = _adapter.referenceUrl(_ctx);
    if (!url) {
      el.textContent = "No reference available.";
      return;
    }

    fetch(url, {
      headers: { "X-App-Secret-Key": _ctx.secretKey || "" },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var markdown  = (data && data.markdown)   ? String(data.markdown)   : "No content available.";
        var agentName = (data && data.agent_name) ? String(data.agent_name) : "";
        var titleEl   = document.getElementById("export-modal-reference-title");
        if (titleEl && agentName) {
          titleEl.textContent = "Assistant (" + agentName + ") Output";
        }
        if (window.MarkdownViewer && typeof window.MarkdownViewer.render === "function") {
          el.innerHTML = window.MarkdownViewer.render(markdown);
        } else {
          el.textContent = markdown;
        }
      })
      .catch(function () {
        el.textContent = "Could not load reference content.";
      });
  }

  // ---------------------------------------------------------------------------
  // open — entry point
  // ---------------------------------------------------------------------------

  function open(ctx, adapter) {
    if (!adapter) return;

    _ctx     = Object.assign({}, ctx || {});
    _adapter = adapter;

    // Remove any existing overlay
    var existing = document.getElementById(OVERLAY_ID);
    if (existing) existing.remove();

    var label    = _esc(adapter.label || "Integration");
    var pushLabel = _esc(adapter.pushLabel || adapter.label || "Integration");
    var leftPane = typeof adapter.renderLeftPane === "function" ? adapter.renderLeftPane(_ctx) : "";

    var overlay = document.createElement("div");
    overlay.className = "export-modal-overlay";
    overlay.id        = OVERLAY_ID;
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });

    overlay.innerHTML =
      '<div class="export-modal export-modal--wide">'

      // ── Header ──────────────────────────────────────────────────────────────
      + '<div class="export-modal__header">'
      + '<h3>Export to ' + label + '</h3>'
      + '<button type="button" class="export-modal__close" id="export-modal-close">&times;</button>'
      + '</div>'

      // ── Body: 70/30 split ───────────────────────────────────────────────────
      + '<div class="export-modal__body">'
      + '<div class="trello-workbench">'

      // Left pane — editor (provider-specific HTML injected here)
      + '<div class="trello-workbench__pane trello-workbench__pane--editor">'
      + leftPane
      + '</div>'

      // Right pane — raw reference markdown
      + '<div class="trello-workbench__pane trello-workbench__pane--reference">'
      + '<div class="trello-reference">'
      + '<h4 id="export-modal-reference-title">Assistant (' + label + ') Output</h4>'
      + '<div id="export-modal-reference-markdown" class="trello-reference__markdown">Loading\u2026</div>'
      + '</div>'
      + '</div>'

      + '</div>'
      + '</div>'

      // ── Footer ───────────────────────────────────────────────────────────────
      + '<div class="export-modal__footer export-modal__footer--wrap">'
      + '<button type="button" class="btn btn--secondary btn--sm" id="export-modal-extract-btn" hidden>Extract Items</button>'
      + '<button type="button" class="btn btn--success btn--sm"   id="export-modal-save-btn">Save</button>'
      + '<button type="button" class="btn btn--primary btn--sm"   id="export-modal-push-btn" hidden>Export to ' + pushLabel + '</button>'
      + '<button type="button" class="btn btn--secondary btn--sm" id="export-modal-cancel-btn">Close</button>'
      + '<span id="export-modal-status" class="form-hint"></span>'
      + '</div>'

      + '</div>';

    document.body.appendChild(overlay);

    // Bind base-owned button events
    overlay.querySelector("#export-modal-close").addEventListener("click", close);
    overlay.querySelector("#export-modal-cancel-btn").addEventListener("click", close);

    overlay.querySelector("#export-modal-extract-btn").addEventListener("click", function () {
      if (_adapter && typeof _adapter.onExtract === "function") {
        _adapter.onExtract(_ctx, _baseAPI);
      }
    });
    overlay.querySelector("#export-modal-save-btn").addEventListener("click", function () {
      if (_adapter && typeof _adapter.onSave === "function") {
        _adapter.onSave(_ctx, _baseAPI);
      }
    });
    overlay.querySelector("#export-modal-push-btn").addEventListener("click", function () {
      if (_adapter && typeof _adapter.onPush === "function") {
        _adapter.onPush(_ctx, _baseAPI);
      }
    });

    // Load right-pane reference (async, non-blocking)
    _loadReference();

    // Let adapter initialize left-pane: bind events, check status, load saved data
    if (typeof adapter.onOpen === "function") {
      adapter.onOpen(_ctx, _baseAPI);
    }

    syncFooter();
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  window.ExportModalBase = {
    open:        open,
    close:       close,
    setStatus:   setStatus,
    syncFooter:  syncFooter,
  };
})();
