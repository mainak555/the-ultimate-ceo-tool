/**
 * jira_service_desk.js — Jira Service Desk provider wrapper.
 */

(function () {
  "use strict";

  function init() {
    if (!window.ProviderRegistry || !window.ExportModalBase || !window.JiraAdapterFactory) {
      return;
    }

    var adapter = window.JiraAdapterFactory.create({
      type: "service_desk",
      prefix: "jira-sd",
      label: "Jira Service Desk",
      itemLabel: "Request",
      itemLabelPlural: "Requests",
      destinationLabel: "Service Desk",
      loadingDestinationLabel: "service desks",
      extractedLabelPlural: "request(s)",
      pushedLabelPlural: "request(s)",
    });

    window.ProviderRegistry.register("jira_service_desk", {
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
