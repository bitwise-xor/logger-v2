#!/usr/bin/env bash
# Throwaway Postgres 17 for the tests (port 55433). Usage: tests/test-db.sh up | down
set -euo pipefail
NAME=logger-v2-testdb; PORT=55433
HERE="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-}" in
down) docker rm -fv "$NAME" >/dev/null 2>&1 || true; echo "test db removed"; exit 0;;
up) ;;
*) echo "usage: $0 up|down"; exit 1;;
esac
docker rm -fv "$NAME" >/dev/null 2>&1 || true
# Throwaway data, so speed over safety (core item 28, 02.11.2026): the data in memory, no syncing to disk (each test
# empties about 30 tables first, 340 ms with syncing), and a cheap password check at each connection (50 ms before).
docker run -d --name "$NAME" -p 127.0.0.1:$PORT:5432 --tmpfs /var/lib/postgresql/data:rw,size=512m \
  -e POSTGRES_USER=pgadmin -e POSTGRES_PASSWORD=testsuper -e APP_OWNER_PASSWORD=testowner -e CORE_RW_PASSWORD=testcore \
  -v "$HERE/db/initdb:/docker-entrypoint-initdb.d:ro" postgres:17.11 \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c scram_iterations=1 >/dev/null
for i in $(seq 1 60); do
  docker exec "$NAME" psql -U pgadmin -d appdata -c 'select 1' >/dev/null 2>&1 && break; sleep 1
done
echo "test db ready on 127.0.0.1:$PORT"
