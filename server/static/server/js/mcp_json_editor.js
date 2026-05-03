(function () {
  var EDITOR_MAP = new Map();

  function isJsonEditorAvailable() {
    return typeof window.JSONEditor === "function";
  }

  function statusPayload(text) {
    var raw = (text || "").trim();
    if (!raw) {
      return { level: "neutral", message: "Empty JSON" };
    }
    try {
      JSON.parse(raw);
      return { level: "ok", message: "Valid JSON" };
    } catch (err) {
      return { level: "error", message: err.message };
    }
  }

  function setStatus(entry, payload) {
    if (!entry || !entry.statusEl) return;
    entry.statusEl.textContent = payload.message;
    entry.statusEl.classList.remove("mcp-json-editor__status--ok", "mcp-json-editor__status--error");
    if (payload.level === "ok") {
      entry.statusEl.classList.add("mcp-json-editor__status--ok");
    }
    if (payload.level === "error") {
      entry.statusEl.classList.add("mcp-json-editor__status--error");
    }
  }

  function syncTextarea(entry) {
    if (!entry || !entry.editor || !entry.textarea) return "";
    var value = entry.editor.getText();
    entry.textarea.value = value;
    return value;
  }

  function formatEntry(entry) {
    if (!entry || !entry.editor) return;
    var text = entry.editor.getText();
    var raw = (text || "").trim();
    if (!raw) {
      setStatus(entry, { level: "neutral", message: "Empty JSON" });
      return;
    }

    try {
      var parsed = JSON.parse(raw);
      var pretty = JSON.stringify(parsed, null, 2);
      entry.editor.updateText(pretty);
      entry.textarea.value = pretty;
      setStatus(entry, { level: "ok", message: "Formatted and valid" });
    } catch (err) {
      setStatus(entry, { level: "error", message: err.message });
    }
  }

  function validateEntry(entry) {
    if (!entry || !entry.editor) return;
    var text = syncTextarea(entry);
    setStatus(entry, statusPayload(text));
  }

  function createShell(textarea) {
    var shell = document.createElement("div");
    shell.className = "mcp-json-editor";

    var toolbar = document.createElement("div");
    toolbar.className = "mcp-json-editor__toolbar";

    var left = document.createElement("div");
    left.className = "mcp-json-editor__toolbar-left";

    var label = document.createElement("span");
    label.className = "mcp-json-editor__label";
    label.textContent = "JSON editor";

    var status = document.createElement("span");
    status.className = "mcp-json-editor__status";

    left.appendChild(label);
    left.appendChild(status);

    var right = document.createElement("div");
    right.className = "mcp-json-editor__toolbar-right";

    var formatBtn = document.createElement("button");
    formatBtn.type = "button";
    formatBtn.className = "btn btn--secondary btn--sm";
    formatBtn.textContent = "Format";

    var validateBtn = document.createElement("button");
    validateBtn.type = "button";
    validateBtn.className = "btn btn--secondary btn--sm";
    validateBtn.textContent = "Validate";

    right.appendChild(formatBtn);
    right.appendChild(validateBtn);

    toolbar.appendChild(left);
    toolbar.appendChild(right);

    var canvas = document.createElement("div");
    canvas.className = "mcp-json-editor__canvas";

    shell.appendChild(toolbar);
    shell.appendChild(canvas);

    textarea.insertAdjacentElement("afterend", shell);

    return {
      shell: shell,
      canvas: canvas,
      statusEl: status,
      formatBtn: formatBtn,
      validateBtn: validateBtn,
    };
  }

  function mountTextarea(textarea) {
    if (!textarea || EDITOR_MAP.has(textarea)) return;
    if (textarea.dataset.mcpJsonMounted === "1") return;
    if (!isJsonEditorAvailable()) return;

    var shellParts = createShell(textarea);

    var editor = new window.JSONEditor(shellParts.canvas, {
      mode: "code",
      modes: ["code", "text"],
      mainMenuBar: false,
      navigationBar: false,
      statusBar: true,
      onChangeText: function (text) {
        textarea.value = text;
        setStatus(entry, statusPayload(text));
      },
    });

    var initialText = textarea.value || "";
    if (initialText) {
      try {
        editor.setText(initialText);
      } catch (_err) {
        // Keep value as-is and show parse error status from validator.
      }
    }

    textarea.classList.add("mcp-json-editor__source");
    textarea.hidden = true;
    textarea.dataset.mcpJsonMounted = "1";

    var entry = {
      textarea: textarea,
      editor: editor,
      shell: shellParts.shell,
      statusEl: shellParts.statusEl,
      formatBtn: shellParts.formatBtn,
      validateBtn: shellParts.validateBtn,
    };

    shellParts.formatBtn.addEventListener("click", function () {
      formatEntry(entry);
    });

    shellParts.validateBtn.addEventListener("click", function () {
      validateEntry(entry);
    });

    EDITOR_MAP.set(textarea, entry);
    validateEntry(entry);
  }

  function pruneUnmounted() {
    EDITOR_MAP.forEach(function (entry, textarea) {
      if (textarea && textarea.isConnected) return;
      if (entry && entry.editor && typeof entry.editor.destroy === "function") {
        entry.editor.destroy();
      }
      if (entry && entry.shell && entry.shell.isConnected) {
        entry.shell.remove();
      }
      EDITOR_MAP.delete(textarea);
    });
  }

  function mountAll(root) {
    pruneUnmounted();
    if (!isJsonEditorAvailable()) return;

    var scope = root || document;
    scope.querySelectorAll(".js-shared-mcp-json, .js-mcp-dedicated-json").forEach(function (textarea) {
      mountTextarea(textarea);
    });
  }

  function syncAll() {
    pruneUnmounted();
    EDITOR_MAP.forEach(function (entry) {
      var value = syncTextarea(entry);
      setStatus(entry, statusPayload(value));
    });
  }

  function prepareForSubmit() {
    pruneUnmounted();
    EDITOR_MAP.forEach(function (entry) {
      var text = syncTextarea(entry);
      var raw = (text || "").trim();
      if (!raw) {
        setStatus(entry, { level: "neutral", message: "Empty JSON" });
        return;
      }
      try {
        var pretty = JSON.stringify(JSON.parse(raw), null, 2);
        entry.editor.updateText(pretty);
        entry.textarea.value = pretty;
        setStatus(entry, { level: "ok", message: "Formatted and valid" });
      } catch (err) {
        setStatus(entry, { level: "error", message: err.message });
      }
    });
  }

  window.McpJsonEditor = {
    mountAll: mountAll,
    syncAll: syncAll,
    prepareForSubmit: prepareForSubmit,
  };

  document.addEventListener("DOMContentLoaded", function () {
    mountAll(document);
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    mountAll(document);
  });
})();
