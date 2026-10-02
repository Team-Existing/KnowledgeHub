"""
GitHub merged pull requests: each PR's description and discussion is where
the "why" behind a change gets written down.

Only the repository the user links is contacted: github.com links use that
host's API (api.github.com), GitHub Enterprise links use <host>/api/v3, and
every request is confined to that one host. The token needs read access to
the repository (none for public repos).
"""
from __future__ import annotations

from typing import List, Tuple

from app.services.connectors import base
from app.services.connectors.base import ConfigField, ConnectorError, FetchResult, Row, SourceDocument, user_url

KIND = "github"
LABEL = "GitHub — merged pull requests"
FILESYSTEM = False
FIELDS = [
    ConfigField("repo_url", "Repository link", required=True,
                help="The repository's web address, e.g. https://github.com/acme/payments-api "
                     "(or your GitHub Enterprise address)"),
    ConfigField("token", "Access token", type="password", secret=True,
                help="Fine-grained token with read access to pull requests; optional for public repos"),
    ConfigField("api_url", "API address (optional)",
                help="Only if your GitHub Enterprise API isn't at <host>/api/v3"),
    ConfigField("max_items", "Max PRs per sync", type="number", default=30),
    ConfigField("include_comments", "Include discussion comments (yes/no)", default="yes"),
]


def resolve(config: Row) -> Tuple[str, str, str]:
    """(owner/name, API base, API host) from the user's repository link — no built-in default host."""
    link = user_url(config["repo_url"], "Repository link")
    parts = [p for p in link.path.split("/") if p]
    if len(parts) < 2:
        raise ConnectorError("Repository link must point at a repository, e.g. https://github.com/acme/api")
    repo = f"{parts[0]}/{parts[1].removesuffix('.git')}"
    if config.get("api_url"):
        api = str(user_url(config["api_url"], "API address")).rstrip("/")
    elif link.host.lower() in ("github.com", "www.github.com"):
        api = "https://api.github.com"     # github.com serves its REST API from this host
    else:
        api = f"{link.scheme}://{link.netloc.decode()}/api/v3"   # GitHub Enterprise Server
    return repo, api, user_url(api, "API address").host


def check(config: Row) -> None:
    """Called when the connector is saved, so a bad link is reported before any sync."""
    resolve(config)


async def fetch(config: Row) -> FetchResult:
    repo, api, api_host = resolve(config)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if config.get("token"):
        headers["Authorization"] = f"Bearer {config['token']}"
    limit = config["max_items"]
    with_comments = str(config.get("include_comments", "yes")).lower() not in ("no", "false", "0")

    docs: List[SourceDocument] = []
    async with base.make_client({api_host}, headers=headers) as client:
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
