#!/usr/bin/env bash
# A throwaway v2 database with v1's data, built the way the real import will work:
# v2 migrations, then v1's rows (data only), then tools/import/v1_fixes.sql.
# Usage: tools/import/sandbox.sh up <v1-dump-file> | down        (Postgres on 127.0.0.1:55435)
set -euo pipefail
NAME=logger-v2-sandbox; PORT=55435
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
case "${1:-}" in
down) docker rm -f "$NAME" >/dev/null 2>&1 || true; echo "sandbox removed"; exit 0;;
up) DUMP="${2:?give the v1 dump file}";;
*) echo "usage: $0 up <v1-dump> | down"; exit 1;;
esac
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p 127.0.0.1:$PORT:5432 \
  -e POSTGRES_USER=pgadmin -e POSTGRES_PASSWORD=sandbox -e APP_OWNER_PASSWORD=sandbox-owner -e CORE_RW_PASSWORD=sandbox-core \
  -v "$HERE/db/initdb:/docker-entrypoint-initdb.d:ro" postgres:17.11 >/dev/null
for i in $(seq 1 90); do [ "$(docker logs "$NAME" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ] && break; sleep 1; done
PSQL="docker exec -i $NAME psql -q -v ON_ERROR_STOP=1 -U pgadmin -d appdata"
( echo "SET ROLE app_owner;"; cat "$HERE"/core/migrations/0*.sql ) | $PSQL >/dev/null
echo "TRUNCATE log_types CASCADE;" | $PSQL      # migration 002 seeds the v1 type list; v1's own rows replace it
docker exec -i "$NAME" pg_restore -U pgadmin -d appdata --data-only --disable-triggers --no-owner --no-acl < "$DUMP"
$PSQL < "$HERE/tools/import/v1_fixes.sql"
echo "sandbox ready on 127.0.0.1:$PORT (v2 schema, v1 data, fixes applied)"
