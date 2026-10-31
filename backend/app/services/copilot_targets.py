"""
Where the Decision Copilot reads and comments: one adapter per connector kind.

  github   pull requests (title, description, changed file paths); Markdown comments,
           found again by a hidden marker
  jira     issues (summary, description); wiki-markup comments (REST API v2, which
           Cloud and Server both serve), found again by the id of the last one posted
  linear   issues (title, description); Markdown comments over GraphQL, found
           again by the id of the last one posted

Every request goes through the connector's host-confined client
(connectors/base.make_client), to the address in the connector's own config.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.connectors import base, github
from app.services.connectors.base import ConnectorError, Row, user_url

MARKER = "<!-- knowledge-hubs-copilot -->"
MAX_FILES = 100
MAX_BODY = 3000


@dataclass
class Target:
    """A pull request or issue, as the Copilot analyses it."""
    ref: str                    # "7", "ARCH-12", "ENG-42"
    title: str
    body: str
    url: str
    noun: str                   # "pull request" / "issue"
    files: List[str] = field(default_factory=list)
    skip: Optional[str] = None  # why it shouldn't be analysed (a draft, closed …)
    native_id: Optional[str] = None   # Linear's issue id, needed to comment

    def proposal(self) -> str:
        text = f"{self.title}\n\n{self.body}".strip()
        if self.files:
            text += "\n\nChanged files:\n" + "\n".join(self.files)
        return text

    def revision(self) -> str:
        """What was analysed: an edit that changes none of it (or our own comment) isn't analysed again."""
        return hashlib.sha256(self.proposal().encode()).hexdigest()[:16]


# ── rendering ────────────────────────────────────────────────────────────────

# a comment is built as a list of blocks, then written in each platform's markup:
#   ("heading", text) ("warning", text) ("bold", text) ("item", text, url, [sub-lines]) ("note", text) ("blank",)

def markdown(blocks: List[tuple]) -> str:
    out: List[str] = [MARKER]
    for b in blocks:
        if b[0] == "heading":
            out.append(f"### {b[1]}")
        elif b[0] == "warning":
            out.append(f"> [!WARNING]\n> {b[1]}")
        elif b[0] == "bold":
            out.append(f"**{b[1]}**")
        elif b[0] == "item":
            text, url, subs = b[1], b[2], b[3]
            out.append(f"- [{text}]({url})" if url else f"- **{text}**")
            out += [f"  - {s}" for s in subs]
        elif b[0] == "line":
            out.append(b[1])
        elif b[0] == "note":
            out.append(f"<sub>{b[1]}</sub>")
        else:
            out.append("")
    return "\n".join(out)


def _wiki_escape(text: str) -> str:
    return text.replace("|", "/").replace("[", "(").replace("]", ")")


def jira_wiki(blocks: List[tuple]) -> str:
    out: List[str] = []
    for b in blocks:
        if b[0] == "heading":
            out.append(f"h3. {b[1]}")
        elif b[0] == "warning":
            out.append("{panel:bgColor=#fffae6}" + b[1] + "{panel}")
        elif b[0] == "bold":
            out.append(f"*{b[1]}*")
        elif b[0] == "item":
            text, url, subs = _wiki_escape(b[1]), b[2], b[3]
            out.append(f"* [{text}|{url}]" if url else f"* *{text}*")
            out += [f"** {s}" for s in subs]
        elif b[0] == "line":
            out.append(b[1].replace("**", "*"))
        elif b[0] == "note":
            out.append("{color:#6b778c}" + b[1] + "{color}")
        else:
            out.append("")
    return "\n".join(out)


# ── GitHub ──────────────────────────────────────────────────────────────────

class GitHub:
    kind = "github"
    noun = "pull request"
    render = staticmethod(markdown)

    @staticmethod
    def _client(config: Row):
        repo, api, host = github.resolve(config)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if config.get("token"):
            headers["Authorization"] = f"Bearer {config['token']}"
        return repo, api, base.make_client({host}, headers=headers)

    @classmethod
    async def fetch(cls, config: Row, ref: str) -> Target:
        if not ref.lstrip("#").isdigit():
            raise ConnectorError("A pull request is named by its number, e.g. 42")
        number = int(ref.lstrip("#"))
        repo, api, client = cls._client(config)
        async with client:
            pr = await base.get_json(client, "GET", f"{api}/repos/{repo}/pulls/{number}", "GitHub")
            files = await base.get_json(client, "GET", f"{api}/repos/{repo}/pulls/{number}/files", "GitHub",
                                        params={"per_page": MAX_FILES})
        skip = "it's a draft" if pr.get("draft") else None if pr.get("state") == "open" else "it's closed"
        return Target(ref=str(number), title=pr.get("title") or "", body=(pr.get("body") or "")[:MAX_BODY],
                      url=pr.get("html_url") or "", noun=cls.noun, skip=skip,
                      files=[f.get("filename") for f in files if f.get("filename")][:MAX_FILES])

    @classmethod
    async def comment(cls, config: Row, target: Target, body: str, previous_id: Optional[str]) -> Tuple[str, str, bool]:
        repo, api, client = cls._client(config)
        async with client:
            existing = None
            for page in range(1, 6):
                comments = await base.get_json(client, "GET", f"{api}/repos/{repo}/issues/{target.ref}/comments",
                                               "GitHub", params={"per_page": 100, "page": page})
                existing = next((c for c in comments if MARKER in (c.get("body") or "")), None)
                if existing or len(comments) < 100:
                    break
            if existing:
                c = await base.get_json(client, "PATCH", f"{api}/repos/{repo}/issues/comments/{existing['id']}",
                                        "GitHub", json={"body": body})
                return str(c.get("id")), c.get("html_url") or "", True
            c = await base.get_json(client, "POST", f"{api}/repos/{repo}/issues/{target.ref}/comments", "GitHub",
                                    json={"body": body})
            return str(c.get("id")), c.get("html_url") or "", False


# ── Jira ────────────────────────────────────────────────────────────────────

class Jira:
    kind = "jira"
    noun = "issue"
    render = staticmethod(jira_wiki)

    @staticmethod
    def _client(config: Row):
        link = user_url(config["base_url"], "Jira URL")
        if config.get("email"):
            kwargs: Dict[str, Any] = {"auth": (config["email"], config["api_token"])}
        else:
            kwargs = {"headers": {"Authorization": f"Bearer {config['api_token']}"}}
        return str(link).rstrip("/"), base.make_client({link.host}, **kwargs)

    @classmethod
    async def fetch(cls, config: Row, ref: str) -> Target:
        key = ref.strip().upper()
        if not key or not all(part for part in key.split("-")) or "-" not in key:
            raise ConnectorError("A Jira issue is named by its key, e.g. ARCH-12")
        base_url, client = cls._client(config)
        async with client:
            issue = await base.get_json(client, "GET", f"{base_url}/rest/api/2/issue/{key}", "Jira",
                                        params={"fields": "summary,description,status"})
        f = issue.get("fields") or {}
        category = (((f.get("status") or {}).get("statusCategory") or {}).get("key") or "")
        return Target(ref=issue.get("key") or key, title=f.get("summary") or "",
                      body=str(f.get("description") or "")[:MAX_BODY], url=f"{base_url}/browse/{issue.get('key') or key}",
                      noun=cls.noun, skip="it's done" if category == "done" else None)

    @classmethod
    async def comment(cls, config: Row, target: Target, body: str, previous_id: Optional[str]) -> Tuple[str, str, bool]:
        base_url, client = cls._client(config)
        async with client:
            if previous_id:
                try:
                    c = await base.get_json(client, "PUT", f"{base_url}/rest/api/2/issue/{target.ref}/comment/{previous_id}",
                                            "Jira", json={"body": body})
                    return str(c.get("id") or previous_id), _jira_comment_url(target, c.get("id") or previous_id), True
                except ConnectorError as exc:
                    if "404" not in str(exc):          # deleted since: post a new one
                        raise
            c = await base.get_json(client, "POST", f"{base_url}/rest/api/2/issue/{target.ref}/comment", "Jira",
                                    json={"body": body})
            return str(c.get("id")), _jira_comment_url(target, c.get("id")), False


def _jira_comment_url(target: Target, comment_id: Any) -> str:
    return f"{target.url}?focusedCommentId={comment_id}"


# ── Linear ──────────────────────────────────────────────────────────────────

_LINEAR_ISSUE = "query Issue($id: String!) { issue(id: $id) { id identifier title description url completedAt canceledAt } }"
_LINEAR_CREATE = ("mutation Create($input: CommentCreateInput!) { commentCreate(input: $input) "
                  "{ success comment { id url } } }")
_LINEAR_UPDATE = ("mutation Update($id: String!, $input: CommentUpdateInput!) { commentUpdate(id: $id, input: $input) "
                  "{ success comment { id url } } }")


class Linear:
    kind = "linear"
    noun = "issue"
    render = staticmethod(markdown)

    @staticmethod
    def _client(config: Row):
        api = user_url(config["api_url"], "Linear API address")
        return str(api), base.make_client({api.host}, headers={"Authorization": config["api_key"]})

    @staticmethod
    async def _graphql(client, api: str, query: str, variables: Row) -> Row:
        data = await base.get_json(client, "POST", api, "Linear", json={"query": query, "variables": variables})
        if data.get("errors"):
            raise ConnectorError(f"Linear: {data['errors'][0].get('message', 'request failed')}")
        return data.get("data") or {}

    @classmethod
    async def fetch(cls, config: Row, ref: str) -> Target:
        ident = ref.strip().upper()
        if "-" not in ident:
            raise ConnectorError("A Linear issue is named by its identifier, e.g. ENG-42")
        api, client = cls._client(config)
        async with client:
            issue = (await cls._graphql(client, api, _LINEAR_ISSUE, {"id": ident})).get("issue")
        if not issue:
            raise ConnectorError(f"Linear has no issue {ident} that this key can see")
        closed = "it's done" if issue.get("completedAt") else "it's cancelled" if issue.get("canceledAt") else None
        return Target(ref=issue.get("identifier") or ident, title=issue.get("title") or "",
                      body=(issue.get("description") or "")[:MAX_BODY], url=issue.get("url") or "", noun=cls.noun,
                      skip=closed, native_id=issue.get("id"))

    @classmethod
    async def comment(cls, config: Row, target: Target, body: str, previous_id: Optional[str]) -> Tuple[str, str, bool]:
        api, client = cls._client(config)
        async with client:
            if previous_id:
                try:
                    out = (await cls._graphql(client, api, _LINEAR_UPDATE, {"id": previous_id, "input": {"body": body}}))
                    c = (out.get("commentUpdate") or {}).get("comment") or {}
                    if c.get("id"):
                        return c["id"], c.get("url") or "", True
                except ConnectorError as exc:
                    if "not found" not in str(exc).lower() and "entity" not in str(exc).lower():
                        raise
            out = await cls._graphql(client, api, _LINEAR_CREATE, {"input": {"issueId": target.native_id, "body": body}})
            c = (out.get("commentCreate") or {}).get("comment") or {}
            if not c.get("id"):
                raise ConnectorError("Linear didn't create the comment")
            return c["id"], c.get("url") or "", False


ADAPTERS = {a.kind: a for a in (GitHub, Jira, Linear)}


def slack_mrkdwn(blocks: List[tuple]) -> str:
    """Slack's mrkdwn: *bold*, <url|text> links, no headings."""
    def esc(text: str) -> str:            # Slack's three control characters
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out: List[str] = []
    for b in blocks:
        if b[0] in ("heading", "bold"):
            out.append(f"*{esc(b[1])}*")
        elif b[0] == "warning":
            out.append(f":warning: {esc(b[1])}")
        elif b[0] == "item":
            text, url, subs = esc(b[1]), b[2], b[3]
            out.append(f"• <{url}|{text}>" if url else f"• *{text}*")
            out += [f"      ◦ {esc(s)}" for s in subs]
        elif b[0] == "line":
            out.append(esc(b[1]).replace("**", "*"))
        elif b[0] == "note":
            out.append(f"_{esc(b[1])}_")
        else:
            out.append("")
    return "\n".join(out).strip()
