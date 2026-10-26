#!/usr/bin/env bash
# Creates (or refreshes) the read-only database role for the Google Sheets sync and gives it its grants.
# The password comes from sheets/sync.env (SHEETS_READER_PASSWORD, never printed). Safe to run again, for example after a
# restore into a fresh database (roles are not part of a pg_dump of appdata). Run it as the user that can use docker.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENVFILE="${SYNC_ENV:-$HERE/../../sheets/sync.env}"
PW="$(grep '^SHEETS_READER_PASSWORD=' "$ENVFILE" 2>/dev/null | cut -d= -f2- || true)"
[ -n "$PW" ] || { echo "SHEETS_READER_PASSWORD is missing in $ENVFILE (type one there, for example from: openssl rand -hex 24)"; exit 1; }
docker exec -i logger-v2-db psql -v ON_ERROR_STOP=1 -q -U pgadmin -d appdata <<SQL
SELECT 'CREATE ROLE sheets_reader LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sheets_reader') \gexec
ALTER ROLE sheets_reader PASSWORD '$PW';
GRANT CONNECT ON DATABASE appdata TO sheets_reader;
GRANT USAGE ON SCHEMA public TO sheets_reader;
GRANT SELECT ON v_food_log, v_food_day, v_measurements, v_sleep, v_events TO sheets_reader;
SQL
echo "sheets_reader ready"
