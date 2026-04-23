/**
 * config_readonly_markdown.js - Hydrates markdown blocks inside config readonly partial.
 */

(function () {
  "use strict";

  function renderReadonlyMarkdown(root) {
    var scope = root || document;
    if (!window.MarkdownViewer || typeof window.MarkdownViewer.render !== "function") {
      return;
    }

    var nodes = scope.querySelectorAll("[data-markdown]");
    nodes.forEach(function (node) {
      var source = node.getAttribute("data-markdown-source");
      if (source == null) {
        source = node.textContent || "";
        node.setAttribute("data-markdown-source", source);
      }
      node.innerHTML = window.MarkdownViewer.render(source);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderReadonlyMarkdown(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    var target = event && event.detail ? event.detail.target : null;
    if (!target) {
      return;
    }

    if (target.id === "main-content" || target.closest("#main-content")) {
      renderReadonlyMarkdown(target);
    }
  });
})();
