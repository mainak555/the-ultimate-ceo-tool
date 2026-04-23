/**
 * jira_software.js — Jira Software provider wrapper.
 *
 * Keeps provider registration type-owned while using the shared
 * JiraAdapterFactory for modal lifecycle behavior.
 */

(function () {
  "use strict";

  function init() {
    if (!window.ProviderRegistry || !window.ExportModalBase || !window.JiraAdapterFactory) {
      return;
    }

    var adapter = window.JiraAdapterFactory.create({
      type: "software",
      prefix: "jira-sw",
      label: "Jira Software",
      itemLabel: "Issue",
      itemLabelPlural: "Issues",
      destinationLabel: "Project",
      loadingDestinationLabel: "projects",
      extractedLabelPlural: "issue(s)",
      pushedLabelPlural: "issue(s)",
    });

    window.ProviderRegistry.register("jira_software", {
      openExportModal: function (ctx) {
        window.ExportModalBase.open(ctx, adapter);
      },
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
