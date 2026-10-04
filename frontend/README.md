# Knowledge Hubs frontend

The Angular 21 single-page app. It calls the FastAPI backend on its own origin; in development, [`proxy.conf.cjs`](proxy.conf.cjs) forwards the API paths to `http://localhost:8000`.

Requires Node 20.19+ or 22.12+.

```bash
npm install
npm start          # http://localhost:4200, with live reload (start the backend first)
npm run build      # production build in dist/frontend/browser
npm test -- --watch=false --browsers=ChromeHeadless   # unit tests (needs Chrome)
```

- **Setting up the whole app:** [Getting started](../docs/getting-started.md)
- **How the frontend talks to the backend:** [Frontend ↔ backend](../docs/getting-started.md#frontend--backend)
- **Code layout:** `src/app/pages/` holds one component per page, `components/` the shared pieces, `services/` auth, spaces, models and HTTP helpers, and `models/api.ts` the API types.
