/**
 * trello.js — Self-contained Trello export modal.
 *
 * Namespace: window.TrelloExport
 *
 * Flow:
 *   1. openModal(sessionId, secretKey, csrfToken)
 *   2. Check token status — if valid, show destination; if not, show config prompt
 *   3. Load cascade: Workspaces → Boards → Lists
 *   4. Extract → Preview → Confirm → Push
 */

(function () {
  "use strict";

  var _state = {};

  function _headers() {
    return {
      "X-App-Secret-Key": _state.secretKey,
      "X-CSRFToken": _state.csrfToken,
      "Content-Type": "application/json",
    };
  }

  function _api(method, path, body) {
    var opts = { method: method, headers: _headers() };
    if (body) opts.body = JSON.stringify(body);
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.error || "Request failed");
        return d;
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Modal DOM
  // ---------------------------------------------------------------------------

  function _createModal() {
    var overlay = document.createElement("div");
    overlay.className = "export-modal-overlay";
    overlay.id = "trello-export-overlay";
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeModal();
    });

    overlay.innerHTML =
      '<div class="export-modal">'
      + '<div class="export-modal__header">'
      + '<h3>Export to Trello</h3>'
      + '<button type="button" class="export-modal__close" id="trello-modal-close">&times;</button>'
      + '</div>'
      + '<div class="export-modal__body">'
      // Token section
      + '<div class="export-modal__section" id="trello-token-section">'
      + '<h4>Authorization</h4>'
      + '<div id="trello-token-status">Checking token…</div>'
      + '</div>'
      // Destination section
      + '<div class="export-modal__section" id="trello-destination-section" hidden>'
      + '<h4>Destination</h4>'
      + '<div class="cascade-select">'
      + '<div class="cascade-select__group">'
      + '<label>Workspace</label>'
      + '<select id="trello-workspace-select" class="input input--sm"><option value="">Loading…</option></select>'
      + '</div>'
      + '<div class="cascade-select__group">'
      + '<label>Board</label>'
      + '<select id="trello-board-select" class="input input--sm"><option value="">—</option></select>'
      + '<div id="trello-create-board" class="cascade-select__create-new" hidden>'
      + '<input type="text" class="input input--sm" placeholder="New board name">'
      + '<button type="button" class="btn btn--sm btn--success">Create</button>'
      + '</div>'
      + '</div>'
      + '<div class="cascade-select__group">'
      + '<label>List</label>'
      + '<select id="trello-list-select" class="input input--sm"><option value="">—</option></select>'
      + '<div id="trello-create-list" class="cascade-select__create-new" hidden>'
      + '<input type="text" class="input input--sm" placeholder="New list name">'
      + '<button type="button" class="btn btn--sm btn--success">Create</button>'
      + '</div>'
      + '</div>'
      + '</div>'
      + '</div>'
      // Mapping guide
      + '<div class="export-modal__section" id="trello-mapping-section" hidden>'
      + '<h4>Mapping</h4>'
      + '<div class="mapping-guide">'
      + '<span class="mapping-badge mapping-badge--card">Title → Card</span>'
      + '<span class="mapping-badge mapping-badge--desc">Description → Card Description</span>'
      + '<span class="mapping-badge mapping-badge--checklist">Children → Checklist Items</span>'
      + '</div>'
      + '</div>'
      // Extract / Preview
      + '<div class="export-modal__section" id="trello-preview-section" hidden>'
      + '<h4>Preview</h4>'
      + '<div id="trello-preview-items"></div>'
      + '</div>'
      + '</div>'
      // Footer
      + '<div class="export-modal__footer">'
      + '<button type="button" class="btn btn--secondary btn--sm" id="trello-extract-btn" hidden>Extract Items</button>'
      + '<button type="button" class="btn btn--primary btn--sm" id="trello-push-btn" hidden>Export to Trello</button>'
      + '<span id="trello-modal-status" class="form-hint"></span>'
      + '</div>'
      + '</div>';

    document.body.appendChild(overlay);
    _bindModalEvents(overlay);
    return overlay;
  }

  function _bindModalEvents(overlay) {
    overlay.querySelector("#trello-modal-close").addEventListener("click", closeModal);

    overlay.querySelector("#trello-workspace-select").addEventListener("change", function () {
      _loadBoards(this.value);
    });

    overlay.querySelector("#trello-board-select").addEventListener("change", function () {
      var v = this.value;
      var createDiv = overlay.querySelector("#trello-create-board");
      if (v === "__new__") {
        createDiv.hidden = false;
        overlay.querySelector("#trello-list-select").innerHTML = '<option value="">—</option>';
      } else {
        createDiv.hidden = true;
        if (v) _loadLists(v);
      }
      _syncFooter();
    });

    overlay.querySelector("#trello-list-select").addEventListener("change", function () {
      var createDiv = overlay.querySelector("#trello-create-list");
      createDiv.hidden = this.value !== "__new__";
      _syncFooter();
    });

    // Create board
    var createBoardDiv = overlay.querySelector("#trello-create-board");
    createBoardDiv.querySelector("button").addEventListener("click", function () {
      var input = createBoardDiv.querySelector("input");
      var name = input.value.trim();
      if (!name) return;
      var wsId = overlay.querySelector("#trello-workspace-select").value || undefined;
      _setStatus("Creating board…");
      _api("POST", "/trello/" + _state.sessionId + "/create-board/", { name: name, workspace_id: wsId })
        .then(function (board) {
          createBoardDiv.hidden = true;
          input.value = "";
          var sel = overlay.querySelector("#trello-board-select");
          var opt = new Option(board.name, board.id, true, true);
          sel.add(opt, sel.length - 1);
          _setStatus("");
          _loadLists(board.id);
        })
        .catch(function (err) { _setStatus("Error: " + err.message); });
    });

    // Create list
    var createListDiv = overlay.querySelector("#trello-create-list");
    createListDiv.querySelector("button").addEventListener("click", function () {
      var input = createListDiv.querySelector("input");
      var name = input.value.trim();
      if (!name) return;
      var boardId = overlay.querySelector("#trello-board-select").value;
      if (!boardId || boardId === "__new__") return;
      _setStatus("Creating list…");
      _api("POST", "/trello/" + _state.sessionId + "/create-list/", { name: name, board_id: boardId })
        .then(function (list) {
          createListDiv.hidden = true;
          input.value = "";
          var sel = overlay.querySelector("#trello-list-select");
          var opt = new Option(list.name, list.id, true, true);
          sel.add(opt, sel.length - 1);
          _setStatus("");
          _syncFooter();
        })
        .catch(function (err) { _setStatus("Error: " + err.message); });
    });

    // Extract
    overlay.querySelector("#trello-extract-btn").addEventListener("click", _extract);

    // Push
    overlay.querySelector("#trello-push-btn").addEventListener("click", _push);
  }

  // ---------------------------------------------------------------------------
  // Token
  // ---------------------------------------------------------------------------

  function _checkToken() {
    _api("GET", "/trello/" + _state.sessionId + "/token-status/")
      .then(function (d) {
        var statusEl = document.getElementById("trello-token-status");
        if (d.valid) {
          statusEl.innerHTML = '<span class="export-modal__token-status export-modal__token-status--valid">✅ Authorized</span>';
          if (d.token_generated_at) statusEl.innerHTML += ' <small>(configured ' + d.token_generated_at + ')</small>';
          // Store defaults for pre-selection
          if (d.defaults) _state.defaults = d.defaults;
          _showDestination();
        } else {
          statusEl.innerHTML = 'Not authorized. <a href="#" onclick="return false;" style="pointer-events:none;">Configure Trello token in Project Settings (edit mode).</a>';
        }
      })
      .catch(function (err) {
        document.getElementById("trello-token-status").textContent = "Error: " + err.message;
      });
  }

  // ---------------------------------------------------------------------------
  // Cascade dropdowns
  // ---------------------------------------------------------------------------

  function _showDestination() {
    document.getElementById("trello-destination-section").hidden = false;
    document.getElementById("trello-mapping-section").hidden = false;
    _loadWorkspaces();
  }

  function _loadWorkspaces() {
    var sel = document.getElementById("trello-workspace-select");
    sel.innerHTML = '<option value="">Loading…</option>';
    var defaultWs = (_state.defaults && _state.defaults.default_workspace_id) || "";
    _api("GET", "/trello/" + _state.sessionId + "/workspaces/")
      .then(function (list) {
        var html = '<option value="">(All / Personal)</option>';
        list.forEach(function (w) {
          var selected = (w.id === defaultWs) ? ' selected' : '';
          html += '<option value="' + w.id + '"' + selected + '>' + _esc(w.displayName) + '</option>';
        });
        sel.innerHTML = html;
        _loadBoards(sel.value);
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  function _loadBoards(workspaceId) {
    var sel = document.getElementById("trello-board-select");
    sel.innerHTML = '<option value="">Loading…</option>';
    var url = "/trello/" + _state.sessionId + "/boards/";
    if (workspaceId) url += "?workspace=" + encodeURIComponent(workspaceId);
    var defaultBoard = (_state.defaults && _state.defaults.default_board_id) || "";
    _api("GET", url)
      .then(function (list) {
        var html = '<option value="">— Select Board —</option>';
        list.forEach(function (b) {
          var selected = (b.id === defaultBoard) ? ' selected' : '';
          html += '<option value="' + b.id + '"' + selected + '>' + _esc(b.name) + '</option>';
        });
        html += '<option value="__new__">➕ Create New Board</option>';
        sel.innerHTML = html;
        document.getElementById("trello-create-board").hidden = true;
        // Auto-load lists if a default board is selected
        if (defaultBoard && sel.value === defaultBoard) {
          _loadLists(defaultBoard);
        } else {
          document.getElementById("trello-list-select").innerHTML = '<option value="">—</option>';
        }
        _syncFooter();
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  function _loadLists(boardId) {
    var sel = document.getElementById("trello-list-select");
    sel.innerHTML = '<option value="">Loading…</option>';
    var defaultList = (_state.defaults && _state.defaults.default_list_id) || "";
    _api("GET", "/trello/" + _state.sessionId + "/lists/?board=" + encodeURIComponent(boardId))
      .then(function (list) {
        var html = '<option value="">— Select List —</option>';
        list.forEach(function (l) {
          var selected = (l.id === defaultList) ? ' selected' : '';
          html += '<option value="' + l.id + '"' + selected + '>' + _esc(l.name) + '</option>';
        });
        html += '<option value="__new__">➕ Create New List</option>';
        sel.innerHTML = html;
        document.getElementById("trello-create-list").hidden = true;
        _syncFooter();
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  // ---------------------------------------------------------------------------
  // Extract & Push
  // ---------------------------------------------------------------------------

  function _syncFooter() {
    var listId = _getSelectedListId();
    var extractBtn = document.getElementById("trello-extract-btn");
    var pushBtn = document.getElementById("trello-push-btn");
    extractBtn.hidden = !listId;
    pushBtn.hidden = !(_state.extractedItems && _state.extractedItems.length && listId);
  }

  function _getSelectedListId() {
    var sel = document.getElementById("trello-list-select");
    var v = sel ? sel.value : "";
    return (v && v !== "__new__") ? v : "";
  }

  function _extract() {
    _setStatus("Extracting items…");
    document.getElementById("trello-extract-btn").disabled = true;
    _api("POST", "/trello/" + _state.sessionId + "/extract/")
      .then(function (d) {
        _state.extractedItems = d.items || [];
        _renderPreview(_state.extractedItems);
        _setStatus("Extracted " + _state.extractedItems.length + " item(s).");
        document.getElementById("trello-extract-btn").disabled = false;
        _syncFooter();
      })
      .catch(function (err) {
        _setStatus("Extraction error: " + err.message);
        document.getElementById("trello-extract-btn").disabled = false;
      });
  }

  function _renderPreview(items) {
    var section = document.getElementById("trello-preview-section");
    section.hidden = false;
    var container = document.getElementById("trello-preview-items");
    if (!items.length) { container.innerHTML = '<p>No items extracted.</p>'; return; }

    var html = "";
    items.forEach(function (item) {
      html += '<div class="export-preview__item">';
      html += '<strong class="mapping-badge mapping-badge--card">📋 ' + _esc(item.title || "Untitled") + '</strong>';
      if (item.description) {
        html += '<p class="mapping-badge mapping-badge--desc">📝 ' + _esc(item.description).substring(0, 200) + '</p>';
      }
      if (item.children && item.children.length) {
        html += '<ul>';
        item.children.forEach(function (c) {
          html += '<li class="mapping-badge mapping-badge--checklist">☑️ ' + _esc(c.title || "") + '</li>';
        });
        html += '</ul>';
      }
      html += '</div>';
    });
    container.innerHTML = html;
  }

  function _push() {
    var listId = _getSelectedListId();
    if (!listId || !_state.extractedItems || !_state.extractedItems.length) return;

    _setStatus("Exporting to Trello…");
    document.getElementById("trello-push-btn").disabled = true;

    _api("POST", "/trello/" + _state.sessionId + "/push/", { list_id: listId, items: _state.extractedItems })
      .then(function (d) {
        var count = (d.result || []).length;
        _setStatus("✅ Exported " + count + " card(s) to Trello!");
        document.getElementById("trello-push-btn").disabled = false;
        document.getElementById("trello-push-btn").hidden = true;

        // Show links
        var container = document.getElementById("trello-preview-items");
        var html = '<div class="export-preview__success"><strong>Exported cards:</strong><ul>';
        (d.result || []).forEach(function (r) {
          html += '<li><a href="' + _esc(r.url) + '" target="_blank" rel="noopener">' + _esc(r.title) + '</a></li>';
        });
        html += '</ul></div>';
        container.innerHTML = html;
      })
      .catch(function (err) {
        _setStatus("Export error: " + err.message);
        document.getElementById("trello-push-btn").disabled = false;
      });
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function _setStatus(msg) {
    var el = document.getElementById("trello-modal-status");
    if (el) el.textContent = msg;
  }

  function _esc(s) {
    var div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  function openModal(sessionId, secretKey, csrfToken) {
    _state = { sessionId: sessionId, secretKey: secretKey, csrfToken: csrfToken, extractedItems: null };

    var existing = document.getElementById("trello-export-overlay");
    if (existing) existing.remove();

    _createModal();
    _checkToken();
  }

  function closeModal() {
    var overlay = document.getElementById("trello-export-overlay");
    if (overlay) overlay.remove();
    _state = {};
  }

  window.TrelloExport = {
    openModal: openModal,
    closeModal: closeModal,
  };
})();
