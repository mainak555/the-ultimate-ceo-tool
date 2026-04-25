"""Jira Service Desk type-specific service helpers."""

import logging

from . import jira_client

logger = logging.getLogger(__name__)


from core.tracing import traced_function


def fetch_spaces(site_url, email, api_key):
    """Return Jira Service Desk spaces for the configured credentials."""
    return jira_client.get_service_desks(site_url, email, api_key)


def normalize_item(item, normalize_labels, coerce_confidence):
    """Normalize one Jira Service Desk request payload."""
    summary = str(item.get("summary") or item.get("card_title") or "").strip() or "Untitled"
    description = str(item.get("description") or item.get("card_description") or "").strip()
    request_type = str(item.get("request_type") or "").strip()
    priority = str(item.get("priority") or "").strip()
    labels = normalize_labels(item.get("labels"))
    impact = str(item.get("impact") or "").strip()
    urgency = str(item.get("urgency") or "").strip()

    return {
        "summary": summary,
        "description": description,
        "request_type": request_type,
        "priority": priority,
        "labels": labels,
        "impact": impact,
        "urgency": urgency,
        "confidence_score": coerce_confidence(item.get("confidence_score", 0.0)),
    }


@traced_function("service.jira.service_desk.push_issues")
def push_issues(site_url, email, api_key, project_key, normalized_items):
    """Push normalized Jira Service Desk items to Jira."""
    return jira_client.push_issues_service_desk(site_url, email, api_key, project_key, normalized_items)
