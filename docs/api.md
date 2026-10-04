# API

All endpoints except `/auth/register`, `/auth/token`, `GET /health`, `GET /models/catalog` and `POST /models/bootstrap-install` need `Authorization: Bearer <token>`. Data endpoints act in the space named by the `X-Space` header (a group id, or your own user id); without it they use your personal space. Interactive docs are at `http://localhost:8000/docs`.

| Group | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/token` (form fields `username`, `password`; token valid for 8 h), `GET /auth/me` (role and permissions) |
| Admin *(server admins)* | `GET /admin/users` (with last activity and expiry), `POST /admin/users` (create with a role), `PATCH /admin/users/{id}` (change role), `GET /admin/retention` (what's due), `POST /admin/retention/run` |
| Spaces & groups | `GET /spaces`; `POST/GET /groups`, `PATCH/DELETE /groups/{id}`, `GET /groups/{id}/members`, `GET /groups/{id}/candidates?q=` (search users), `POST /groups/{id}/invitations`, `DELETE /groups/{id}/members/{user_id}`, `POST /groups/{id}/leave`; `GET /invitations`, `POST /invitations/{group_id}/accept`, `/decline` |
| Ingest | `POST /knowledge/artifacts` (text), `/artifacts/upload` (multipart), `/artifacts/url`, `/artifacts/transcript` |
| Artifacts | `PUT /knowledge/artifacts/{id}` (a content change re-extracts items), `DELETE /knowledge/artifacts/{id}`, `POST /knowledge/artifacts/{id}/share` (copy into another space) |
| Items | `GET/PUT/DELETE /knowledge/items/{id}`, `GET /knowledge/{ref}` (by id, id suffix or title) |
| Browse | `GET /knowledge` (everything in the space), `GET /knowledge/graph` |
| Review | `GET /knowledge/review`, `PATCH /knowledge/review/{id}` |
| Search | `GET /knowledge/search?q=&type=&source_type=&tag=&limit=` |
| Links | `POST /knowledge/link`, `GET /knowledge/links` |
| Lineage | `GET /knowledge/lineage/kinds`, `GET/POST /knowledge/items/{id}/lineage`, `DELETE /knowledge/items/{id}/lineage/{other}`, `GET …/lineage/suggestions`, `PUT /knowledge/items/{id}/status`, `GET /knowledge/register` |
| Playbooks | `GET /knowledge/playbooks`, `POST /knowledge/playbooks` (same title replaces), `DELETE /knowledge/playbooks/{id}` |
| OKF | `GET /knowledge/okf/export`, `POST /knowledge/okf/import` |
| GraphRAG | `POST /knowledge/graphrag/query`, `POST /knowledge/reembed` |
| Connectors | `GET /connectors/kinds`, `GET/POST /connectors`, `PATCH/DELETE /connectors/{id}`, `POST /connectors/{id}/sync`, `GET /connectors/{id}/documents` |
| Models (public) | `GET /models/catalog` (which of the three are installed), `POST /models/bootstrap-install` (first download; only while none is installed) |
| Models | `GET /models/status`, `/models/local`, `/models/system-info`; `POST /models/set-default` (switch your model); `POST /models/install` (streamed progress), `/models/remove` *(server admins)* |
| Health | `GET /health`: `status` (`healthy` / `degraded`), `arcadedb_nodes` (ready / configured), `background` (`worker` / `in-process`), `queue` |
