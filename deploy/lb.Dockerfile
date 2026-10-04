# The load balancer: Caddy terminates TLS, serves the built Angular app, and
# round-robins API requests across the app instances (see deploy/Caddyfile).
# Build context: the repository root (deploy/docker-compose.yml sets it).

FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx ng build --configuration production

# 2.10, not 2.11: Caddy 2.11 cancels proxied requests after 60 s (499), which
# breaks GraphRAG answers that take longer on a CPU-only machine
FROM caddy:2.10-alpine
COPY --from=frontend /src/dist/frontend/browser /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
