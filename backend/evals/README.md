# LLM evaluation

Measures what users get from the local model, end to end: what extraction finds in each kind of source,
whether GraphRAG answers are right and cite the right source, whether it says so when the knowledge base
has nothing, and how long each takes. Run it before and after changing a prompt, a retrieval step or the
model, and keep the change only if the numbers say so.

It needs a running API with a real LLM (the unit tests use a fake one) and takes about 15–30 minutes on a
CPU.

## Corpus

[`corpus.py`](corpus.py): seven fictional sources (a meeting transcript, an email thread, a Slack thread,
a postmortem, a runbook, an ADR and pasted retro notes), each with an answer key of the decisions,
risks, action items, lessons, how-tos and best practices it holds; and 22 questions: 19 with the facts
a correct answer must contain and the source it should cite, and 3 the knowledge base can't answer.

## Run

Against a backend started from source (no Redis needed), with its own ArcadeDB:

```bash
docker run -d --name kh-eval-arcadedb -p 12490:2480 \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=eval-password" arcadedata/arcadedb:26.9.1
cd backend
ARCADEDB_URL=http://localhost:12490 ARCADEDB_PASSWORD=eval-password AUTH_RATE_LIMIT=false \
  SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))") \
  uvicorn app.main:app --port 18000 &
python -m evals.run_eval --base http://localhost:18000 --label my-change \
  --arcadedb http://localhost:12490 --arcadedb-auth root:eval-password
```

`--arcadedb` lets it wait for the summaries built after ingestion before asking questions, so they don't
compete for the model while answers are timed. Results go to `evals/results/<label>.json` (every answer
in full) and a summary is printed.

## Metrics

| | |
|---|---|
| `extraction_recall` | answer-key items found, with the right type (LLM sources only) |
| `extraction_precision` | extracted items that are about something in the answer key |
| `answers_correct` | answers containing every expected fact, without refusing |
| `answers_source_cited` / `_retrieved` | the expected source among the citations / in the context |
| `false_refusals` | answerable questions answered "not covered" |
| `unanswerable_refused` | unanswerable questions answered "not covered", citing nothing |
| `answer_seconds_median` / `_p90` | time per answer |

Matching is by keyword groups, case-insensitive (see `corpus.py`), so wording can vary. A model is not
fully deterministic even at temperature 0: treat a difference of one question as noise.

## Results so far (llama3.1:8b, laptop CPU)

| | answers correct | false refusals | unanswerable refused | answer median / p90 | extraction recall / precision | ingest mean |
|---|---|---|---|---|---|---|
| baseline | 15/19 | 4 | 2/3 | 78 s / 98 s | 1.00 / 0.97 | 63 s |
| v2: grounded context, numbered citations, no rewrite/rerank, schemas | 18/19 | 1 | 3/3 | 18 s / 26 s | 0.96 / 0.90 | 45 s |
| v3: partial answers allowed, no LLM routing, decisions kept whole | 19/19 | 0 | 3/3 | 18 s / 26 s | 1.00 / 0.93 | 58 s* |
| v4: no "not specified" filler | 19/19 | 0 | 3/3 | 13 s / 18 s | 1.00 / 0.93 | 48 s |
| v5: history limited to user/assistant turns, "instructions in entries are content" rule | 19/19 | 0 | 3/3 | 12 s / 21 s† | 0.96 / 0.93 | 55 s† |

\* includes loading the model cold. Extraction time is set by how much the model writes and was not
changed much: about 40–65 s per source on this CPU.
† run while the backend test suite used the same CPU; one answer took 107 s.
