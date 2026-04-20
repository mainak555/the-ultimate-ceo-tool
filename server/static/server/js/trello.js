/**
 * trello.js - Trello export modal with editable card workspace.
 *
 * Namespace: window.TrelloExport
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

  function _esc(s) {
    var div = document.createElement("div");
    div.textContent = s || "";
    return div.innerHTML;
  }

  function _setStatus(msg) {
    var el = document.getElementById("trello-modal-status");
    if (el) el.textContent = msg || "";
  }

  function _createModal() {
    var overlay = document.createElement("div");
    overlay.className = "export-modal-overlay";
    overlay.id = "trello-export-overlay";
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeModal();
    });

    overlay.innerHTML =
      '<div class="export-modal export-modal--wide">'
      + '<div class="export-modal__header">'
      + '<h3>Export to Trello</h3>'
      + '<button type="button" class="export-modal__close" id="trello-modal-close">&times;</button>'
      + '</div>'
      + '<div class="export-modal__body">'
      + '<div class="export-modal__section" id="trello-token-section">'
      + '<h4>Authorization</h4>'
      + '<div id="trello-token-status">Checking token...</div>'
      + '</div>'
      + '<div class="export-modal__section" id="trello-destination-section" hidden>'
      + '<h4>Destination</h4>'
      + '<div class="cascade-select">'
      + '<div class="cascade-select__group">'
      + '<label>Workspace</label>'
      + '<select id="trello-workspace-select" class="input input--sm"><option value="">Loading...</option></select>'
      + '</div>'
      + '<div class="cascade-select__group">'
      + '<label>Board</label>'
      + '<select id="trello-board-select" class="input input--sm"><option value="">-</option></select>'
      + '<div id="trello-create-board" class="cascade-select__create-new" hidden>'
      + '<input type="text" class="input input--sm" placeholder="New board name">'
      + '<button type="button" class="btn btn--sm btn--success">Create</button>'
      + '</div>'
      + '</div>'
      + '<div class="cascade-select__group">'
      + '<label>List</label>'
      + '<select id="trello-list-select" class="input input--sm"><option value="">-</option></select>'
      + '<div id="trello-create-list" class="cascade-select__create-new" hidden>'
      + '<input type="text" class="input input--sm" placeholder="New list name">'
      + '<button type="button" class="btn btn--sm btn--success">Create</button>'
      + '</div>'
      + '</div>'
      + '</div>'
      + '</div>'
      + '<div class="export-modal__section" id="trello-workspace-section">'
      + '<div class="trello-editor__header-row">'
      + '<span>List</span>'
      + '<strong>Card Number #</strong>'
      + '</div>'
      + '<div id="trello-editor-cards" class="trello-editor__cards"></div>'
      + '<button type="button" class="btn btn--sm btn--secondary" id="trello-add-card-btn">Add Card</button>'
      + '</div>'
      + '</div>'
      + '<div class="export-modal__footer export-modal__footer--wrap">'
      + '<button type="button" class="btn btn--secondary btn--sm" id="trello-extract-btn" hidden>Extract Items</button>'
      + '<button type="button" class="btn btn--success btn--sm" id="trello-save-btn">Update in DB</button>'
      + '<button type="button" class="btn btn--primary btn--sm" id="trello-push-btn" hidden>Export to Trello</button>'
      + '<button type="button" class="btn btn--secondary btn--sm" id="trello-cancel-btn">Cancel</button>'
      + '<span id="trello-modal-status" class="form-hint"></span>'
      + '</div>'
      + '</div>';

    document.body.appendChild(overlay);
    _bindModalEvents(overlay);
    return overlay;
  }

  function _bindModalEvents(overlay) {
    overlay.querySelector("#trello-modal-close").addEventListener("click", closeModal);
    overlay.querySelector("#trello-cancel-btn").addEventListener("click", closeModal);

    overlay.querySelector("#trello-workspace-select").addEventListener("change", function () {
      _loadBoards(this.value);
    });

    overlay.querySelector("#trello-board-select").addEventListener("change", function () {
      var v = this.value;
      var createDiv = overlay.querySelector("#trello-create-board");
      if (v === "__new__") {
        createDiv.hidden = false;
        overlay.querySelector("#trello-list-select").innerHTML = '<option value="">-</option>';
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

    var createBoardDiv = overlay.querySelector("#trello-create-board");
    createBoardDiv.querySelector("button").addEventListener("click", function () {
      var input = createBoardDiv.querySelector("input");
      var name = input.value.trim();
      if (!name) return;
      var wsId = overlay.querySelector("#trello-workspace-select").value || undefined;
      _setStatus("Creating board...");
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

    var createListDiv = overlay.querySelector("#trello-create-list");
    createListDiv.querySelector("button").addEventListener("click", function () {
      var input = createListDiv.querySelector("input");
      var name = input.value.trim();
      if (!name) return;
      var boardId = overlay.querySelector("#trello-board-select").value;
      if (!boardId || boardId === "__new__") return;
      _setStatus("Creating list...");
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

    overlay.querySelector("#trello-add-card-btn").addEventListener("click", function () {
      _state.cards.push(_emptyCard());
      _renderEditorCards();
      _syncFooter();
    });

    overlay.querySelector("#trello-extract-btn").addEventListener("click", _extract);
    overlay.querySelector("#trello-save-btn").addEventListener("click", _saveExport);
    overlay.querySelector("#trello-push-btn").addEventListener("click", _push);

    overlay.querySelector("#trello-editor-cards").addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      var cardIndex = parseInt(btn.getAttribute("data-card-index") || "-1", 10);
      if (cardIndex < 0 || cardIndex >= _state.cards.length) return;

      if (btn.classList.contains("js-delete-card")) {
        _state.cards.splice(cardIndex, 1);
        if (!_state.cards.length) _state.cards.push(_emptyCard());
        _renderEditorCards();
        _syncFooter();
        return;
      }

      if (btn.classList.contains("js-add-checklist")) {
        _state.cards[cardIndex].checklists.push({ name: "Tasks", items: [{ title: "", checked: false }] });
        _renderEditorCards();
        return;
      }

      if (btn.classList.contains("js-add-custom-field")) {
        _state.cards[cardIndex].custom_fields.push({ field_name: "", field_type: "text", value: "" });
        _renderEditorCards();
        return;
      }

      if (btn.classList.contains("js-save-card")) {
        _state.cards = _collectCardsFromEditor();
        _saveExport();
      }
    });

    overlay.querySelector("#trello-editor-cards").addEventListener("input", function () {
      _syncFooter();
    });
  }

  function _checkToken() {
    _api("GET", "/trello/" + _state.sessionId + "/token-status/")
      .then(function (d) {
        var statusEl = document.getElementById("trello-token-status");
        if (d.valid) {
          statusEl.innerHTML = '<span class="export-modal__token-status export-modal__token-status--valid">Authorized</span>';
          if (d.token_generated_at) statusEl.innerHTML += ' <small>(configured ' + d.token_generated_at + ')</small>';
          if (d.defaults) _state.defaults = d.defaults;
          _showDestination();
        } else {
          statusEl.textContent = "Not authorized. Configure Trello token in Project Settings.";
        }
      })
      .catch(function (err) {
        document.getElementById("trello-token-status").textContent = "Error: " + err.message;
      });
  }

  function _showDestination() {
    document.getElementById("trello-destination-section").hidden = false;
    _loadWorkspaces();
  }

  function _loadWorkspaces() {
    var sel = document.getElementById("trello-workspace-select");
    sel.innerHTML = '<option value="">Loading...</option>';
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
    sel.innerHTML = '<option value="">Loading...</option>';
    var url = "/trello/" + _state.sessionId + "/boards/";
    if (workspaceId) url += "?workspace=" + encodeURIComponent(workspaceId);
    var defaultBoard = (_state.defaults && _state.defaults.default_board_id) || "";
    _api("GET", url)
      .then(function (list) {
        var html = '<option value="">- Select Board -</option>';
        list.forEach(function (b) {
          var selected = (b.id === defaultBoard) ? ' selected' : '';
          html += '<option value="' + b.id + '"' + selected + '>' + _esc(b.name) + '</option>';
        });
        html += '<option value="__new__">Create New Board</option>';
        sel.innerHTML = html;
        document.getElementById("trello-create-board").hidden = true;
        if (defaultBoard && sel.value === defaultBoard) {
          _loadLists(defaultBoard);
        } else {
          document.getElementById("trello-list-select").innerHTML = '<option value="">-</option>';
        }
        _syncFooter();
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  function _loadLists(boardId) {
    var sel = document.getElementById("trello-list-select");
    sel.innerHTML = '<option value="">Loading...</option>';
    var defaultList = (_state.defaults && _state.defaults.default_list_id) || "";
    _api("GET", "/trello/" + _state.sessionId + "/lists/?board=" + encodeURIComponent(boardId))
      .then(function (list) {
        var html = '<option value="">- Select List -</option>';
        list.forEach(function (l) {
          var selected = (l.id === defaultList) ? ' selected' : '';
          html += '<option value="' + l.id + '"' + selected + '>' + _esc(l.name) + '</option>';
        });
        html += '<option value="__new__">Create New List</option>';
        sel.innerHTML = html;
        document.getElementById("trello-create-list").hidden = true;
        _syncFooter();
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  function _emptyCard() {
    return {
      card_title: "",
      card_description: "",
      checklists: [{ name: "Tasks", items: [{ title: "", checked: false }] }],
      custom_fields: [],
      labels: [],
      priority: "",
      confidence_score: 0.0,
    };
  }

  function _syncFooter() {
    var listId = _getSelectedListId();
    var extractBtn = document.getElementById("trello-extract-btn");
    var pushBtn = document.getElementById("trello-push-btn");
    var saveBtn = document.getElementById("trello-save-btn");

    extractBtn.hidden = !_state.discussionId;
    saveBtn.disabled = false;
    pushBtn.hidden = !(_state.cards && _state.cards.length && listId);
  }

  function _getSelectedListId() {
    var sel = document.getElementById("trello-list-select");
    var v = sel ? sel.value : "";
    return (v && v !== "__new__") ? v : "";
  }

  function _loadSavedExport() {
    _setStatus("Loading saved export...");
    _api("GET", "/trello/" + _state.sessionId + "/export/" + encodeURIComponent(_state.discussionId) + "/")
      .then(function (data) {
        var cards = (data.export && data.export.cards) || [];
        _state.cards = cards.length ? cards : [_emptyCard()];
        _renderEditorCards();
        _setStatus(data.saved ? "Loaded saved export JSON." : "No saved JSON found. You can extract or edit manually.");
        _syncFooter();
      })
      .catch(function (err) {
        _state.cards = [_emptyCard()];
        _renderEditorCards();
        _setStatus("Load error: " + err.message);
      });
  }

  function _extract() {
    if (!_state.discussionId) {
      _setStatus("Extraction error: Missing discussion context.");
      return;
    }

    _setStatus("Extracting items...");
    document.getElementById("trello-extract-btn").disabled = true;
    _api("POST", "/trello/" + _state.sessionId + "/extract/" + encodeURIComponent(_state.discussionId) + "/")
      .then(function (d) {
        _state.cards = (d.items && d.items.length) ? d.items : [_emptyCard()];
        _renderEditorCards();
        _setStatus("Extracted " + ((d.items || []).length) + " card(s).");
        document.getElementById("trello-extract-btn").disabled = false;
        _syncFooter();
      })
      .catch(function (err) {
        _setStatus("Extraction error: " + err.message);
        document.getElementById("trello-extract-btn").disabled = false;
      });
  }

  function _saveExport() {
    _state.cards = _collectCardsFromEditor();
    _setStatus("Saving export JSON...");
    _api("POST", "/trello/" + _state.sessionId + "/export/" + encodeURIComponent(_state.discussionId) + "/", {
      items: _state.cards,
      source: "manual",
    })
      .then(function (d) {
        _state.cards = (d.export && d.export.cards) || _state.cards;
        _renderEditorCards();
        _setStatus("Saved export JSON to discussion.");
        _syncFooter();
      })
      .catch(function (err) {
        _setStatus("Save error: " + err.message);
      });
  }

  function _push() {
    var listId = _getSelectedListId();
    _state.cards = _collectCardsFromEditor();
    if (!listId || !_state.cards.length) return;

    _setStatus("Exporting to Trello...");
    document.getElementById("trello-push-btn").disabled = true;

    _api("POST", "/trello/" + _state.sessionId + "/push/", {
      list_id: listId,
      discussion_id: _state.discussionId,
      items: _state.cards,
    })
      .then(function (d) {
        var count = (d.result || []).length;
        _setStatus("Exported " + count + " card(s) to Trello.");
        document.getElementById("trello-push-btn").disabled = false;

        var container = document.getElementById("trello-editor-cards");
        var html = '<div class="export-preview__success"><strong>Exported cards:</strong><ul>';
        (d.result || []).forEach(function (r) {
          var title = _esc(r.title);
          var warning = "";
          if (r.warnings && r.warnings.length) {
            warning = " <small>(warnings: " + _esc(r.warnings.join(" | ")) + ")</small>";
          }
          html += '<li><a href="' + _esc(r.url) + '" target="_blank" rel="noopener">' + title + '</a>' + warning + '</li>';
        });
        html += '</ul></div>';
        container.innerHTML = html;
      })
      .catch(function (err) {
        _setStatus("Export error: " + err.message);
        document.getElementById("trello-push-btn").disabled = false;
      });
  }

  function _parseChecklistLines(text) {
    var lines = (text || "").split(/\r?\n/);
    var out = [];
    lines.forEach(function (line) {
      var trimmed = line.trim();
      if (!trimmed) return;
      out.push({ title: trimmed, checked: false });
    });
    return out;
  }

  function _collectCardsFromEditor() {
    var cards = [];
    var root = document.getElementById("trello-editor-cards");
    if (!root) return cards;

    var rows = root.querySelectorAll(".trello-editor__card");
    rows.forEach(function (row) {
      var card = _emptyCard();
      card.card_title = (row.querySelector(".js-card-title") || {}).value || "";
      card.card_description = (row.querySelector(".js-card-description") || {}).value || "";
      card.priority = (row.querySelector(".js-card-priority") || {}).value || "";
      card.confidence_score = parseFloat((row.querySelector(".js-card-confidence") || {}).value || "0") || 0;

      var labelsRaw = ((row.querySelector(".js-card-labels") || {}).value || "").split(",");
      card.labels = labelsRaw.map(function (l) { return l.trim(); }).filter(Boolean);

      card.checklists = [];
      var checklistRows = row.querySelectorAll(".trello-editor__checklist");
      checklistRows.forEach(function (checklistRow) {
        var listName = (checklistRow.querySelector(".js-checklist-name") || {}).value || "Tasks";
        var listItems = _parseChecklistLines((checklistRow.querySelector(".js-checklist-items") || {}).value || "");
        if (listItems.length) {
          card.checklists.push({ name: listName.trim() || "Tasks", items: listItems });
        }
      });

      card.custom_fields = [];
      var customRows = row.querySelectorAll(".trello-editor__custom-field");
      customRows.forEach(function (customRow) {
        var fieldName = (customRow.querySelector(".js-custom-name") || {}).value || "";
        var fieldValue = (customRow.querySelector(".js-custom-value") || {}).value || "";
        if (fieldName.trim()) {
          card.custom_fields.push({
            field_name: fieldName.trim(),
            field_type: "text",
            value: fieldValue.trim(),
          });
        }
      });

      cards.push(card);
    });

    return cards;
  }

  function _renderEditorCards() {
    var container = document.getElementById("trello-editor-cards");
    if (!container) return;

    if (!_state.cards || !_state.cards.length) {
      _state.cards = [_emptyCard()];
    }

    var html = "";
    _state.cards.forEach(function (card, idx) {
      var checklists = card.checklists || [];
      var customFields = card.custom_fields || [];
      var labels = (card.labels || []).join(", ");

      html += '<div class="trello-editor__card">';
      html += '<div class="trello-editor__card-index">' + (idx + 1) + '</div>';
      html += '<div class="trello-editor__card-body">';
      html += '<div class="trello-editor__card-toolbar">';
      html += '<button type="button" class="btn btn--sm btn--success js-save-card" data-card-index="' + idx + '">Save Card</button>';
      html += '<button type="button" class="btn btn--sm btn--danger js-delete-card" data-card-index="' + idx + '">Delete</button>';
      html += '</div>';

      html += '<label>Card Title</label>';
      html += '<input type="text" class="input input--sm js-card-title" value="' + _esc(card.card_title || "") + '">';

      html += '<label>Description</label>';
      html += '<textarea class="input js-card-description" rows="3">' + _esc(card.card_description || "") + '</textarea>';

      html += '<div class="trello-editor__row">';
      html += '<div>';
      html += '<label>Labels (comma separated)</label>';
      html += '<input type="text" class="input input--sm js-card-labels" value="' + _esc(labels) + '">';
      html += '</div>';
      html += '<div>';
      html += '<label>Priority</label>';
      html += '<select class="input input--sm js-card-priority">';
      html += '<option value="">-</option>';
      ["Low", "Medium", "High", "Critical"].forEach(function (p) {
        var selected = (card.priority === p) ? ' selected' : '';
        html += '<option value="' + p + '"' + selected + '>' + p + '</option>';
      });
      html += '</select>';
      html += '</div>';
      html += '<div>';
      html += '<label>Confidence</label>';
      html += '<input type="number" min="0" max="1" step="0.01" class="input input--sm js-card-confidence" value="' + _esc(String(card.confidence_score || 0)) + '">';
      html += '</div>';
      html += '</div>';

      html += '<div class="trello-editor__subsection">';
      html += '<div class="trello-editor__subsection-header">';
      html += '<strong>Checklists</strong>';
      html += '<button type="button" class="btn btn--sm btn--secondary js-add-checklist" data-card-index="' + idx + '">Add Checklist</button>';
      html += '</div>';
      if (!checklists.length) {
        html += '<p class="form-hint">No checklists yet.</p>';
      }
      checklists.forEach(function (checklist) {
        var lines = (checklist.items || []).map(function (it) { return it.title || ""; }).join("\n");
        html += '<div class="trello-editor__checklist">';
        html += '<input type="text" class="input input--sm js-checklist-name" value="' + _esc(checklist.name || "Tasks") + '" placeholder="Checklist name">';
        html += '<textarea class="input js-checklist-items" rows="3" placeholder="One item per line">' + _esc(lines) + '</textarea>';
        html += '</div>';
      });
      html += '</div>';

      html += '<div class="trello-editor__subsection">';
      html += '<div class="trello-editor__subsection-header">';
      html += '<strong>Custom Fields</strong>';
      html += '<button type="button" class="btn btn--sm btn--secondary js-add-custom-field" data-card-index="' + idx + '">Add Field</button>';
      html += '</div>';
      if (!customFields.length) {
        html += '<p class="form-hint">No custom fields yet.</p>';
      }
      customFields.forEach(function (field) {
        html += '<div class="trello-editor__custom-field">';
        html += '<input type="text" class="input input--sm js-custom-name" placeholder="Field name" value="' + _esc(field.field_name || "") + '">';
        html += '<input type="text" class="input input--sm js-custom-value" placeholder="Value" value="' + _esc(field.value || "") + '">';
        html += '</div>';
      });
      html += '</div>';

      html += '</div>';
      html += '</div>';
    });

    container.innerHTML = html;
  }

  function openModal(sessionId, discussionId, secretKey, csrfToken) {
    _state = {
      sessionId: sessionId,
      discussionId: discussionId,
      secretKey: secretKey,
      csrfToken: csrfToken,
      cards: [],
    };

    var existing = document.getElementById("trello-export-overlay");
    if (existing) existing.remove();

    _createModal();
    _checkToken();
    _loadSavedExport();
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
