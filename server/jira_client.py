"""
Pure Jira REST API client — no Django imports.

Auth: Basic Auth with base64("email:api_key") in Authorization header.
Each function takes (site_url, email, api_key) plus endpoint-specific params.
All responses are simplified dicts; errors raise ValueError.

Per-call HTTP detail (timing, status, request/response bodies) is captured
on OpenTelemetry spans created by the auto-instrumented ``requests``
library; this module enriches the active span with the redacted action +
payload context. Only ERROR records are emitted to console. The
Authorization header is never logged or attached to spans.

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
import logging
import re

import requests

from core.http_tracing import instrument_http_response

logger = logging.getLogger(__name__)

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


def _handle_api_response(resp, action):
    """Enrich the active span and raise ValueError on non-2xx responses.

    Never logs the Authorization header or request body to console.
    """
    if resp.ok:
        instrument_http_response(
            resp,
            provider="jira",
            action=action,
        )
        return

    method = getattr(resp.request, "method", "?") if resp.request else "?"
    url = getattr(resp.request, "url", "") or ""
    elapsed_ms = int(resp.elapsed.total_seconds() * 1000) if resp.elapsed else 0

    detail = ""
    error_messages = []
    field_errors = {}
    try:
        body = resp.json()
        error_messages = body.get("errorMessages", []) or []
        field_errors = body.get("errors") or {}
        detail = error_messages
        if detail:
            detail = " ".join(detail)
        else:
            detail = "; ".join(f"{k}: {v}" for k, v in field_errors.items()) if field_errors else ""
    except Exception:
        pass
    if not detail:
        detail = resp.text[:200] if resp.text else resp.reason

    _, detail = instrument_http_response(
        resp,
        provider="jira",
        action=action,
        detail=detail,
        error_messages=error_messages,
        field_errors=field_errors,
    )
    detail = detail or "HTTP error"

    logger.error(
        "jira.api.error",
        extra={
            "action": action,
            "method": method,
            "url": url,
            "status": resp.status_code,
            "elapsed_ms": elapsed_ms,
            "body_snippet": (resp.text or "")[:500],
        },
    )
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
    _handle_api_response(resp, "verify_credentials")
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
    _handle_api_response(resp, "get_projects")
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
    _handle_api_response(resp, "get_project_priorities")
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
    _handle_api_response(boards_resp, "get_project_sprints.boards")
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
            instrument_http_response(
                sprints_resp,
                provider="jira",
                action="get_project_sprints.sprints",
                detail=_format_jira_error(sprints_resp),
            )
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
    """Return epic issues for a project key.

    Uses the new ``/rest/api/3/search/jql`` endpoint (the legacy
    ``/rest/api/3/search`` was removed by Atlassian — see
    https://developer.atlassian.com/changelog/#CHANGE-2046).
    """
    project_key = (project_key or "").strip()
    if not project_key:
        return []

    jql = f'project = "{project_key}" AND issuetype = Epic ORDER BY created DESC'
    url = f"{_base_url(site_url)}/rest/api/3/search/jql"
    resp = requests.post(
        url,
        headers=_auth_headers(email, api_key),
        json={"jql": jql, "fields": ["summary"], "maxResults": 100},
        timeout=20,
    )
    _handle_api_response(resp, "get_project_epics")

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


def get_project_existing_issues(site_url, email, api_key, project_key):
    """Return existing Jira issues for a project.

    Uses ``/rest/api/3/search/jql`` and returns rows shaped as:
    ``[{key, summary, issue_type, parent_key}]``.
    """
    project_key = (project_key or "").strip()
    if not project_key:
        return []

    jql = f'project = "{project_key}" ORDER BY created DESC'
    url = f"{_base_url(site_url)}/rest/api/3/search/jql"
    resp = requests.post(
        url,
        headers=_auth_headers(email, api_key),
        json={"jql": jql, "fields": ["summary", "issuetype", "parent"], "maxResults": 500},
        timeout=20,
    )
    _handle_api_response(resp, "get_project_existing_issues")

    out = []
    for issue in resp.json().get("issues") or []:
        key = str(issue.get("key") or "").strip()
        fields = issue.get("fields") or {}
        summary = str((fields.get("summary") or "")).strip()
        issue_type = str(((fields.get("issuetype") or {}).get("name") or "")).strip()
        parent_key = str(((fields.get("parent") or {}).get("key") or "")).strip()
        if not key:
            continue
        out.append({"key": key, "summary": summary, "issue_type": issue_type, "parent_key": parent_key})
    return out


def get_service_desks(site_url, email, api_key):
    """
    GET /rest/servicedeskapi/servicedesk — list service desk projects.

    Returns [{id, project_key, project_name}].
    """
    url = f"{_base_url(site_url)}/rest/servicedeskapi/servicedesk"
    resp = requests.get(url, headers=_auth_headers(email, api_key), timeout=15)
    _handle_api_response(resp, "get_service_desks")
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
    _handle_api_response(resp, "get_request_types")
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
        instrument_http_response(
            resp,
            provider="jira",
            action="get_issue_types",
            detail=_format_jira_error(resp),
        )
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

# Issue types that cannot receive a Sprint assignment via the Agile API.
# Epics live above sprints; sub-tasks inherit from their parent.
_NON_SPRINTABLE_TYPES = {"epic", "sub-task", "subtask"}

# Aliases used when the extracted issue_type does not exist in the
# destination project's issue type scheme. Each value is a list of
# fallbacks tried in order against the project's available types.
_ISSUE_TYPE_ALIASES = {
    "feature": ["feature", "story", "task"],
    "story": ["story", "task"],
    "task": ["task", "story"],
    "bug": ["bug", "defect", "task"],
    "epic": ["epic"],
    # Some projects disable Sub-task issue types; gracefully degrade to Task.
    "sub-task": ["sub-task", "subtask", "task"],
    "subtask": ["subtask", "sub-task", "task"],
}


def _build_type_resolver(project_types):
    """Return a callable that maps an issue_type_name (any casing/alias)
    to the EXACT name as defined in the destination project's issue type
    scheme, or None if no candidate matches.
    """
    available = {t["name"].lower(): t["name"] for t in (project_types or []) if t.get("name")}

    def resolve(name):
        key = (name or "").strip().lower()
        if not key:
            return None
        if key in available:
            return available[key]
        for alias in _ISSUE_TYPE_ALIASES.get(key, []):
            if alias in available:
                return available[alias]
        return None

    return resolve


def _build_software_fields(item, project_key, parent_key, resolved_type_name, warnings):
    """Compose the Jira REST `fields` payload for a Software issue."""
    summary = str(item.get("summary") or item.get("card_title") or "Untitled").strip() or "Untitled"
    description = str(item.get("description") or item.get("card_description") or "").strip()
    issue_type_name = resolved_type_name or "Task"
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
            # Keep internal `story_points` as a logical field only.
            # Jira REST accepts custom fields (for example customfield_10016),
            # not a top-level `story_points` field.
            fields["customfield_10016"] = sp
        except (TypeError, ValueError):
            warnings.append(f"Invalid story_points value: {story_points}")

    if acceptance_criteria:
        description_text = (
            description + "\n\nAcceptance Criteria:\n" + acceptance_criteria
            if description
            else "Acceptance Criteria:\n" + acceptance_criteria
        )
        fields["description"] = _adf_doc(description_text)

    if parent_key:
        # Jira Cloud team-managed projects accept `fields.parent` for the
        # full hierarchy (Epic -> Story -> Sub-task). Company-managed
        # projects need the Epic Link customfield for Story->Epic — we
        # cascade through fallbacks in `_create_software_issue`.
        fields["parent"] = {"key": parent_key}

    return fields, summary, issue_type_name


def _format_jira_error(resp):
    """Best-effort extraction of a human-readable error from a Jira response."""
    try:
        body = resp.json()
        msgs = " ".join(body.get("errorMessages") or [])
        details = "; ".join(f"{k}: {v}" for k, v in (body.get("errors") or {}).items())
        combined = (msgs + " " + details).strip()
        if combined:
            return combined
    except Exception:
        pass
    return (resp.text or "")[:300] or f"HTTP {resp.status_code}"


def _extract_jira_error_details(resp):
    """Return (message, errors_dict) parsed from a Jira error response."""
    message = ""
    errors = {}
    try:
        body = resp.json()
        msg = " ".join(body.get("errorMessages") or []).strip()
        errs = body.get("errors") or {}
        details = "; ".join(f"{k}: {v}" for k, v in errs.items())
        message = (msg + " " + details).strip()
        errors = errs
    except Exception:
        message = (resp.text or "").strip()
        errors = {}
    if not message:
        message = f"HTTP {resp.status_code}"
    return message, errors


def _unsupported_field_keys(resp, attempted_fields):
    """Best-effort detection of field keys Jira says are unknown/unsettable."""
    message, errors = _extract_jira_error_details(resp)
    keys = set()

    for key, val in (errors or {}).items():
        txt = str(val or "").lower()
        if any(
            token in txt
            for token in (
                "cannot be set",
                "unknown",
                "not on the appropriate screen",
                "field was not found",
            )
        ):
            keys.add(str(key))

    # Some Jira errors only appear in `errorMessages`.
    for match in re.findall(r"[Ff]ield\s+'([^']+)'", message or ""):
        keys.add(match)

    return [k for k in keys if k in (attempted_fields or {})]


def _create_software_issue(base, headers, fields, parent_key, allow_epic_link=True):
    """POST /rest/api/3/issue with progressive parent-link fallbacks.

    Strategy when ``parent_key`` is set and the first attempt returns 400:
        1. ``fields.parent``         (team-managed projects, Sub-task in classic)
        2. ``customfield_10014``     (classic Story -> Epic via Epic Link)
        3. no parent at all          (issue is created un-linked + warning)

    Returns ``(response, parent_method, attempts)`` where ``parent_method``
    is one of ``"parent"``, ``"epic_link"``, ``"none"``, or ``""`` (no
    parent attempted).
    """
    url = f"{base}/rest/api/3/issue"
    resp = requests.post(url, headers=headers, json={"fields": fields}, timeout=20)
    instrument_http_response(
        resp,
        provider="jira",
        action="push_issues_software.create_issue.parent",
        detail=_format_jira_error(resp) if not resp.ok else None,
    )
    attempts = [("parent" if parent_key else "", resp.status_code)]

    if resp.ok:
        return resp, ("parent" if parent_key else ""), attempts
    if not parent_key:
        return resp, "", attempts

    # Only retry on 400 — auth / project / type errors won't be helped by
    # changing the parent linkage strategy.
    if resp.status_code != 400:
        return resp, "parent", attempts

    if allow_epic_link:
        # Attempt 2: Epic Link customfield (classic Story->Epic).
        retry_fields = {k: v for k, v in fields.items() if k != "parent"}
        retry_fields["customfield_10014"] = parent_key
        resp2 = requests.post(url, headers=headers, json={"fields": retry_fields}, timeout=20)
        instrument_http_response(
            resp2,
            provider="jira",
            action="push_issues_software.create_issue.epic_link",
            detail=_format_jira_error(resp2) if not resp2.ok else None,
        )
        attempts.append(("epic_link", resp2.status_code))
        if resp2.ok:
            return resp2, "epic_link", attempts
        if resp2.status_code != 400:
            return resp2, "epic_link", attempts

    # Attempt 3: drop the parent linkage entirely so the issue is at
    # least created. The caller appends a warning describing this.
    bare_fields = {k: v for k, v in fields.items() if k not in ("parent", "customfield_10014")}
    resp3 = requests.post(url, headers=headers, json={"fields": bare_fields}, timeout=20)
    instrument_http_response(
        resp3,
        provider="jira",
        action="push_issues_software.create_issue.no_parent",
        detail=_format_jira_error(resp3) if not resp3.ok else None,
    )
    attempts.append(("none", resp3.status_code))
    if resp3.ok:
        return resp3, "none", attempts

    # All three attempts failed — return the most recent (unlinked) error
    # so the user sees the cleanest message.
    return resp3, "none", attempts


def _assign_issue_to_sprint(base, headers, sprint_value, issue_key):
    """POST /rest/agile/1.0/sprint/{sprintId}/issue. Returns warning string
    on failure, otherwise empty string."""
    sprint_id = str(sprint_value or "").strip()
    if not sprint_id or not sprint_id.isdigit():
        return f"Sprint '{sprint_value}' is not a numeric sprint id; skipped sprint assignment."
    resp = requests.post(
        f"{base}/rest/agile/1.0/sprint/{sprint_id}/issue",
        headers=headers,
        json={"issues": [issue_key]},
        timeout=20,
    )
    instrument_http_response(
        resp,
        provider="jira",
        action="push_issues_software.assign_sprint",
        detail=_format_jira_error(resp) if not resp.ok else None,
    )
    if resp.ok:
        return ""
    return f"Sprint assignment failed (sprint id {sprint_id}): {_format_jira_error(resp)}"


def _update_software_issue(base, headers, issue_key, fields):
    """PUT /rest/api/3/issue/{issue_key} for updating an existing issue."""
    url = f"{base}/rest/api/3/issue/{issue_key}"
    return requests.put(url, headers=headers, json={"fields": fields}, timeout=20)


def push_issues_software(site_url, email, api_key, project_key, items):
    """
    Create Jira Software issues with parent/child hierarchy preserved.

    items — flat list of normalized issues. Each item carries:
      summary, description, issue_type, priority, sprint, labels,
      story_points, components, acceptance_criteria, confidence_score,
            temp_id, parent_temp_id, existing_issue_key

    Strategy
    --------
    1. Resolve every requested ``issue_type`` against the destination
       project's real issue type scheme (with sensible aliases) so a
       missing custom type like ``Feature`` falls back to ``Story``
       instead of failing the create outright.
    2. Build a parent → children index using ``temp_id`` /
       ``parent_temp_id``. Roots are items whose ``parent_temp_id`` is
       missing or unknown.
    3. Walk the tree breadth-first from the roots so a parent's Jira key
       is always known before we create its children.
    4. Maintain ``temp_to_key = {temp_id: jira_key}`` and pass each
       child's parent reference. ``_create_software_issue`` cascades
       through ``fields.parent`` -> ``customfield_10014`` -> no parent
       so a single 400 does not abort the create.
    5. After issue create, assign Sprint via the Agile API only when the
       issue carries a non-empty numeric sprint id and is sprintable.
       Empty sprint == Backlog == skip the call entirely.
        6. If ``existing_issue_key`` is present, update that existing Jira
             issue with the card's fields, map ``temp_to_key[temp_id] =
             existing_issue_key``, emit a success-style result row, and
             continue BFS so descendants can attach to that key.

    Returns [{issue_key, issue_id, summary, url, warnings, temp_id}].
    Order matches the BFS push order (roots first, then breadth-first).
    """
    base = _base_url(site_url)
    headers = _auth_headers(email, api_key)

    items = list(items or [])
    project_types = _get_issue_types(site_url, email, api_key, project_key)
    resolve_type = _build_type_resolver(project_types)

    by_temp_id = {}
    for it in items:
        tid = str((it or {}).get("temp_id") or "").strip()
        if not tid:
            continue
        by_temp_id[tid] = it

    children_of = {}
    roots = []
    for it in items:
        tid = str((it or {}).get("temp_id") or "").strip()
        if not tid:
            # No temp_id -> treat as root, will be created without parent.
            roots.append(it)
            continue
        pid = (it or {}).get("parent_temp_id")
        pid = str(pid).strip() if pid else ""
        if pid and pid in by_temp_id and pid != tid:
            children_of.setdefault(pid, []).append(it)
        else:
            roots.append(it)

    temp_to_key = {}
    results = []

    # BFS from roots — guarantees parents are created before children.
    queue = list(roots)
    while queue:
        item = queue.pop(0)
        warnings = []
        tid = str((item or {}).get("temp_id") or "").strip()
        parent_tid = (item or {}).get("parent_temp_id")
        parent_tid = str(parent_tid).strip() if parent_tid else ""

        existing_issue_key = str((item or {}).get("existing_issue_key") or "").strip()
        parent_key = ""
        if parent_tid:
            parent_key = temp_to_key.get(parent_tid, "")
            if not parent_key:
                warnings.append(
                    f"Parent '{parent_tid}' was not created; this issue will be created as a root."
                )

        if existing_issue_key and tid:
            # Children may still link to this existing issue even if update
            # partially fails, so publish the mapping early.
            temp_to_key[tid] = existing_issue_key

        requested_type = str((item or {}).get("issue_type") or "Task").strip() or "Task"
        if not parent_key and requested_type.strip().lower() in {"sub-task", "subtask"}:
            warnings.append(
                "Parent issue is unavailable; converting Sub-task to Task so creation can continue."
            )
            requested_type = "Task"
        resolved_type = resolve_type(requested_type)
        if not resolved_type:
            resolved_type = requested_type
            warnings.append(
                f"Issue type '{requested_type}' is not recognized for this project; "
                f"attempting Jira create with '{requested_type}'."
            )
        if resolved_type.lower() != requested_type.lower():
            warnings.append(
                f"Issue type '{requested_type}' is not available in this project; "
                f"using '{resolved_type}' instead."
            )

        fields, summary, issue_type_name = _build_software_fields(
            item, project_key, parent_key, resolved_type, warnings
        )

        if existing_issue_key:
            # Existing issue path: update selected Jira issue with the current
            # card fields (editable rows update Jira in place).
            resp = _update_software_issue(base, headers, existing_issue_key, fields)

            if not resp.ok and resp.status_code == 400:
                bad_keys = _unsupported_field_keys(resp, fields)
                if bad_keys:
                    stripped_fields = {k: v for k, v in fields.items() if k not in set(bad_keys)}
                    warnings.append(
                        "Removed unsupported Jira field(s) and retried update: "
                        + ", ".join(sorted(set(bad_keys)))
                    )
                    resp2 = _update_software_issue(base, headers, existing_issue_key, stripped_fields)
                    resp = resp2

            if not resp.ok:
                warnings.append(f"Failed to update issue {existing_issue_key}: {_format_jira_error(resp)}")

            sprint_value = str((item or {}).get("sprint") or "").strip()
            if sprint_value and existing_issue_key:
                if issue_type_name.strip().lower() in _NON_SPRINTABLE_TYPES:
                    warnings.append(
                        f"Skipped sprint assignment: '{issue_type_name}' issues cannot be placed in a sprint."
                    )
                else:
                    sprint_warning = _assign_issue_to_sprint(base, headers, sprint_value, existing_issue_key)
                    if sprint_warning:
                        warnings.append(sprint_warning)

            results.append({
                "issue_key": existing_issue_key,
                "issue_id": "<existing>",
                "summary": summary,
                "url": f"{base}/browse/{existing_issue_key}",
                "warnings": warnings,
                "temp_id": tid,
            })

            if tid:
                for child in children_of.get(tid, []):
                    queue.append(child)
            continue

        parent_type_name = ""
        if parent_tid and parent_tid in by_temp_id:
            parent_type_name = str((by_temp_id[parent_tid] or {}).get("issue_type") or "").strip().lower()
        child_type_name = issue_type_name.strip().lower()

        # Epic Link fallback is only meaningful for classic Story/Task/Bug -> Epic style links.
        allow_epic_link = bool(parent_key and parent_type_name == "epic" and child_type_name not in _NON_SPRINTABLE_TYPES)

        resp, parent_method, attempts = _create_software_issue(
            base,
            headers,
            fields,
            parent_key,
            allow_epic_link=allow_epic_link,
        )

        # If Jira rejects unknown/unsettable fields, strip only those keys and retry once.
        if not resp.ok and resp.status_code == 400:
            bad_keys = _unsupported_field_keys(resp, fields)
            if bad_keys:
                stripped_fields = {k: v for k, v in fields.items() if k not in set(bad_keys)}
                warnings.append(
                    "Removed unsupported Jira field(s) and retried: " + ", ".join(sorted(set(bad_keys)))
                )
                resp2, parent_method2, attempts2 = _create_software_issue(
                    base,
                    headers,
                    stripped_fields,
                    parent_key,
                    allow_epic_link=allow_epic_link,
                )
                resp = resp2
                parent_method = parent_method2
                attempts.extend([(f"retry/{m}" if m else "retry/no-parent", s) for m, s in attempts2])

        if resp.ok and parent_key:
            if parent_method == "epic_link":
                warnings.append(
                    "Parent linked via Epic Link customfield_10014 (company-managed project fallback)."
                )
            elif parent_method == "none":
                warnings.append(
                    "Parent link could not be applied — issue was created without a parent. "
                    "Link it manually in Jira if required."
                )

        if not resp.ok:
            attempt_summary = ", ".join(f"{m or 'no-parent'}={s}" for m, s in attempts)
            warnings.append(
                f"Failed to create issue: {_format_jira_error(resp)} (tried: {attempt_summary})"
            )
            results.append({
                "issue_key": None,
                "issue_id": None,
                "summary": summary,
                "url": "",
                "warnings": warnings,
                "temp_id": tid,
            })
            # Skip enqueuing children — they have no parent to attach to.
            continue

        data = resp.json()
        issue_key = data.get("key", "")
        issue_id = data.get("id", "")
        issue_url = f"{base}/browse/{issue_key}" if issue_key else ""

        if tid and issue_key:
            temp_to_key[tid] = issue_key

        # Sprint assignment via Agile API. Backlog (empty) -> skip.
        sprint_value = str((item or {}).get("sprint") or "").strip()
        if sprint_value and issue_key:
            if issue_type_name.strip().lower() in _NON_SPRINTABLE_TYPES:
                warnings.append(
                    f"Skipped sprint assignment: '{issue_type_name}' issues cannot be placed in a sprint."
                )
            else:
                sprint_warning = _assign_issue_to_sprint(base, headers, sprint_value, issue_key)
                if sprint_warning:
                    warnings.append(sprint_warning)

        results.append({
            "issue_key": issue_key,
            "issue_id": issue_id,
            "summary": summary,
            "url": issue_url,
            "warnings": warnings,
            "temp_id": tid,
        })

        # Enqueue this node's direct children for the next BFS level.
        if tid:
            for child in children_of.get(tid, []):
                queue.append(child)

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
            instrument_http_response(
                resp,
                provider="jira",
                action="push_issues_service_desk.create_request",
                detail=msg,
            )
            warnings.append(f"Failed to create request: {msg}")
            results.append({"issue_key": None, "summary": summary, "url": "", "warnings": warnings})
            continue

        instrument_http_response(
            resp,
            provider="jira",
            action="push_issues_service_desk.create_request",
        )

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
            instrument_http_response(
                resp,
                provider="jira",
                action="push_issues_business.create_issue",
                detail=msg,
            )
            warnings.append(f"Failed to create issue: {msg}")
            results.append({"issue_key": None, "summary": summary, "url": "", "warnings": warnings})
            continue

        instrument_http_response(
            resp,
            provider="jira",
            action="push_issues_business.create_issue",
        )

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
