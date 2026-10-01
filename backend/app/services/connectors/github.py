"""
GitHub merged pull requests: each PR's description and discussion is where
the "why" behind a change gets written down. Works with GitHub Enterprise via
API URL. The token needs read access to the repository (none for public repos).
"""
from __future__ import annotations

from typing import List

from app.services.connectors import base
from app.services.connectors.base import ConfigField, ConnectorError, FetchResult, Row, SourceDocument

KIND = "github"
LABEL = "GitHub — merged pull requests"
FILESYSTEM = False
FIELDS = [
    ConfigField("repo", "Repository", required=True, help="owner/name, e.g. acme/payments-api"),
    ConfigField("token", "Access token", type="password", secret=True,
                help="Fine-grained token with read access to pull requests; optional for public repos"),
    ConfigField("api_url", "API URL", default="https://api.github.com",
                help="Change only for GitHub Enterprise, e.g. https://github.acme.com/api/v3"),
    ConfigField("max_items", "Max PRs per sync", type="number", default=30),
    ConfigField("include_comments", "Include discussion comments (yes/no)", default="yes"),
]


async def fetch(config: Row) -> FetchResult:
    repo = config["repo"].strip("/")
    if repo.count("/") != 1:
        raise ConnectorError("Repository must look like owner/name")
    api = config["api_url"].rstrip("/")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if config.get("token"):
        headers["Authorization"] = f"Bearer {config['token']}"
    limit = config["max_items"]
    with_comments = str(config.get("include_comments", "yes")).lower() not in ("no", "false", "0")

    docs: List[SourceDocument] = []
    async with base.make_client(headers=headers) as client:
        page = 1
        while len(docs) < limit and page <= 10:
            pulls = await base.get_json(
                client, "GET", f"{api}/repos/{repo}/pulls", "GitHub",
                params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": 50, "page": page},
            )
            if not pulls:
                break
            for pr in pulls:
                if not pr.get("merged_at"):
                    continue
                body = (pr.get("body") or "").strip()
                comments: List[str] = []
                if with_comments and pr.get("comments_url"):
                    for c in await base.get_json(client, "GET", pr["comments_url"], "GitHub", params={"per_page": 50}):
                        text = (c.get("body") or "").strip()
                        if text:
                            comments.append(f"- {(c.get('user') or {}).get('login', 'someone')}: {text}")
                author = (pr.get("user") or {}).get("login") or "unknown"
                parts = [f"# {pr['title']}", f"Pull request #{pr['number']} by {author}, merged {pr['merged_at']}.",
                         body or "(no description)"]
                if comments:
                    parts.append("## Discussion\n" + "\n".join(comments))
                docs.append(SourceDocument(
                    external_id=f"pr-{pr['number']}",
                    title=f"PR #{pr['number']}: {pr['title']}",
                    content="\n\n".join(parts),
                    mode="document",
                    source_type="github_pr",
                    url=pr.get("html_url", ""),
                    author=author,
                    created_at=pr["merged_at"],
                    tags=[l["name"] for l in pr.get("labels") or [] if l.get("name")][:5],
                ))
                if len(docs) >= limit:
                    break
            page += 1
    return FetchResult(docs)
