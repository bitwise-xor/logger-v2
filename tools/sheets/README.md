# Google Sheets sync

Feeds the Google Sheet "Food Log (view)" from v2 every 2 minutes. Values only; the layout, formulas, colours and charts live in the sheet.

| File | What it does |
|---|---|
| `sheets_sync.py` | Reads the views `v_food_log`, `v_food_day`, `v_measurements`, `v_sleep`, `v_events` as the read-only role `sheets_reader` (through `docker exec` into the database container) and rewrites the data tabs Log (90 days), Daily totals, Day rows, Health, Habits, Stats, plus the "Updated" time on the two summary tabs. Skips the big write when nothing changed. Options: `--check`, `--dry-run`, `--force`. |
| `sheets_layout.py` | Builds the layout: Daily summary, Weekly summary, Health, Habits, Daily totals (visible); Day rows, Chart data, Stats (hidden). Deletes and recreates those tabs, fills the data once and then adds the charts. |
| `run-sync.sh` | One sync run with the settings from `sheets/sync.env`. Cron runs it every 2 minutes: `*/2 * * * * /opt/stacks/logger-v2/tools/sheets/run-sync.sh`. |
| `run-layout.sh` | Rebuilds the layout while holding the sync's lock file, so the cron run cannot interfere. |
| `create-role.sh` | Creates or refreshes the database role `sheets_reader` (needs a superuser, so it cannot be a migration) and gives it its grants. Run it once on a new database. |

Secrets and state live in `sheets/` (git-ignored): `sync.env` (SHEET_ID, SA_KEY, DB_CONTAINER, DB_USER, DB_NAME, SHEETS_READER_PASSWORD), the Google service account key, the Python venv, `status.txt`, `last_hash.txt`, `lock`.
Set up the venv with `python3 -m venv sheets/.venv && sheets/.venv/bin/pip install google-auth requests`.

The migration `core/migrations/013_sheets_reader.sql` gives an existing `sheets_reader` role its grants.
