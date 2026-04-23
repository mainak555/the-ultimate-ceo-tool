/**
 * jira_business.js — Jira Business provider wrapper.
 */

(function () {
  "use strict";

  function init() {
    if (!window.ProviderRegistry || !window.ExportModalBase || !window.JiraAdapterFactory) {
      return;
    }

    var adapter = window.JiraAdapterFactory.create({
      type: "business",
      prefix: "jira-biz",
      label: "Jira Business",
      itemLabel: "Task",
      itemLabelPlural: "Tasks",
      destinationLabel: "Project",
      loadingDestinationLabel: "projects",
      extractedLabelPlural: "task(s)",
      pushedLabelPlural: "task(s)",
    });

    window.ProviderRegistry.register("jira_business", {
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
