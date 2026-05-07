/**
 * mermaid_viewer.js - Shared Mermaid hydration helper.
 *
 * Converts fenced code blocks rendered as:
 *   <pre><code class="language-mermaid">...</code></pre>
 * into Mermaid diagram nodes and renders them.
 */

(function () {
  "use strict";

  var _initialized = false;

  function _initMermaid() {
    if (_initialized) return true;
    if (!window.mermaid) return false;

    if (typeof window.mermaid.initialize === "function") {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
      });
    }
    _initialized = true;
    return true;
  }

  function _diagramSourceFromCode(codeNode) {
    return String((codeNode && codeNode.textContent) || "").trim();
  }

  function _replaceCodeBlock(codeNode) {
    if (!codeNode) return null;
    var pre = codeNode.closest("pre");
    if (!pre) return null;

    var source = _diagramSourceFromCode(codeNode);
    if (!source) {
      pre.setAttribute("data-mermaid-processed", "1");
      return null;
    }

    var host = document.createElement("div");
    host.className = "mermaid";
    host.textContent = source;
    host.setAttribute("data-mermaid-processed", "1");

    pre.replaceWith(host);
    return host;
  }

  function hydrate(root) {
    if (!_initMermaid()) return;

    var scope = root || document;
    if (!scope || !scope.querySelectorAll) return;

    var selector = [
      "pre:not([data-mermaid-processed]) > code.language-mermaid",
      "pre:not([data-mermaid-processed]) > code.lang-mermaid",
    ].join(",");

    var diagramNodes = [];
    scope.querySelectorAll(selector).forEach(function (codeNode) {
      var node = _replaceCodeBlock(codeNode);
      if (node) diagramNodes.push(node);
    });

    if (!diagramNodes.length) return;

    if (typeof window.mermaid.run === "function") {
      window.mermaid.run({ nodes: diagramNodes });
      return;
    }

    if (typeof window.mermaid.init === "function") {
      window.mermaid.init(undefined, diagramNodes);
    }
  }

  window.MermaidViewer = {
    hydrate: hydrate,
  };
})();