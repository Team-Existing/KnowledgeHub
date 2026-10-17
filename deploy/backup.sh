#!/bin/sh
# Back up the Knowledge Hubs database to deploy/backups/ (see docs/deployment.md).
#
#   deploy/backup.sh                      run from anywhere, while the stack is up
#   0 3 * * * /path/to/deploy/backup.sh   nightly, from the host's crontab
#
# ArcadeDB writes a consistent snapshot (BACKUP DATABASE, as the app's own user:
# the root password isn't needed); the zip is copied out of the container and
# removed from it, and local backups older than BACKUP_KEEP_DAYS are deleted.
# Copy deploy/backups/ off this machine too: a backup on the same disk doesn't
# survive losing the disk.
#
#   BACKUP_DIR=./backups   BACKUP_KEEP_DAYS=14   ENV_FILE=.env
#   BACKUP_CONTAINER=      the ArcadeDB container (default: this stack's arcadedb-0)
set -eu
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: don't rewrite the container paths below
cd "$(dirname "$0")"

BACKUP_DIR=${BACKUP_DIR:-./backups}
KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
ENV_FILE=${ENV_FILE:-.env}

env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed 's/^"\(.*\)"$/\1/'; }
DB=$(env_value ARCADEDB_DATABASE); DB=${DB:-knowledge_hubs}
APP_USER=$(env_value ARCADEDB_APP_USER); APP_USER=${APP_USER:-kh_app}
APP_PASSWORD=$(env_value ARCADEDB_APP_PASSWORD)
[ -n "$APP_PASSWORD" ] || { echo "backup: ARCADEDB_APP_PASSWORD is not set in $ENV_FILE" >&2; exit 1; }

CONTAINER=${BACKUP_CONTAINER:-$(docker compose ps -q arcadedb-0)}
[ -n "$CONTAINER" ] || { echo "backup: arcadedb-0 isn't running (docker compose up -d)" >&2; exit 1; }

AUTH=$(printf '%s:%s' "$APP_USER" "$APP_PASSWORD" | base64 | tr -d '\n')
RESULT=$(docker exec "$CONTAINER" wget -q -O - \
  --header "Authorization: Basic $AUTH" --header "Content-Type: application/json" \
  --post-data '{"language":"sql","command":"BACKUP DATABASE"}' \
  "http://localhost:2480/api/v1/command/$DB") || { echo "backup: ArcadeDB refused the backup" >&2; exit 1; }
FILE=$(printf '%s' "$RESULT" | sed -n 's/.*"backupFile":"\([^"]*\)".*/\1/p')
[ -n "$FILE" ] || { echo "backup: unexpected answer from ArcadeDB: $RESULT" >&2; exit 1; }

mkdir -p "$BACKUP_DIR"
docker cp "$CONTAINER:/home/arcadedb/backups/$DB/$FILE" "$BACKUP_DIR/$FILE"
docker exec "$CONTAINER" rm -f "/home/arcadedb/backups/$DB/$FILE"
find "$BACKUP_DIR" -name "$DB-backup-*.zip" -mtime +"$KEEP_DAYS" -delete
echo "$BACKUP_DIR/$FILE"
