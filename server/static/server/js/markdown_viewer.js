/**
 * markdown_viewer.js - Shared markdown-to-HTML helper.
 *
 * Scope:
 *   - Cross-feature markdown rendering utility
 *   - Home chat bubbles, Trello reference pane, and future providers
 */

(function () {
  "use strict";

  function _escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function render(markdownText) {
    var text = markdownText == null ? "" : String(markdownText);

    if (typeof marked !== "undefined" && typeof marked.parse === "function") {
      if (typeof marked.setOptions === "function") {
        marked.setOptions({
          gfm: true,
          breaks: true,
        });
      }
      return marked.parse(text);
    }

    return "<pre>" + _escapeHtml(text) + "</pre>";
  }

  window.MarkdownViewer = {
    escapeHtml: _escapeHtml,
    render: render,
  };
})();
