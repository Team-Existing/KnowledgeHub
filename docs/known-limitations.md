# Known limitations

**Models and search**
- **One LLM, one embedding model.** Only Ollama and local sentence-transformers are implemented.
- **Models are per machine, choices are per user.** Everyone on one server shares the same Ollama, so removing a model affects every account. Anyone using it is moved to another installed model.
- **Full-text search doesn't fold accents.** The default analyzer means `cafe` won't match "Café".
- **Lists come a page at a time.** The Hub, review queue, decision log and graph show the newest (or, for review, oldest) page with **Load more**; the Hub's filter runs on the server. The graph draws at most the 300 newest nodes. The status counts on the decision log cover the loaded entries.
- **Sources are found through their items.** A source in search results opens an item extracted from it, whose page shows the source; a source nothing was extracted from has no page.
- **Search slows on very broad queries.** On 50,000 items a precise query takes about 75 ms, but a one-word query matching 30,000+ items takes about 300 ms, mostly spent counting the total.
- **Extracted text isn't size-capped.** Uploads and URLs are capped by `MAX_UPLOAD_MB`, but the text extracted from them isn't (pasted text is capped at 100,000 characters). LLM extraction reads at most 8 chunks.

**Connectors**
- **No scheduled syncs.** By design, a connector only syncs when someone clicks **Sync now**.
- **A crashed worker's sync waits for its timeout.** A worker killed outright (not stopped) leaves its sync queued until `CELERY_VISIBILITY_TIMEOUT` (default 2 h) passes, then it runs again. A normal stop or redeploy lets the sync finish. In development, restarting the API mid-sync marks it "interrupted"; sync again.
- **Deleted source files stay.** Removing a file or closing an issue at the source doesn't remove what was already imported.
- **Group connectors share one credential.** Every member of a group can sync a group connector with the token whoever added it saved. Use a token scoped to what the group should see.
- **Per-sync caps.** GitHub reads at most 10 pages of closed pull requests per sync; each connector has a `max` setting for items per sync.

**Accounts**
- **Signing out ends every session of the account.** Tokens can't be revoked one at a time: **Sign out** on one device signs you out on all of them.
- **Sign-in throttling counts by client address.** Many people behind one address (an office NAT) share the limit of 30 failed sign-ins per 15 minutes. If Redis is down, the limits aren't enforced, rather than blocking sign-in.
- **Tokens live in the browser's `localStorage`.** The Content Security Policy stops injected scripts, which are what could read it, and signing out revokes the token on the server.

**Data**
- **Deletion is permanent.** Inactivity retention, group deletion and removing a connector's data can't be undone; back up the `arcadedb-data` volume.
- **Shared copies drop lineage links.** Sharing a source into another space copies its items, statuses and review state, but not links to items outside it.
- **Single-instance setups connect to ArcadeDB as `root`.** Running from source and the one-command Docker stack use `ARCADEDB_USER=root` for simplicity. The production stack uses a dedicated app user.

**Production stack**
- **The cluster needs a majority.** Three nodes survive one being down. With two down there's no leader, and writes stop until a node returns.
- **Reads don't spread over the nodes.** Every app instance sends its reads and writes to the current leader, so that a read always sees the write before it. The other nodes are standbys: they keep a copy and take over when the leader goes. More nodes add resilience, not read capacity.
- **One Ollama for everything.** Every app instance and the worker call the same Ollama on the Docker host, so LLM throughput doesn't grow with more instances.
- **Exactly one `beat`.** Two would schedule the clean-up twice. It's harmless, because runs are locked, but wasteful.
- **No horizontal Redis.** Redis is a single container. If it's down, starting a sync returns "background queue unavailable" (503), and the Sources page can't show live sync state. Everything else (reading, ingesting, search, GraphRAG) keeps working, and `/health` reports `degraded`. This was tested against the stack.

**Performance and network**
- **Writes are one HTTP round trip per statement.** An ingest of N items makes roughly 3N calls to ArcadeDB. That's fine on the same machine, but slower if ArcadeDB is remote.
