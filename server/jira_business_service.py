"""Jira Business type-specific service helpers."""

import logging

from . import jira_client

logger = logging.getLogger(__name__)


from core.tracing import traced_function


def fetch_spaces(site_url, email, api_key):
    """Return Jira Business projects for the configured credentials."""
    return jira_client.get_projects(site_url, email, api_key, type_key="business")


def normalize_item(item, normalize_labels, coerce_confidence):
    """Normalize one Jira Business task payload."""
    summary = str(item.get("summary") or item.get("card_title") or "").strip() or "Untitled"
    description = str(item.get("description") or item.get("card_description") or "").strip()
    issue_type = str(item.get("issue_type") or "Task").strip() or "Task"
    priority = str(item.get("priority") or "").strip()
    labels = normalize_labels(item.get("labels"))
    due_date = str(item.get("due_date") or "").strip()
    category = str(item.get("category") or "").strip()

    return {
        "summary": summary,
        "description": description,
        "issue_type": issue_type,
        "priority": priority,
        "labels": labels,
        "due_date": due_date,
        "category": category,
        "confidence_score": coerce_confidence(item.get("confidence_score", 0.0)),
    }


@traced_function("service.jira.business.push_issues")
def push_issues(site_url, email, api_key, project_key, normalized_items):
    """Push normalized Jira Business items to Jira."""
    return jira_client.push_issues_business(site_url, email, api_key, project_key, normalized_items)
