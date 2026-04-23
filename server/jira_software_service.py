"""Jira Software type-specific service helpers."""

from . import jira_client


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
        issue_types = []

    try:
        priorities = jira_client.get_project_priorities(site_url, email, api_key, project_key)
    except ValueError:
        priorities = []

    try:
        sprints = jira_client.get_project_sprints(site_url, email, api_key, project_key)
    except ValueError:
        sprints = []

    try:
        epics = jira_client.get_project_epics(site_url, email, api_key, project_key)
    except ValueError:
        epics = []

    return {
        "issue_types": issue_types,
        "priorities": priorities,
        "sprints": sprints,
        "epics": epics,
    }


def normalize_item(item, normalize_labels, coerce_confidence):
    """Normalize one Jira Software issue payload."""
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
    epic = str(item.get("epic") or "").strip()

    return {
        "summary": summary,
        "description": description,
        "issue_type": issue_type,
        "priority": priority,
        "sprint": sprint,
        "epic": epic,
        "labels": labels,
        "story_points": story_points,
        "components": components,
        "acceptance_criteria": acceptance_criteria,
        "confidence_score": coerce_confidence(item.get("confidence_score", 0.0)),
    }


def push_issues(site_url, email, api_key, project_key, normalized_items):
    """Push normalized Jira Software items to Jira."""
    return jira_client.push_issues_software(site_url, email, api_key, project_key, normalized_items)
