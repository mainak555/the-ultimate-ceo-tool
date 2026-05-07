/**
 * chat_surface_utils.js - Shared chat rendering helpers for Home/Remote/Guest.
 *
 * Scope:
 *   - Attachment chip/message attachment HTML helpers
 *   - File-size formatting and file-icon lookup
 *   - Shared scroll + history-container helpers
 *   - Local-time rendering shim used by dynamic bubble append paths
 *
 * Not in scope:
 *   - Run-state/gate/quorum workflow logic
 *   - Page-specific composer behavior
 */
(function () {
  "use strict";

  var FILE_ICON_EXTS = {
    pdf: 1, doc: 1, docx: 1, xls: 1, xlsx: 1, ppt: 1, pptx: 1,
    csv: 1, txt: 1, json: 1, xml: 1, md: 1,
  };

  function _escape(text) {
    if (window.MarkdownViewer && typeof window.MarkdownViewer.escapeHtml === "function") {
      return window.MarkdownViewer.escapeHtml(text || "");
    }
    var value = String(text || "");
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function _safeUrl(url, allowDataProtocol) {
    var raw = String(url || "").trim();
    if (!raw) return "";
    try {
      var parsed = new URL(raw, window.location.origin);
      var proto = (parsed.protocol || "").toLowerCase();
      var allowData = !!allowDataProtocol;
      if (proto !== "http:" && proto !== "https:" && proto !== "blob:" && !(allowData && proto === "data:")) {
        return "";
      }
      // Preserve relative paths where possible for cleaner markup.
      if (raw.charAt(0) === "/") {
        return raw;
      }
      return parsed.href;
    } catch (_) {
      return "";
    }
  }

  function formatBytes(size) {
    var n = Number(size || 0);
    if (!n) return "0 B";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function iconUrlForFile(filename) {
    var ext = (filename || "").split(".").pop().toLowerCase();
    var name = FILE_ICON_EXTS[ext] ? ext : "document";
    return "/static/server/assets/icons/file-" + name + ".svg";
  }

  function renderAttachmentChip(att, target, index) {
    var name = _escape(att && att.filename ? att.filename : "file");
    var safeUrl = _safeUrl(att && att.content_url ? att.content_url : "", false);
    var iconCls = att && att.is_image
      ? "chat-attachment-chip__thumb"
      : "chat-attachment-chip__thumb chat-attachment-chip__thumb--icon";
    var thumbUrl = _safeUrl(att && att.thumbnail_url ? att.thumbnail_url : "", true);
    var thumb = thumbUrl
      ? '<img class="' + iconCls + '" src="' + _escape(thumbUrl) + '" alt="' + name + '">'
      : "";
    var openTag = safeUrl
      ? '<a class="chat-attachment-chip__file" href="' + _escape(safeUrl) + '" target="_blank" rel="noopener noreferrer">'
      : '<span class="chat-attachment-chip__file">';
    var closeTag = safeUrl ? "</a>" : "</span>";

    return '<div class="chat-attachment-chip">'
      + thumb
      + openTag
      + '<span class="chat-attachment-chip__name">' + name + "</span>"
      + '<span class="chat-attachment-chip__meta">' + formatBytes(att && att.size_bytes) + "</span>"
      + closeTag
      + '<button class="chat-attachment-chip__remove" type="button" data-attachment-target="' + _escape(target || "") + '" data-attachment-index="' + Number(index || 0) + '">&#x00D7;</button>'
      + "</div>";
  }

  function renderMessageAttachments(attachments, options) {
    var list = attachments || [];
    if (!list.length) return "";
    var opts = options || {};
    var allowFallbackIcon = opts.fallbackIcon !== false;
    var allowFallbackImage = opts.fallbackImage !== false;

    var html = '<div class="chat-message-attachments">';
    list.forEach(function (att) {
      var name = _escape(att && att.filename ? att.filename : "file");
      var url = _safeUrl(att && att.content_url ? att.content_url : "", false);
      var iconCls = att && att.is_image
        ? "chat-message-attachment__thumb"
        : "chat-message-attachment__thumb chat-message-attachment__thumb--icon";

      var thumbUrl = _safeUrl(att && att.thumbnail_url ? att.thumbnail_url : "", true);
      if (!thumbUrl && allowFallbackImage && att && att.is_image && url) {
        thumbUrl = url;
      }
      if (!thumbUrl && allowFallbackIcon && (!att || !att.is_image)) {
        thumbUrl = iconUrlForFile(att && att.filename ? att.filename : "");
      }
      var thumb = thumbUrl
        ? '<img class="' + iconCls + '" src="' + _escape(thumbUrl) + '" alt="' + name + '">'
        : "";

      if (url) {
        html += '<a class="chat-message-attachment" href="' + _escape(url) + '" target="_blank" rel="noopener noreferrer">'
          + thumb
          + '<span class="chat-message-attachment__name">' + name + "</span>"
          + "</a>";
      } else {
        html += '<span class="chat-message-attachment">'
          + thumb
          + '<span class="chat-message-attachment__name">' + name + "</span>"
          + "</span>";
      }
    });
    html += "</div>";
    return html;
  }

  function scrollToBottom(containerOrSelector) {
    var container = typeof containerOrSelector === "string"
      ? document.querySelector(containerOrSelector)
      : containerOrSelector;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }

  function getOrCreateHistoryContainer(containerId, historyId) {
    var container = document.getElementById(containerId);
    if (!container) return null;
    var history = document.getElementById(historyId);
    if (history) return history;
    history = document.createElement("div");
    history.className = "chat-history";
    history.id = historyId;
    container.insertAdjacentElement("afterbegin", history);
    return history;
  }

  function renderLocalTimes() {
    if (typeof window.renderLocalTimes === "function") {
      window.renderLocalTimes();
      return;
    }
    document.querySelectorAll(".local-time[data-utc]:not([data-rendered])").forEach(function (el) {
      var d = new Date(el.dataset.utc);
      if (!isNaN(d.getTime())) {
        el.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        el.dataset.rendered = "1";
      }
    });
  }

  window.ChatSurfaceUtils = {
    formatBytes: formatBytes,
    iconUrlForFile: iconUrlForFile,
    renderAttachmentChip: renderAttachmentChip,
    renderMessageAttachments: renderMessageAttachments,
    scrollToBottom: scrollToBottom,
    getOrCreateHistoryContainer: getOrCreateHistoryContainer,
    renderLocalTimes: renderLocalTimes,
  };
})();
