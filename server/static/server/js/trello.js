/**
 * trello.js — Trello export adapter.
 *
 * Implements the ExportModalBase adapter interface.
 * Registered as provider "trello" in window.ProviderRegistry.
 *
 * Depends on: export_modal_base.js, provider_registry.js
 */

(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Per-open state (reset in onOpen)
  // ---------------------------------------------------------------------------

  var _state = {};

  // ---------------------------------------------------------------------------
  // Internal helpers — read credentials from _state (set by onOpen)
  // ---------------------------------------------------------------------------

  function _headers() {
    return {
      "X-App-Secret-Key": _state.secretKey || "",
      "X-CSRFToken":      _state.csrfToken  || "",
      "Content-Type":     "application/json",
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

  // Thin wrappers so all internal functions stay unchanged
  function _setStatus(msg) {
    if (_state.baseAPI) _state.baseAPI.setStatus(msg);
  }

  function _syncFooter() {
    if (_state.baseAPI) _state.baseAPI.syncFooter();
  }

  // ---------------------------------------------------------------------------
  // Card utilities
  // ---------------------------------------------------------------------------

  function _emptyCard() {
    return {
      card_title:       "",
      card_description: "",
      checklists:   [{ name: "Tasks", items: [{ title: "", checked: false }] }],
      custom_fields: [],
      labels:        [],
      priority:      "",
      confidence_score: 0.0,
    };
  }

  function _parseChecklistLines(text) {
    return (text || "").split(/\r?\n/).reduce(function (acc, line) {
      var trimmed = line.trim();
      if (trimmed) acc.push({ title: trimmed, checked: false });
      return acc;
    }, []);
  }

  function _getSelectedListId() {
    var sel = document.getElementById("trello-list-select");
    var v   = sel ? sel.value : "";
    return (v && v !== "__new__") ? v : "";
  }

  function _syncCardCountBadge(count) {
    var badge = document.getElementById("trello-card-count");
    if (!badge) return;
    badge.textContent = String(Math.max(0, count || 0));
  }

  // ---------------------------------------------------------------------------
  // Card editor render / collect
  // ---------------------------------------------------------------------------

  function _renderEditorCards() {
    var container = document.getElementById("trello-editor-cards");
    if (!container) return;

    if (!_state.cards || !_state.cards.length) {
      _state.cards = [_emptyCard()];
    }

    _syncCardCountBadge((_state.cards || []).length);

    var html = "";
    _state.cards.forEach(function (card, idx) {
      var checklists   = card.checklists   || [];
      var customFields = card.custom_fields || [];
      var labels       = (card.labels || []).join(", ");

      html += '<div class="trello-editor__card">';
      html += '<div class="trello-editor__card-body">';

      html += '<div class="trello-editor__card-title-row">';
      html += '<label>Card Title</label>';
      html += '<button type="button" class="btn btn--danger btn--xs trello-editor__card-remove js-delete-card"'
            + ' data-card-index="' + idx + '" title="Remove card" aria-label="Remove card">\u2715</button>';
      html += '</div>';
      html += '<input type="text" class="input input--sm js-card-title" value="' + _esc(card.card_title || "") + '">';

      html += '<label>Description</label>';
      html += '<textarea class="input js-card-description" rows="3">' + _esc(card.card_description || "") + '</textarea>';

      html += '<div class="trello-editor__row">';
      html += '<div><label>Labels (comma separated)</label>'
            + '<input type="text" class="input input--sm js-card-labels" value="' + _esc(labels) + '"></div>';
      html += '<div><label>Priority</label>'
            + '<select class="input input--sm js-card-priority">'
            + '<option value="">-</option>';
      ["Low", "Medium", "High", "Critical"].forEach(function (p) {
        var sel = (card.priority === p) ? ' selected' : '';
        html += '<option value="' + p + '"' + sel + '>' + p + '</option>';
      });
      html += '</select></div>';
      html += '<div><label>Confidence</label>'
            + '<input type="number" min="0" max="1" step="0.01" class="input input--sm js-card-confidence"'
            + ' value="' + _esc(String(card.confidence_score || 0)) + '"></div>';
      html += '</div>'; // .trello-editor__row

      // Checklists
      html += '<div class="trello-editor__subsection">';
      html += '<div class="trello-editor__subsection-header"><strong>Checklists</strong>'
            + '<button type="button" class="btn btn--sm btn--primary js-add-checklist"'
            + ' data-card-index="' + idx + '">Add Checklist</button></div>';
      if (!checklists.length) html += '<p class="form-hint">No checklists yet.</p>';
      checklists.forEach(function (cl, ci) {
        var lines = (cl.items || []).map(function (it) { return it.title || ""; }).join("\n");
        html += '<div class="trello-editor__checklist">';
        html += '<div class="trello-editor__checklist-head">'
              + '<input type="text" class="input input--sm js-checklist-name"'
              + ' value="' + _esc(cl.name || "Tasks") + '" placeholder="Checklist name">'
              + '<button type="button" class="chat-session-item__delete js-delete-checklist"'
              + ' data-card-index="' + idx + '" data-checklist-index="' + ci + '"'
              + ' title="Remove checklist">&times;</button>'
              + '</div>';
        html += '<textarea class="input js-checklist-items" rows="3"'
              + ' placeholder="One item per line">' + _esc(lines) + '</textarea>';
        html += '</div>';
      });
      html += '</div>';

      // Custom fields
      html += '<div class="trello-editor__subsection">';
      html += '<div class="trello-editor__subsection-header"><strong>Custom Fields</strong>'
            + '<button type="button" class="btn btn--sm btn--primary js-add-custom-field"'
            + ' data-card-index="' + idx + '">Add Field</button></div>';
      if (!customFields.length) html += '<p class="form-hint">No custom fields yet.</p>';
      customFields.forEach(function (field, fi) {
        html += '<div class="trello-editor__custom-field">'
              + '<input type="text" class="input input--sm js-custom-name" placeholder="Field name"'
              + ' value="' + _esc(field.field_name || "") + '">'
              + '<input type="text" class="input input--sm js-custom-value" placeholder="Value"'
              + ' value="' + _esc(field.value || "") + '">'
              + '<button type="button" class="chat-session-item__delete js-delete-custom-field"'
              + ' data-card-index="' + idx + '" data-custom-index="' + fi + '"'
              + ' title="Remove field">&times;</button>'
              + '</div>';
      });
      html += '</div>';

      html += '</div>'; // .trello-editor__card-body
      html += '</div>'; // .trello-editor__card
    });

    container.innerHTML = html;
  }

  function _collectCardsFromEditor() {
    var cards = [];
    var root  = document.getElementById("trello-editor-cards");
    if (!root) return cards;

    root.querySelectorAll(".trello-editor__card").forEach(function (row) {
      var card       = _emptyCard();
      card.card_title       = (row.querySelector(".js-card-title")       || {}).value || "";
      card.card_description = (row.querySelector(".js-card-description") || {}).value || "";
      card.priority         = (row.querySelector(".js-card-priority")    || {}).value || "";
      card.confidence_score = parseFloat((row.querySelector(".js-card-confidence") || {}).value || "0") || 0;

      var labelsRaw = ((row.querySelector(".js-card-labels") || {}).value || "").split(",");
      card.labels = labelsRaw.map(function (l) { return l.trim(); }).filter(Boolean);

      card.checklists = [];
      row.querySelectorAll(".trello-editor__checklist").forEach(function (clRow) {
        var name  = (clRow.querySelector(".js-checklist-name")  || {}).value || "Tasks";
        var items = _parseChecklistLines((clRow.querySelector(".js-checklist-items") || {}).value || "");
        card.checklists.push({ name: name.trim() || "Tasks", items: items });
      });

      card.custom_fields = [];
      row.querySelectorAll(".trello-editor__custom-field").forEach(function (cfRow) {
        card.custom_fields.push({
          field_name: (cfRow.querySelector(".js-custom-name")  || {}).value || "",
          field_type: "text",
          value:      (cfRow.querySelector(".js-custom-value") || {}).value || "",
        });
      });

      cards.push(card);
    });

    return cards;
  }

  // ---------------------------------------------------------------------------
  // Export summary (locked state display)
  // ---------------------------------------------------------------------------

  function _renderExportSummary(pushResult, cards) {
    var container = document.getElementById("trello-editor-cards");
    if (!container) return;

    var list = [];
    if (Array.isArray(pushResult) && pushResult.length) {
      list = pushResult.map(function (row) {
        return { title: (row && row.title) ? row.title : "Untitled", url: (row && row.url) ? row.url : "", warnings: Array.isArray(row && row.warnings) ? row.warnings : [] };
      });
    } else if (Array.isArray(cards) && cards.length) {
      list = cards.map(function (card) {
        return { title: (card && card.card_title) ? card.card_title : "Untitled", url: "", warnings: [] };
      });
    }

    _syncCardCountBadge(list.length || ((cards && cards.length) || 0));

    var html = '<div class="export-preview__success"><strong>Exported cards:</strong><ul>';
    if (!list.length) html += "<li>(no exported cards)</li>";
    list.forEach(function (item) {
      var title   = _esc(item.title);
      var warning = item.warnings.length ? ' <small>(warnings: ' + _esc(item.warnings.join(" | ")) + ')</small>' : "";
      html += item.url
        ? '<li><a href="' + _esc(item.url) + '" target="_blank" rel="noopener">' + title + '</a>' + warning + '</li>'
        : '<li>' + title + warning + '</li>';
    });
    html += '</ul></div>';
    container.innerHTML = html;
  }

  // ---------------------------------------------------------------------------
  // API proxy calls
  // ---------------------------------------------------------------------------

  function _checkToken() {
    _api("GET", "/trello/" + _state.sessionId + "/token-status/")
      .then(function (d) {
        var statusEl = document.getElementById("trello-token-status");
        if (!statusEl) return;
        if (d.valid) {
          statusEl.innerHTML =
            '<span class="export-modal__token-status export-modal__token-status--valid">Authorized</span>'
            + (d.token_generated_at ? ' <small>(configured <time class="local-time" data-utc="' + d.token_generated_at + '">' + d.token_generated_at + '</time>)</small>' : '');
          if (d.defaults) _state.defaults = d.defaults;
          _showDestination();
        } else {
          statusEl.textContent = "Not authorized. Configure Trello token in Project Settings.";
        }
      })
      .catch(function (err) {
        var el = document.getElementById("trello-token-status");
        if (el) el.textContent = "Error: " + err.message;
      });
  }

  function _showDestination() {
    var section = document.getElementById("trello-destination-section");
    if (section) section.hidden = false;
    _loadWorkspaces();
  }

  function _loadWorkspaces() {
    var sel      = document.getElementById("trello-workspace-select");
    if (!sel) return;
    sel.innerHTML = '<option value="">Loading\u2026</option>';
    var defaultWs = (_state.defaults && _state.defaults.default_workspace_id) || "";
    _api("GET", "/trello/" + _state.sessionId + "/workspaces/")
      .then(function (list) {
        var html = '<option value="">(All / Personal)</option>';
        list.forEach(function (w) {
          html += '<option value="' + _esc(w.id) + '"' + (w.id === defaultWs ? ' selected' : '') + '>'
                + _esc(w.displayName) + '</option>';
        });
        sel.innerHTML = html;
        _loadBoards(sel.value);
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  function _loadBoards(workspaceId) {
    var sel = document.getElementById("trello-board-select");
    if (!sel) return;
    sel.innerHTML = '<option value="">Loading\u2026</option>';
    var url          = "/trello/" + _state.sessionId + "/boards/" + (workspaceId ? "?workspace=" + encodeURIComponent(workspaceId) : "");
    var defaultBoard = (_state.defaults && _state.defaults.default_board_id) || "";
    _api("GET", url)
      .then(function (list) {
        var html = '<option value="">- Select Board -</option>';
        list.forEach(function (b) {
          html += '<option value="' + _esc(b.id) + '"' + (b.id === defaultBoard ? ' selected' : '') + '>'
                + _esc(b.name) + '</option>';
        });
        html += '<option value="__new__">Create New Board</option>';
        sel.innerHTML = html;
        var createDiv = document.getElementById("trello-create-board");
        if (createDiv) createDiv.hidden = true;
        if (defaultBoard && sel.value === defaultBoard) {
          _loadLists(defaultBoard);
        } else {
          var listSel = document.getElementById("trello-list-select");
          if (listSel) listSel.innerHTML = '<option value="">-</option>';
        }
        _syncFooter();
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  function _loadLists(boardId) {
    var sel = document.getElementById("trello-list-select");
    if (!sel) return;
    sel.innerHTML = '<option value="">Loading\u2026</option>';
    var defaultList = (_state.defaults && _state.defaults.default_list_id) || "";
    _api("GET", "/trello/" + _state.sessionId + "/lists/?board=" + encodeURIComponent(boardId))
      .then(function (list) {
        var html = '<option value="">- Select List -</option>';
        list.forEach(function (l) {
          html += '<option value="' + _esc(l.id) + '"' + (l.id === defaultList ? ' selected' : '') + '>'
                + _esc(l.name) + '</option>';
        });
        html += '<option value="__new__">Create New List</option>';
        sel.innerHTML = html;
        var createDiv = document.getElementById("trello-create-list");
        if (createDiv) createDiv.hidden = true;
        _syncFooter();
      })
      .catch(function (err) { sel.innerHTML = '<option value="">Error</option>'; _setStatus(err.message); });
  }

  function _loadSavedExport() {
    if (!_state.discussionId) return;
    _setStatus("Loading saved export\u2026");
    _api("GET", "/trello/" + _state.sessionId + "/export/" + encodeURIComponent(_state.discussionId) + "/")
      .then(function (data) {
        var payload = data.export || {};
        var cards   = payload.cards || [];
        _state.cards           = cards.length ? cards : [_emptyCard()];
        _state.lastPushResult  = ((payload.last_push || {}).result) || [];
        _state.exported        = !!payload.exported;

        if (_state.exported) {
          _renderExportSummary(_state.lastPushResult, _state.cards);
          _setStatus("Already exported to Trello. Click Extract Items to unlock editing.");
        } else {
          _renderEditorCards();
          _setStatus(data.saved ? "Loaded saved export JSON." : "No saved JSON found. You can extract or edit manually.");
        }
        _syncFooter();
      })
      .catch(function (err) {
        _state.exported       = false;
        _state.lastPushResult = [];
        _state.cards          = [_emptyCard()];
        _renderEditorCards();
        _setStatus("Load error: " + err.message);
      });
  }

  function _extract() {
    if (!_state.discussionId) { _setStatus("Extraction error: Missing discussion context."); return; }
    _setStatus("Extracting items\u2026");
    var extractBtn = document.getElementById("export-modal-extract-btn");
    if (extractBtn) extractBtn.disabled = true;

    _api("POST", "/trello/" + _state.sessionId + "/extract/" + encodeURIComponent(_state.discussionId) + "/")
      .then(function (d) {
        var extracted  = d.items || [];
        _state.exported       = false;
        _state.lastPushResult = [];
        _state.cards          = extracted.length ? extracted : [_emptyCard()];
        _renderEditorCards();
        _setStatus("Extracted " + extracted.length + " card(s). Editing unlocked.");
        if (extractBtn) extractBtn.disabled = false;
        _syncFooter();
      })
      .catch(function (err) {
        _setStatus("Extraction error: " + err.message);
        if (extractBtn) extractBtn.disabled = false;
      });
  }

  function _saveExport() {
    if (_state.exported) { _setStatus("Export is locked. Click Extract Items to unlock editing."); return; }
    _state.cards = _collectCardsFromEditor();
    _setStatus("Saving export JSON\u2026");

    _api("POST", "/trello/" + _state.sessionId + "/export/" + encodeURIComponent(_state.discussionId) + "/", {
      items:  _state.cards,
      source: "manual",
    })
      .then(function (d) {
        _state.cards          = (d.export && d.export.cards) || _state.cards;
        _state.exported       = !!(d.export && d.export.exported);
        _state.lastPushResult = [];
        _renderEditorCards();
        _setStatus("Saved export JSON to discussion.");
        _syncFooter();
      })
      .catch(function (err) { _setStatus("Save error: " + err.message); });
  }

  function _push() {
    if (_state.exported) { _setStatus("Already exported. Click Extract Items to prepare a new export."); return; }
    var listId = _getSelectedListId();
    _state.cards = _collectCardsFromEditor();
    if (!listId || !_state.cards.length) return;

    _setStatus("Exporting to Trello\u2026");
    var pushBtn = document.getElementById("export-modal-push-btn");
    if (pushBtn) pushBtn.disabled = true;

    _api("POST", "/trello/" + _state.sessionId + "/push/", {
      list_id:      listId,
      discussion_id: _state.discussionId,
      items:        _state.cards,
    })
      .then(function (d) {
        var count          = (d.result || []).length;
        _state.exported       = true;
        _state.lastPushResult = d.result || [];
        _renderExportSummary(_state.lastPushResult, _state.cards);
        _syncFooter();
        _setStatus("Exported " + count + " card(s) to Trello.");
        if (pushBtn) pushBtn.disabled = false;
      })
      .catch(function (err) {
        _setStatus("Export error: " + err.message);
        if (pushBtn) pushBtn.disabled = false;
      });
  }

  // ---------------------------------------------------------------------------
  // Left-pane event binding (called from onOpen after DOM is ready)
  // ---------------------------------------------------------------------------

  function _bindLeftPaneEvents() {
    // Workspace cascade
    var wsSel = document.getElementById("trello-workspace-select");
    if (wsSel) wsSel.addEventListener("change", function () { _loadBoards(this.value); });

    // Board cascade
    var boardSel = document.getElementById("trello-board-select");
    if (boardSel) boardSel.addEventListener("change", function () {
      var v         = this.value;
      var createDiv = document.getElementById("trello-create-board");
      if (createDiv) createDiv.hidden = (v !== "__new__");
      if (v && v !== "__new__") {
        _loadLists(v);
      } else {
        var listSel = document.getElementById("trello-list-select");
        if (listSel) listSel.innerHTML = '<option value="">-</option>';
      }
      _syncFooter();
    });

    // List cascade
    var listSel = document.getElementById("trello-list-select");
    if (listSel) listSel.addEventListener("change", function () {
      var createDiv = document.getElementById("trello-create-list");
      if (createDiv) createDiv.hidden = (this.value !== "__new__");
      _syncFooter();
    });

    // Create board inline
    var createBoardDiv = document.getElementById("trello-create-board");
    if (createBoardDiv) {
      var createBoardBtn = createBoardDiv.querySelector("button");
      if (createBoardBtn) createBoardBtn.addEventListener("click", function () {
        var input = createBoardDiv.querySelector("input");
        var name  = input ? input.value.trim() : "";
        if (!name) return;
        var wsId = (document.getElementById("trello-workspace-select") || {}).value || undefined;
        _setStatus("Creating board\u2026");
        _api("POST", "/trello/" + _state.sessionId + "/create-board/", { name: name, workspace_id: wsId })
          .then(function (board) {
            createBoardDiv.hidden = true;
            if (input) input.value = "";
            var sel = document.getElementById("trello-board-select");
            if (sel) sel.add(new Option(board.name, board.id, true, true), sel.length - 1);
            _setStatus("");
            _loadLists(board.id);
          })
          .catch(function (err) { _setStatus("Error: " + err.message); });
      });
    }

    // Create list inline
    var createListDiv = document.getElementById("trello-create-list");
    if (createListDiv) {
      var createListBtn = createListDiv.querySelector("button");
      if (createListBtn) createListBtn.addEventListener("click", function () {
        var input   = createListDiv.querySelector("input");
        var name    = input ? input.value.trim() : "";
        var boardId = (document.getElementById("trello-board-select") || {}).value || "";
        if (!name || !boardId || boardId === "__new__") return;
        _setStatus("Creating list\u2026");
        _api("POST", "/trello/" + _state.sessionId + "/create-list/", { name: name, board_id: boardId })
          .then(function (list) {
            createListDiv.hidden = true;
            if (input) input.value = "";
            var sel = document.getElementById("trello-list-select");
            if (sel) sel.add(new Option(list.name, list.id, true, true), sel.length - 1);
            _setStatus("");
            _syncFooter();
          })
          .catch(function (err) { _setStatus("Error: " + err.message); });
      });
    }

    // Add card
    var addCardBtn = document.getElementById("trello-add-card-btn");
    if (addCardBtn) addCardBtn.addEventListener("click", function () {
      if (_state.exported) { _setStatus("Export is locked. Click Extract Items to unlock editing."); return; }
      _state.cards.push(_emptyCard());
      _renderEditorCards();
      _syncFooter();
    });

    // Card-level actions (delete, add checklist, add custom field, delete checklist, delete custom field)
    var editorCards = document.getElementById("trello-editor-cards");
    if (editorCards) {
      editorCards.addEventListener("click", function (e) {
        if (_state.exported) { _setStatus("Export is locked. Click Extract Items to unlock editing."); return; }
        var btn = e.target.closest("button");
        if (!btn) return;

        var cardIndex = parseInt(btn.getAttribute("data-card-index") || "-1", 10);
        if (cardIndex < 0 || cardIndex >= _state.cards.length) return;

        // Preserve in-progress edits first
        _state.cards = _collectCardsFromEditor();

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
        if (btn.classList.contains("js-delete-checklist")) {
          var ci = parseInt(btn.getAttribute("data-checklist-index") || "-1", 10);
          if (ci >= 0) { _state.cards[cardIndex].checklists.splice(ci, 1); _renderEditorCards(); _syncFooter(); }
          return;
        }
        if (btn.classList.contains("js-delete-custom-field")) {
          var fi = parseInt(btn.getAttribute("data-custom-index") || "-1", 10);
          if (fi >= 0) { _state.cards[cardIndex].custom_fields.splice(fi, 1); _renderEditorCards(); _syncFooter(); }
          return;
        }
      });

      editorCards.addEventListener("input", _syncFooter);
    }
  }

  // ---------------------------------------------------------------------------
  // Adapter — left-pane HTML
  // ---------------------------------------------------------------------------

  function _renderLeftPane() {
    return ''
      // Auth status
      + '<div class="export-modal__section" id="trello-token-section">'
      + '<h4>Authorization</h4>'
      + '<div id="trello-token-status">Checking token\u2026</div>'
      + '</div>'

      // Destination cascade
      + '<div class="export-modal__section" id="trello-destination-section" hidden>'
      + '<h4>Destination</h4>'
      + '<div class="cascade-select">'
      + '<div class="cascade-select__group"><label>Workspace</label>'
      + '<select id="trello-workspace-select" class="input input--sm"><option value="">Loading\u2026</option></select>'
      + '</div>'
      + '<div class="cascade-select__group"><label>Board</label>'
      + '<select id="trello-board-select" class="input input--sm"><option value="">-</option></select>'
      + '<div id="trello-create-board" class="cascade-select__create-new" hidden>'
      + '<input type="text" class="input input--sm" placeholder="New board name">'
      + '<button type="button" class="btn btn--sm btn--success">Create</button>'
      + '</div>'
      + '</div>'
      + '<div class="cascade-select__group"><label>List</label>'
      + '<select id="trello-list-select" class="input input--sm"><option value="">-</option></select>'
      + '<div id="trello-create-list" class="cascade-select__create-new" hidden>'
      + '<input type="text" class="input input--sm" placeholder="New list name">'
      + '<button type="button" class="btn btn--sm btn--success">Create</button>'
      + '</div>'
      + '</div>'
      + '</div>'
      + '</div>'

      // Card editor
      + '<div class="export-modal__section" id="trello-workspace-section">'
      + '<div class="trello-editor__section-head">'
      + '<h4>Cards <span class="export-modal__count-badge" id="trello-card-count">0</span></h4>'
      + '<button type="button" class="btn btn--sm btn--primary export-modal__context-add-btn" id="trello-add-card-btn">Add Card</button>'
      + '</div>'
      + '<div class="trello-editor__section-divider"></div>'
      + '<div id="trello-editor-cards" class="trello-editor__cards"></div>'
      + '</div>';
  }

  // ---------------------------------------------------------------------------
  // TrelloAdapter — the adapter object registered with ExportModalBase
  // ---------------------------------------------------------------------------

  var TrelloAdapter = {
    label: "Trello",

    referenceUrl: function (ctx) {
      if (!ctx.sessionId || !ctx.discussionId) return null;
      return "/trello/" + ctx.sessionId + "/reference/" + encodeURIComponent(ctx.discussionId) + "/";
    },

    renderLeftPane: function (ctx) {
      return _renderLeftPane(ctx);
    },

    onOpen: function (ctx, baseAPI) {
      _state = {
        sessionId:        ctx.sessionId    || "",
        discussionId:     ctx.discussionId || "",
        secretKey:        ctx.secretKey    || "",
        csrfToken:        ctx.csrfToken    || "",
        cards:            [],
        exported:         false,
        lastPushResult:   [],
        defaults:         {},
        baseAPI:          baseAPI,
      };
      _bindLeftPaneEvents();
      _checkToken();
      _loadSavedExport();
    },

    onExtract: function (ctx, baseAPI) { _extract(); },
    onSave:    function (ctx, baseAPI) { _saveExport(); },
    onPush:    function (ctx, baseAPI) { _push(); },

    syncFooter: function (ctx, baseAPI) {
      var listId     = _getSelectedListId();
      var hasCards   = !!(_state.cards && _state.cards.length);
      var isExported = !!_state.exported;

      // Manage left-pane–owned element
      var addCardBtn = document.getElementById("trello-add-card-btn");
      if (addCardBtn) addCardBtn.disabled = isExported;

      return {
        extractHidden:   !(_state.discussionId),
        extractDisabled: false,
        saveDisabled:    isExported,
        pushHidden:      !hasCards && !isExported,
        pushDisabled:    isExported || !hasCards || !listId,
      };
    },
  };

  // ---------------------------------------------------------------------------
  // ProviderRegistry registration
  // ---------------------------------------------------------------------------

  function _init() {
    if (!window.ProviderRegistry || !window.ExportModalBase) return;
    window.ProviderRegistry.register("trello", {
      openExportModal: function (ctx) {
        window.ExportModalBase.open(ctx, TrelloAdapter);
      },
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _init);
  } else {
    _init();
  }
})();
