# The load balancer: Caddy terminates TLS, serves the built Angular app, and
# round-robins API requests across the app instances (see deploy/Caddyfile).
# Build context: the repository root (deploy/docker-compose.yml sets it).

FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx ng build --configuration production

FROM caddy:2.11-alpine
COPY --from=frontend /src/dist/frontend/browser /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
