"""Jira Software type-specific service helpers."""

import logging
import secrets

from . import jira_client

logger = logging.getLogger(__name__)


def _gen_temp_id():
    return f"T{secrets.token_hex(4)}"


def fetch_spaces(site_url, email, api_key):
    """Return Jira Software projects for the configured credentials."""
    return jira_client.get_projects(site_url, email, api_key, type_key="software")


def fetch_project_metadata(site_url, email, api_key, project_key):
    """Return Jira Software project metadata for editor dropdowns."""
    issue_types = []
    priorities = []
    sprints = []
    epics = []

    try:
        issue_types = jira_client.get_project_issue_types(site_url, email, api_key, project_key)
    except ValueError:
        logger.warning(
            "jira.software.metadata.fallback",
            extra={"field": "issue_types", "project_key": project_key},
            exc_info=True,
        )
        issue_types = []

    try:
        priorities = jira_client.get_project_priorities(site_url, email, api_key, project_key)
    except ValueError:
        logger.warning(
            "jira.software.metadata.fallback",
            extra={"field": "priorities", "project_key": project_key},
            exc_info=True,
        )
        priorities = []

    try:
        sprints = jira_client.get_project_sprints(site_url, email, api_key, project_key)
    except ValueError:
        logger.warning(
            "jira.software.metadata.fallback",
            extra={"field": "sprints", "project_key": project_key},
            exc_info=True,
        )
        sprints = []

    # NOTE: Epics are intentionally not fetched here. The export modal no
    # longer exposes a global Epic selector — parent linkage is expressed
    # via the issue tree (`temp_id` / `parent_temp_id`). Avoiding this call
    # also sidesteps the deprecated `/rest/api/3/search` endpoint.
    return {
        "issue_types": issue_types,
        "priorities": priorities,
        "sprints": sprints,
    }


def normalize_item(item, normalize_labels, coerce_confidence):
    """Normalize one Jira Software issue payload.

    Hierarchy fields: ``temp_id`` (auto-generated if missing) and
    ``parent_temp_id`` (str or ``None``) are preserved so push-time parent
    linking can resolve via a ``temp_id -> jira_key`` map. ``depth_level``
    on input is intentionally ignored — depth is derived from the parent
    chain at render and push time. The legacy ``epic`` field is dropped;
    parent linkage replaces it.
    """
    summary = str(item.get("summary") or item.get("card_title") or "").strip() or "Untitled"
    description = str(item.get("description") or item.get("card_description") or "").strip()
    issue_type = str(item.get("issue_type") or "Story").strip() or "Story"
    priority = str(item.get("priority") or "").strip()
    labels = normalize_labels(item.get("labels"))
    story_points = item.get("story_points")
    if story_points is not None:
        try:
            story_points = float(story_points)
        except (TypeError, ValueError):
            story_points = None
    components = [str(c).strip() for c in (item.get("components") or []) if str(c).strip()]
    acceptance_criteria = str(item.get("acceptance_criteria") or "").strip()
    sprint = str(item.get("sprint") or "").strip()

    temp_id = str(item.get("temp_id") or "").strip() or _gen_temp_id()
    raw_parent = item.get("parent_temp_id")
    if raw_parent is None:
        parent_temp_id = None
    else:
        parent_temp_id = str(raw_parent).strip() or None
    if parent_temp_id == temp_id:
        # Self-referential parent → treat as root.
        parent_temp_id = None

    return {
        "summary": summary,
        "description": description,
        "issue_type": issue_type,
        "priority": priority,
        "sprint": sprint,
        "labels": labels,
        "story_points": story_points,
        "components": components,
        "acceptance_criteria": acceptance_criteria,
        "confidence_score": coerce_confidence(item.get("confidence_score", 0.0)),
        "temp_id": temp_id,
        "parent_temp_id": parent_temp_id,
    }


def push_issues(site_url, email, api_key, project_key, normalized_items):
    """Push normalized Jira Software items to Jira."""
    return jira_client.push_issues_software(site_url, email, api_key, project_key, normalized_items)
