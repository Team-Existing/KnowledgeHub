"""
Jira issues (Cloud, Server or Data Center) selected by a JQL query. The issue
description plus its comment thread is ingested; decisions and risks often
live in the comments.

Auth: Jira Cloud = account email + API token (basic auth). Server / Data
Center = leave email empty and use a personal access token (bearer).
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.connectors import base
from app.services.connectors.base import ConfigField, ConnectorError, FetchResult, Row, SourceDocument
from app.services.connectors.parsers import adf_to_text

KIND = "jira"
LABEL = "Jira — issues and comments"
FILESYSTEM = False
FIELDS = [
    ConfigField("base_url", "Jira URL", required=True, help="e.g. https://acme.atlassian.net"),
    ConfigField("email", "Account email (Cloud only)", help="Leave empty for Server / Data Center"),
    ConfigField("api_token", "API token / personal access token", type="password", required=True, secret=True),
    ConfigField("jql", "JQL", default="updated >= -30d ORDER BY updated DESC",
                help="Which issues to sync, e.g. project = ARCH AND labels = decision"),
    ConfigField("max_items", "Max issues per sync", type="number", default=50),
]

_FIELDS = "summary,description,status,resolution,issuetype,created,updated,reporter,labels,comment"


def _issue_document(base_url: str, issue: Dict[str, Any]) -> SourceDocument:
    f = issue.get("fields") or {}
    key = issue["key"]
    reporter = (f.get("reporter") or {}).get("displayName") or "unknown"
    status = (f.get("status") or {}).get("name") or ""
    resolution = (f.get("resolution") or {}).get("name") or ""
    kind = (f.get("issuetype") or {}).get("name") or "Issue"
    parts = [f"# {key}: {f.get('summary') or ''}",
             f"{kind} reported by {reporter}. Status: {status}" + (f" ({resolution})" if resolution else "") + ".",
             adf_to_text(f.get("description")).strip() or "(no description)"]
    comments = ((f.get("comment") or {}).get("comments")) or []
    lines = []
    for c in comments:
        text = adf_to_text(c.get("body")).strip()
        if text:
            who = (c.get("author") or {}).get("displayName") or "someone"
            lines.append(f"- {who} ({(c.get('created') or '')[:10]}): {text}")
    if lines:
        parts.append("## Comments\n" + "\n".join(lines))
    return SourceDocument(
        external_id=key,
        title=f"{key}: {f.get('summary') or ''}",
        content="\n\n".join(parts),
        mode="document",
        source_type="jira",
        url=f"{base_url}/browse/{key}",
        author=reporter,
        created_at=f.get("created"),
        tags=[str(l) for l in f.get("labels") or []][:5],
    )


async def fetch(config: Row) -> FetchResult:
    base_url = config["base_url"].rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ConnectorError("Jira URL must start with http:// or https://")
    if config.get("email"):
        client_kwargs: Dict[str, Any] = {"auth": (config["email"], config["api_token"])}
        cloud = True
    else:
        client_kwargs = {"headers": {"Authorization": f"Bearer {config['api_token']}"}}
        cloud = False
    limit = config["max_items"]

    issues: List[Dict[str, Any]] = []
    async with base.make_client(**client_kwargs) as client:
        if cloud:
            # Jira Cloud: the enhanced search endpoint, paged with nextPageToken
            token = None
            while len(issues) < limit:
                params = {"jql": config["jql"], "fields": _FIELDS, "maxResults": min(limit - len(issues), 100)}
                if token:
                    params["nextPageToken"] = token
                data = await base.get_json(client, "GET", f"{base_url}/rest/api/3/search/jql", "Jira", params=params)
                issues += data.get("issues") or []
                token = data.get("nextPageToken")
                if not token or data.get("isLast"):
                    break
        else:
            start = 0
            while len(issues) < limit:
                data = await base.get_json(client, "GET", f"{base_url}/rest/api/2/search", "Jira", params={
                    "jql": config["jql"], "fields": _FIELDS, "startAt": start,
                    "maxResults": min(limit - len(issues), 100)})
                batch = data.get("issues") or []
                issues += batch
                start += len(batch)
                if not batch or start >= data.get("total", 0):
                    break
    return FetchResult([_issue_document(base_url, i) for i in issues[:limit]])
