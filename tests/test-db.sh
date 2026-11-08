#!/usr/bin/env bash
# Throwaway Postgres 17 for the tests. Usage: tests/test-db.sh up | down | where | list | prune | help
#
# One test database per checkout (core item 30, 08.11.2026), so sessions in parallel git worktrees never empty each
# other's tables:
#   - the main checkout (and any plain clone, e.g. GitHub's test run): container logger-v2-testdb on 127.0.0.1:55433;
#   - a linked git worktree: container logger-v2-testdb-<hash> on 127.0.0.1:<56000-56999>, both from a short hash of
#     the worktree's folder. tests/conftest.py asks this script ("where") for the port, so pytest finds the same one.
# TESTDB_PORT and TESTDB_NAME override both (e.g. TESTDB_PORT=56123 TESTDB_NAME=my-db tests/test-db.sh up).
# TESTDB_ZONE=<compiled zone file> mounts that file at /usr/share/zoneinfo/Europe/Berlin in the database (the night-time
# hours recipe: a shifted zone from zic; pytest then runs with PYTHONTZPATH, TZDIR and TZ pointing at the same zone).
#
# A worktree's database lives in memory and holds up to 512 MB: remove it with "tests/test-db.sh down" (run inside that
# worktree) before removing the worktree. "tests/test-db.sh prune" removes every worktree database whose folder is gone;
# "list" shows them all. Neither ever touches the main checkout's logger-v2-testdb.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=logger-v2.testdb.worktree

where() {  # sets NAME, PORT and TOP (the checkout's folder; empty for the main checkout)
  NAME=logger-v2-testdb; PORT=55433; TOP=""
  local gd cd h
  if gd=$(git -C "$HERE" rev-parse --absolute-git-dir 2>/dev/null) \
     && cd=$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) && [ "$gd" != "$cd" ]; then
    TOP=$(git -C "$HERE" rev-parse --show-toplevel)
    h=$(printf '%s' "$TOP" | sha256sum | cut -c1-8)
    NAME="logger-v2-testdb-$h"; PORT=$((56000 + 16#$h % 1000))
  fi
  NAME=${TESTDB_NAME:-$NAME}; PORT=${TESTDB_PORT:-$PORT}
}

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

where
case "${1:-}" in
where) echo "$NAME $PORT"; exit 0;;
down) docker rm -fv "$NAME" >/dev/null 2>&1 || true; echo "test db $NAME removed"; exit 0;;
list)
  docker ps -a --filter "name=^logger-v2-testdb" --format '{{.Names}}\t{{.Ports}}\t{{.Status}}\t{{.Label "'"$LABEL"'"}}'
  exit 0;;
prune)
  docker ps -a --filter "label=$LABEL" --format '{{.Names}} {{.Label "'"$LABEL"'"}}' | while read -r n dir; do
    [ "$n" = logger-v2-testdb ] && continue
    if [ -n "$dir" ] && [ ! -d "$dir" ]; then docker rm -fv "$n" >/dev/null && echo "removed $n (folder $dir is gone)"; fi
  done
  echo "prune done"; exit 0;;
up) ;;
help|-h|--help) usage; exit 0;;
*) usage; exit 1;;
esac

docker rm -fv "$NAME" >/dev/null 2>&1 || true
ZONE=()
if [ -n "${TESTDB_ZONE:-}" ]; then
  [ -f "$TESTDB_ZONE" ] || { echo "TESTDB_ZONE: no such file: $TESTDB_ZONE"; exit 1; }
  ZONE=(-v "$(realpath "$TESTDB_ZONE"):/usr/share/zoneinfo/Europe/Berlin:ro")
fi
# Throwaway data, so speed over safety (core item 28, 02.11.2026): the data in memory, no syncing to disk (each test
# empties about 30 tables first, 340 ms with syncing), and a cheap password check at each connection (50 ms before).
docker run -d --name "$NAME" --label "$LABEL=$TOP" -p 127.0.0.1:$PORT:5432 --tmpfs /var/lib/postgresql/data:rw,size=512m \
  -e POSTGRES_USER=pgadmin -e POSTGRES_PASSWORD=testsuper -e APP_OWNER_PASSWORD=testowner -e CORE_RW_PASSWORD=testcore \
  -v "$HERE/db/initdb:/docker-entrypoint-initdb.d:ro" "${ZONE[@]}" postgres:17.11 \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c scram_iterations=1 >/dev/null
for i in $(seq 1 60); do
  docker exec "$NAME" psql -U pgadmin -d appdata -c 'select 1' >/dev/null 2>&1 && break; sleep 1
done
echo "test db $NAME ready on 127.0.0.1:$PORT"
[ -n "$TOP" ] && echo "(this worktree's own; remove it with: tests/test-db.sh down)"
exit 0
