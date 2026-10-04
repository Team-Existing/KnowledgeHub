# Security policy

## Supported versions

Security fixes are made on `main` and included in the next release. Please run the latest release or `main`.

## Reporting a vulnerability

**Please don't report security issues in public GitHub issues, discussions or pull requests.**

Report them privately through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/itisar-345/KnowledgeHubby/security/advisories/new)).

Please include:

- what the issue is and what an attacker could do with it
- the steps to reproduce it, or a proof of concept
- the version or commit you tested, and your setup (Docker one-command stack, `deploy/`, or from source)

What to expect:

- an acknowledgement within 7 days
- an assessment and, if confirmed, a plan for a fix; we'll keep you updated
- credit in the advisory and the changelog once a fix is released, unless you'd rather stay anonymous

Please give us a reasonable amount of time to release a fix before you disclose the issue publicly.

## Scope

Things we especially want to hear about:

- reading or changing data in a space you don't belong to
- getting permissions you shouldn't have (for example, server admin)
- signing in as someone else, or forging tokens
- reading stored connector credentials
- making the server contact addresses a user didn't configure (SSRF), or read files outside `CONNECTOR_ROOTS`
- running code on the server through uploaded files, URLs or repositories

How the app is designed to prevent these is described in [docs/security.md](docs/security.md). Issues already listed in [Known limitations](docs/known-limitations.md) (such as the DNS-rebinding exposure of URL fetching) are known, but reports with a practical exploit or a fix are still welcome.
