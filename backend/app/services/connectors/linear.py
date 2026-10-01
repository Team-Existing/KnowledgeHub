"""Linear issues (description + comments) updated in the last N days, optionally for one team."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from app.services.connectors import base
from app.services.connectors.base import ConfigField, ConnectorError, FetchResult, Row, SourceDocument

KIND = "linear"
LABEL = "Linear — issues and comments"
FILESYSTEM = False
FIELDS = [
    ConfigField("api_key", "Personal API key", type="password", required=True, secret=True,
                help="Linear → Settings → Security & access → Personal API keys"),
    ConfigField("team_key", "Team key (optional)", help="e.g. ENG; empty = all teams you can see"),
    ConfigField("days", "Look back (days)", type="number", default=30),
    ConfigField("max_items", "Max issues per sync", type="number", default=50),
]

API_URL = "https://api.linear.app/graphql"

_QUERY = """
query Issues($first: Int!, $after: String, $filter: IssueFilter) {
  issues(first: $first, after: $after, filter: $filter, orderBy: updatedAt) {
    nodes {
      identifier title description url createdAt
      state { name }
      creator { name }
      labels { nodes { name } }
      comments(first: 50) { nodes { body createdAt user { name } } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _issue_document(issue: Dict[str, Any]) -> SourceDocument:
    author = (issue.get("creator") or {}).get("name") or "unknown"
    state = (issue.get("state") or {}).get("name") or ""
    parts = [f"# {issue['identifier']}: {issue.get('title') or ''}",
             f"Created by {author}. State: {state}.",
             (issue.get("description") or "").strip() or "(no description)"]
    comments = [
        f"- {(c.get('user') or {}).get('name') or 'someone'} ({(c.get('createdAt') or '')[:10]}): {c['body'].strip()}"
        for c in ((issue.get("comments") or {}).get("nodes") or []) if (c.get("body") or "").strip()
    ]
    if comments:
        parts.append("## Comments\n" + "\n".join(comments))
    return SourceDocument(
        external_id=issue["identifier"],
        title=f"{issue['identifier']}: {issue.get('title') or ''}",
        content="\n\n".join(parts),
        mode="document",
        source_type="linear",
        url=issue.get("url") or "",
        author=author,
        created_at=issue.get("createdAt"),
        tags=[l["name"] for l in ((issue.get("labels") or {}).get("nodes") or []) if l.get("name")][:5],
    )


async def fetch(config: Row) -> FetchResult:
    since = (datetime.now(timezone.utc) - timedelta(days=config["days"])).isoformat()
    issue_filter: Dict[str, Any] = {"updatedAt": {"gt": since}}
    if config.get("team_key"):
        issue_filter["team"] = {"key": {"eq": config["team_key"]}}
    limit = config["max_items"]

    issues: List[Dict[str, Any]] = []
    after = None
    # personal API keys go in the Authorization header as-is (no "Bearer")
    async with base.make_client(headers={"Authorization": config["api_key"]}) as client:
        while len(issues) < limit:
            data = await base.get_json(client, "POST", API_URL, "Linear", json={
                "query": _QUERY,
                "variables": {"first": min(limit - len(issues), 50), "after": after, "filter": issue_filter},
            })
            if data.get("errors"):
                raise ConnectorError(f"Linear: {data['errors'][0].get('message', 'query failed')}")
            page = (data.get("data") or {}).get("issues") or {}
            issues += page.get("nodes") or []
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
    return FetchResult([_issue_document(i) for i in issues[:limit]])
