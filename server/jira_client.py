"""
Pure Jira REST API client — no Django imports.

Auth: Basic Auth with base64("email:api_key") in Authorization header.
Each function takes (site_url, email, api_key) plus endpoint-specific params.
All responses are simplified dicts; errors raise ValueError.

Jira REST API base URLs (constructed at call time):
  software   : https://<site_url>/rest/api/3/   +  /rest/agile/1.0/ (sprints)
  service_desk: https://<site_url>/rest/servicedeskapi/
  business   : https://<site_url>/rest/api/3/

project_type_key values used in Jira API:
  software    = "software"
  service_desk = "service_desk"
  business    = "business"
"""

import base64

import requests

# Valid Jira project type keys
JIRA_TYPES = ("software", "service_desk", "business")

# Issue type options per project type
ISSUE_TYPES = {
    "software": ["Story", "Bug", "Task", "Epic", "Subtask"],
    "service_desk": ["Service Request", "Incident", "Problem", "Change"],
    "business": ["Task", "Milestone", "Sub-task", "Epic"],
}

PRIORITY_VALUES = ["Highest", "High", "Medium", "Low", "Lowest"]


def _base_url(site_url):
    """Normalise site URL — strip trailing slashes."""
    url = (site_url or "").strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _auth_headers(email, api_key):
    """Return Authorization + Accept headers for Basic Auth."""
    token = base64.b64encode(f"{email}:{api_key}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _check(resp, action):
    """Raise ValueError with a clear message on non-2xx."""
    if not resp.ok:
        detail = ""
        try:
            body = resp.json()
            detail = body.get("errorMessages", [])
            if detail:
                detail = " ".join(detail)
            else:
                errors = body.get("errors") or {}
                detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) if errors else ""
        except Exception:
            pass
        if not detail:
            detail = resp.text[:200] if resp.text else resp.reason
        raise ValueError(f"Jira API error ({action}): {resp.status_code} — {detail}")


# ---------------------------------------------------------------------------
# Auth / connectivity
# ---------------------------------------------------------------------------

def verify_credentials(site_url, email, api_key):
    """GET /rest/api/3/myself — verify credentials are valid.

    Returns {account_id, display_name, email_address} on success.
    Raises ValueError on failure.
    """
    url = f"{_base_url(site_url)}/rest/api/3/myself"
    resp = requests.get(url, headers=_auth_headers(email, api_key), timeout=15)
    _check(resp, "verify_credentials")
    data = resp.json()
    return {
        "account_id": data.get("accountId", ""),
        "display_name": data.get("displayName", ""),
        "email_address": data.get("emailAddress", email),
    }


# ---------------------------------------------------------------------------
# Project listing
# ---------------------------------------------------------------------------

def get_projects(site_url, email, api_key, type_key=None):
    """
    GET /rest/api/3/project/search — list projects.

    type_key: 'software' | 'business' | None (returns all)
    Returns [{id, key, name, project_type_key}].
    """
    params = {"maxResults": 100, "orderBy": "name"}
    if type_key:
        params["typeKey"] = type_key

    url = f"{_base_url(site_url)}/rest/api/3/project/search"
    resp = requests.get(url, headers=_auth_headers(email, api_key), params=params, timeout=15)
    _check(resp, "get_projects")
    data = resp.json()
    projects = []
    for p in data.get("values") or []:
        projects.append({
            "id": p.get("id", ""),
            "key": p.get("key", ""),
            "name": p.get("name", ""),
            "project_type_key": p.get("projectTypeKey", ""),
        })
    return projects


def get_project_issue_types(site_url, email, api_key, project_key):
    """Return available issue types for a project key."""
    project_key = (project_key or "").strip()
    if not project_key:
        return []
    return _get_issue_types(site_url, email, api_key, project_key)


def get_project_priorities(site_url, email, api_key, project_key=None):
    """Return priorities available in the Jira instance (project-aware fallback)."""
    url = f"{_base_url(site_url)}/rest/api/3/priority"
    resp = requests.get(url, headers=_auth_headers(email, api_key), timeout=15)
    _check(resp, "get_project_priorities")
    out = []
    seen = set()
    for row in resp.json() or []:
        name = str(row.get("name") or "").strip()
        pid = str(row.get("id") or name).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": pid, "name": name})
    return out


def get_project_sprints(site_url, email, api_key, project_key):
    """Return sprints for all boards associated with a project key."""
    project_key = (project_key or "").strip()
    if not project_key:
        return []

    headers = _auth_headers(email, api_key)
    base = _base_url(site_url)
    boards_url = f"{base}/rest/agile/1.0/board"
    boards_resp = requests.get(
        boards_url,
        headers=headers,
        params={"projectKeyOrId": project_key, "maxResults": 50},
        timeout=15,
    )
    _check(boards_resp, "get_project_sprints.boards")
    boards = boards_resp.json().get("values") or []

    sprint_map = {}
    state_order = {"active": 0, "future": 1, "closed": 2}

    for board in boards:
        board_id = board.get("id")
        if not board_id:
            continue
        sprints_url = f"{base}/rest/agile/1.0/board/{board_id}/sprint"
        sprints_resp = requests.get(
            sprints_url,
            headers=headers,
            params={"state": "active,future,closed", "maxResults": 100},
            timeout=15,
        )
        if not sprints_resp.ok:
            continue
        for sprint in (sprints_resp.json().get("values") or []):
            sid = str(sprint.get("id") or "").strip()
            name = str(sprint.get("name") or "").strip()
            state = str(sprint.get("state") or "").strip().lower()
            if not sid or not name:
                continue
            if sid in sprint_map:
                continue
            sprint_map[sid] = {
                "id": sid,
                "name": name,
                "state": state,
                "state_rank": state_order.get(state, 99),
            }

    rows = sorted(
        sprint_map.values(),
        key=lambda r: (r.get("state_rank", 99), (r.get("name") or "").lower()),
    )
    return [{"id": row["id"], "name": row["name"], "state": row.get("state", "")} for row in rows]


def get_project_epics(site_url, email, api_key, project_key):
    """Return epic issues for a project key."""
    project_key = (project_key or "").strip()
    if not project_key:
        return []

    jql = f'project = "{project_key}" AND issuetype = Epic ORDER BY created DESC'
    url = f"{_base_url(site_url)}/rest/api/3/search"
    resp = requests.get(
        url,
        headers=_auth_headers(email, api_key),
        params={"jql": jql, "fields": "summary", "maxResults": 100},
        timeout=20,
    )
    _check(resp, "get_project_epics")

    out = []
    for issue in resp.json().get("issues") or []:
        issue_id = str(issue.get("id") or "").strip()
        key = str(issue.get("key") or "").strip()
        summary = str(((issue.get("fields") or {}).get("summary") or "")).strip()
        if not key:
            continue
        out.append({
            "id": issue_id or key,
            "key": key,
            "name": summary or key,
        })
    return out


def get_service_desks(site_url, email, api_key):
    """
    GET /rest/servicedeskapi/servicedesk — list service desk projects.

    Returns [{id, project_key, project_name}].
    """
    url = f"{_base_url(site_url)}/rest/servicedeskapi/servicedesk"
    resp = requests.get(url, headers=_auth_headers(email, api_key), timeout=15)
    _check(resp, "get_service_desks")
    data = resp.json()
    desks = []
    for d in data.get("values") or []:
        desks.append({
            "id": str(d.get("id", "")),
            "key": d.get("projectKey", ""),
            "name": d.get("projectName", ""),
        })
    return desks


def get_service_desk_request_types(site_url, email, api_key, service_desk_id):
    """
    GET /rest/servicedeskapi/servicedesk/<id>/requesttype — list request types.

    Returns [{id, name, description}].
    """
    url = f"{_base_url(site_url)}/rest/servicedeskapi/servicedesk/{service_desk_id}/requesttype"
    resp = requests.get(url, headers=_auth_headers(email, api_key), timeout=15)
    _check(resp, "get_request_types")
    data = resp.json()
    types = []
    for t in data.get("values") or []:
        types.append({
            "id": str(t.get("id", "")),
            "name": t.get("name", ""),
            "description": t.get("description", ""),
        })
    return types


# ---------------------------------------------------------------------------
# Issue creation helpers
# ---------------------------------------------------------------------------

def _adf_doc(text):
    """Wrap plain text in a minimal Atlassian Document Format structure."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": str(text or "")}],
            }
        ],
    }


def _get_issue_types(site_url, email, api_key, project_key):
    """Return available issue types for a project key."""
    url = f"{_base_url(site_url)}/rest/api/3/project/{project_key}/statuses"
    # Use createmeta for issue type list
    meta_url = f"{_base_url(site_url)}/rest/api/3/issue/createmeta"
    resp = requests.get(
        meta_url,
        headers=_auth_headers(email, api_key),
        params={"projectKeys": project_key, "expand": "projects.issuetypes"},
        timeout=15,
    )
    if not resp.ok:
        return []
    data = resp.json()
    for project in data.get("projects") or []:
        if project.get("key") == project_key:
            out = []
            seen = set()
            for it in project.get("issuetypes") or []:
                name = str(it.get("name") or "").strip()
                if not name:
                    continue
                dedupe_key = name.lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                out.append({"id": it.get("id", ""), "name": name})
            return out
    return []


def _resolve_issue_type_id(site_url, email, api_key, project_key, issue_type_name, fallback="Task"):
    """Return issue type id for project; falls back to fallback type."""
    types = _get_issue_types(site_url, email, api_key, project_key)
    name_lower = (issue_type_name or fallback).lower()
    for t in types:
        if t["name"].lower() == name_lower:
            return t["id"]
    # fallback
    for t in types:
        if t["name"].lower() == fallback.lower():
            return t["id"]
    # last resort: first type
    return types[0]["id"] if types else None


# ---------------------------------------------------------------------------
# Push — Software
# ---------------------------------------------------------------------------

def push_issues_software(site_url, email, api_key, project_key, items):
    """
    Create Jira issues for a Software project.

    items — [{summary, description, issue_type, priority, labels,
              story_points, components, acceptance_criteria, confidence_score}]

    Returns [{issue_key, summary, url, warnings}].
    """
    base = _base_url(site_url)
    headers = _auth_headers(email, api_key)
    results = []

    for item in items:
        warnings = []
        summary = str(item.get("summary") or item.get("card_title") or "Untitled").strip() or "Untitled"
        description = str(item.get("description") or item.get("card_description") or "").strip()
        issue_type_name = str(item.get("issue_type") or "Story").strip() or "Story"
        priority = str(item.get("priority") or "").strip()
        labels = [str(lbl).strip() for lbl in (item.get("labels") or []) if str(lbl).strip()]
        story_points = item.get("story_points")
        components = [str(c).strip() for c in (item.get("components") or []) if str(c).strip()]
        acceptance_criteria = str(item.get("acceptance_criteria") or "").strip()

        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "description": _adf_doc(description),
            "issuetype": {"name": issue_type_name},
        }

        if priority:
            fields["priority"] = {"name": priority}

        if labels:
            fields["labels"] = labels

        if components:
            fields["components"] = [{"name": c} for c in components]

        if story_points is not None:
            try:
                sp = float(story_points)
                fields["story_points"] = sp
                # Also try the standard story points custom field name
                fields["customfield_10016"] = sp
            except (TypeError, ValueError):
                warnings.append(f"Invalid story_points value: {story_points}")

        if acceptance_criteria:
            # Append acceptance criteria to description
            description_text = description + "\n\nAcceptance Criteria:\n" + acceptance_criteria if description else "Acceptance Criteria:\n" + acceptance_criteria
            fields["description"] = _adf_doc(description_text)

        resp = requests.post(
            f"{base}/rest/api/3/issue",
            headers=headers,
            json={"fields": fields},
            timeout=20,
        )

        if not resp.ok:
            try:
                err = resp.json()
                err_msg = " ".join(err.get("errorMessages") or [])
                err_detail = "; ".join(f"{k}: {v}" for k, v in (err.get("errors") or {}).items())
                msg = (err_msg + " " + err_detail).strip() or f"HTTP {resp.status_code}"
            except Exception:
                msg = resp.text[:200] or f"HTTP {resp.status_code}"
            warnings.append(f"Failed to create issue: {msg}")
            results.append({"issue_key": None, "summary": summary, "url": "", "warnings": warnings})
            continue

        data = resp.json()
        issue_key = data.get("key", "")
        issue_id = data.get("id", "")
        issue_url = f"{base}/browse/{issue_key}" if issue_key else ""
        results.append({
            "issue_key": issue_key,
            "issue_id": issue_id,
            "summary": summary,
            "url": issue_url,
            "warnings": warnings,
        })

    return results


# ---------------------------------------------------------------------------
# Push — Service Desk
# ---------------------------------------------------------------------------

def push_issues_service_desk(site_url, email, api_key, service_desk_id, items):
    """
    Create Jira Service Desk requests.

    items — [{summary, description, request_type, priority, labels, impact, urgency, confidence_score}]

    Returns [{issue_key, summary, url, warnings}].
    """
    base = _base_url(site_url)
    headers = _auth_headers(email, api_key)
    results = []

    # Get available request types
    request_types = []
    try:
        request_types = get_service_desk_request_types(site_url, email, api_key, service_desk_id)
    except ValueError as exc:
        # If we can't load types, proceed with ID=None and let individual calls fail gracefully
        pass

    def _resolve_request_type_id(name):
        name_lower = (name or "").lower().strip()
        for rt in request_types:
            if rt["name"].lower() == name_lower:
                return rt["id"]
        # Fallback to first type
        return request_types[0]["id"] if request_types else "1"

    for item in items:
        warnings = []
        summary = str(item.get("summary") or item.get("card_title") or "Untitled").strip() or "Untitled"
        description = str(item.get("description") or item.get("card_description") or "").strip()
        request_type_name = str(item.get("request_type") or "").strip()
        impact = str(item.get("impact") or "").strip()
        urgency = str(item.get("urgency") or "").strip()

        request_type_id = _resolve_request_type_id(request_type_name)
        if not request_type_id:
            warnings.append(f"Could not resolve request type '{request_type_name}'; using default.")
            request_type_id = "1"

        # Build description text including impact/urgency
        desc_parts = []
        if description:
            desc_parts.append(description)
        if impact:
            desc_parts.append(f"Impact: {impact}")
        if urgency:
            desc_parts.append(f"Urgency: {urgency}")
        full_description = "\n\n".join(desc_parts)

        payload = {
            "serviceDeskId": str(service_desk_id),
            "requestTypeId": str(request_type_id),
            "requestFieldValues": {
                "summary": summary,
                "description": full_description,
            },
        }

        resp = requests.post(
            f"{base}/rest/servicedeskapi/request",
            headers=headers,
            json=payload,
            timeout=20,
        )

        if not resp.ok:
            try:
                err = resp.json()
                err_msg = " ".join(err.get("errorMessages") or [])
                msg = err_msg or f"HTTP {resp.status_code}"
            except Exception:
                msg = resp.text[:200] or f"HTTP {resp.status_code}"
            warnings.append(f"Failed to create request: {msg}")
            results.append({"issue_key": None, "summary": summary, "url": "", "warnings": warnings})
            continue

        data = resp.json()
        issue_key = (data.get("issueKey") or data.get("key") or "")
        issue_url = ""
        if issue_key:
            issue_url = f"{base}/browse/{issue_key}"

        results.append({
            "issue_key": issue_key,
            "summary": summary,
            "url": issue_url,
            "warnings": warnings,
        })

    return results


# ---------------------------------------------------------------------------
# Push — Business
# ---------------------------------------------------------------------------

def push_issues_business(site_url, email, api_key, project_key, items):
    """
    Create Jira issues for a Business (Work Management) project.

    items — [{summary, description, issue_type, priority, labels,
              due_date, category, confidence_score}]

    Returns [{issue_key, summary, url, warnings}].
    """
    base = _base_url(site_url)
    headers = _auth_headers(email, api_key)
    results = []

    for item in items:
        warnings = []
        summary = str(item.get("summary") or item.get("card_title") or "Untitled").strip() or "Untitled"
        description = str(item.get("description") or item.get("card_description") or "").strip()
        issue_type_name = str(item.get("issue_type") or "Task").strip() or "Task"
        priority = str(item.get("priority") or "").strip()
        labels = [str(lbl).strip() for lbl in (item.get("labels") or []) if str(lbl).strip()]
        due_date = str(item.get("due_date") or "").strip()
        category = str(item.get("category") or "").strip()

        # Append category to description
        desc_parts = []
        if description:
            desc_parts.append(description)
        if category:
            desc_parts.append(f"Category: {category}")
        full_description = "\n\n".join(desc_parts)

        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "description": _adf_doc(full_description),
            "issuetype": {"name": issue_type_name},
        }

        if priority:
            fields["priority"] = {"name": priority}

        if labels:
            fields["labels"] = labels

        if due_date:
            # Jira expects YYYY-MM-DD format
            fields["duedate"] = due_date

        resp = requests.post(
            f"{base}/rest/api/3/issue",
            headers=headers,
            json={"fields": fields},
            timeout=20,
        )

        if not resp.ok:
            try:
                err = resp.json()
                err_msg = " ".join(err.get("errorMessages") or [])
                err_detail = "; ".join(f"{k}: {v}" for k, v in (err.get("errors") or {}).items())
                msg = (err_msg + " " + err_detail).strip() or f"HTTP {resp.status_code}"
            except Exception:
                msg = resp.text[:200] or f"HTTP {resp.status_code}"
            warnings.append(f"Failed to create issue: {msg}")
            results.append({"issue_key": None, "summary": summary, "url": "", "warnings": warnings})
            continue

        data = resp.json()
        issue_key = data.get("key", "")
        issue_url = f"{base}/browse/{issue_key}" if issue_key else ""
        results.append({
            "issue_key": issue_key,
            "summary": summary,
            "url": issue_url,
            "warnings": warnings,
        })

    return results
