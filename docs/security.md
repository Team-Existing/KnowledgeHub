# Security notes

How Knowledge Hubs protects accounts and data. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

**Accounts**
- **Passwords and tokens.** Passwords are hashed with bcrypt (which reads only the first 72 bytes of a password), on a worker thread so a sign-in doesn't stall other requests. New passwords need at least 8 characters, can't be one of the passwords guessing tools try first, and can't be the username; existing passwords keep working. Signing in with an unknown username runs the same bcrypt check against a dummy hash, so the response time doesn't reveal which usernames exist (before: 15 ms against 250 ms). Tokens are HS256 JWTs (PyJWT) signed with `SECRET_KEY` and expire after 8 hours; a token without an expiry is refused. The frontend sends the token only to its own API.
- **The first account.** It becomes the server admin. With `SETUP_TOKEN` set (required in the production stack), registering it needs that token, compared in constant time, so whoever finds a new server first can't claim it.
- **Throttling.** Failed sign-ins are limited to 5 per account and 30 in total per client address every 15 minutes, and sign-ups to 20 per address an hour. Past that the API answers 429 with `Retry-After`, before checking the password. A successful sign-in clears that account's count. In the production stack the counters are in Redis, so the limit holds across app instances. If Redis is unreachable the limits aren't enforced, rather than blocking sign-in; after a failure Redis is left alone for 30 seconds, so sign-in doesn't wait on it each time. The per-account limit is keyed by address too, so nobody can lock a user out by failing on purpose from elsewhere.
- **Signing out.** Each token carries the account's session version. **Sign out** (`POST /auth/logout`) increments it, so every token issued before stops working at once, a copied one included.
- **Signing key.** The server refuses to start if `SECRET_KEY` is missing, shorter than 32 characters, or one of the placeholders this repo has ever shipped. Anyone who knows the key could sign in as any account.
- **Roles.** [`backend/app/rbac.py`](../backend/app/rbac.py) maps roles to permissions, and each route declares the permission it needs. Roles are read from the database on every request, so a change applies to existing sessions at once. An unknown role grants nothing, and the last server admin can't be demoted.
  - *Upgrading:* databases from before roles existed had every account as admin. On first start, the earliest account stays admin and every other admin becomes a member (logged once).
- **Usernames are unique, ignoring case.** `Alice`, `alice` and ` ALICE ` are the same name, and sign-in isn't case-sensitive; the name keeps the casing it was created with. New names are limited to ASCII letters, digits, `.`, `_` and `-`, so look-alike characters (a Cyrillic `а` for a Latin `a`) can't create a second account that reads the same in member lists and invitations. A UNIQUE index on the normalised name (`User.username_key`, see [`backend/app/usernames.py`](../backend/app/usernames.py)) enforces it in the database too.
  - *Upgrading:* existing accounts that differ only in case keep working with their exact name and are listed in a startup warning.

**Data isolation**
- **Spaces.** Every query is scoped to the active space, and the backend checks membership before using a group's space. Artifact and item ids are hashes that include the space id, so the same document in two spaces gives separate nodes, and edges are only ever created between nodes of the same space. The test suite checks all of this.
- **Query safety.** All values are passed to ArcadeDB as bound parameters. Full-text queries are rebuilt from word characters only, so user input can't inject Lucene operators.
- **Rendering.** GraphRAG answers are HTML-escaped before citation badges are inserted.

**Infrastructure (production stack)**
- **A dedicated database user.** The app connects as a user that can read, write and change the schema in its own database, and can't create databases or see any other. Only the one-off `provision` job and the ArcadeDB nodes ever receive the root password; this was checked against the stack's actual configuration.
- **TLS everywhere outside.** Caddy terminates HTTPS, redirects HTTP, and adds HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy` and `Permissions-Policy`.
- **Content Security Policy.** Only the site's own scripts run: no inline scripts, event handlers or other origins, and the page can't be framed. Injected markup therefore can't run code that would read the sign-in token from `localStorage`.
- **No public API docs.** The production stack sets `API_DOCS=false`, which removes `/docs`, `/redoc` and `/openapi.json`. Internal traffic stays on the Compose network; only the load balancer publishes ports.
- **Secrets stay out of images.** `.dockerignore` files exclude every `.env`, and each container gets only the variables `docker-compose.yml` lists for it. Redis requires a password.

**Connectors and credentials**
- **Stored credentials.** Connector tokens and API keys are encrypted with AES-256-GCM before they reach ArcadeDB ([`backend/app/crypto.py`](../backend/app/crypto.py)). Each value is bound to its connector and field, so ciphertext copied elsewhere won't decrypt. They're decrypted only in memory during a sync and never returned by the API (shown as `********`). Without `CREDENTIALS_KEY` they can't be saved at all. Plaintext values left by older versions are encrypted at startup; until then they're refused.
  - *Rotating the key:* set `CREDENTIALS_KEY=<new>,<old>`, restart (everything is re-encrypted with the new key), then drop `<old>`.
- **Outbound connections.** Each sync's HTTP client is locked to the hosts the connector's own settings name, so redirects and URLs inside API responses that point anywhere else are refused before a request, or a token, leaves the server.
- **Folder connectors.** Disabled unless `CONNECTOR_ROOTS` is set. Paths are checked against it when a connector is saved and again at every sync. Directory symlinks aren't followed, and file symlinks that resolve outside the folder are skipped. `git` runs with the repository's `fsmonitor`, hooks and pager overridden and system config ignored, so a scanned repository can't make the server run programs.

**Input**
- **URL fetching.** Only `http(s)` is allowed. Hosts that resolve to private, loopback, link-local (including cloud metadata) or reserved addresses are rejected, and every redirect hop is checked again. The check is repeated when connecting, and the connection goes to exactly the address that was checked, so a DNS server that answers differently the second time (DNS rebinding) can't redirect it. Proxy settings from the environment are ignored for these fetches.
- **Uploads.** Oversized bodies get a 413 before they're buffered. PDF and DOCX are parsed from Starlette's spooled temp file rather than read into memory. Legacy `.doc` is rejected; save it as `.docx`.

**Local Docker stack.** [`docker-compose.yml`](../docker-compose.yml) publishes only the web port, on `127.0.0.1` unless you set `KH_BIND`. ArcadeDB and Ollama are reachable only inside the Compose network, which is why ArcadeDB can use a default password there. The generated `SECRET_KEY` and `CREDENTIALS_KEY` are files readable only by the app user, in the `backend-secrets` volume. Use [`deploy/`](deployment.md) for anything exposed beyond your machine or network.

**Retention.** Inactive accounts and groups are deleted permanently (see [Inactive accounts and groups](retention.md)). Back up the `arcadedb-data` volume if you need to recover from that.
