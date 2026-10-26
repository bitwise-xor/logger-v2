#!/usr/bin/env bash
# Rebuilds the sheet layout (sheets_layout.py) while holding the sync's lock, so the cron sync cannot run in between.
set -a; . /opt/stacks/logger-v2/sheets/sync.env; set +a
exec 9>/opt/stacks/logger-v2/sheets/lock
flock 9
/opt/stacks/logger-v2/sheets/.venv/bin/python /opt/stacks/logger-v2/tools/sheets/sheets_layout.py "$@"