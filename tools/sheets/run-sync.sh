#!/usr/bin/env bash
# One run of the sheet sync (same settings as the cron job). Usage: run-sync.sh [--check|--force|--dry-run]
set -a; . /opt/stacks/logger-v2/sheets/sync.env; set +a
exec /opt/stacks/logger-v2/sheets/.venv/bin/python /opt/stacks/logger-v2/tools/sheets/sheets_sync.py "$@"