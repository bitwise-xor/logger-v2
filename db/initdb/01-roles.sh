#!/bin/bash
# Runs once, on the first start with an empty data folder (like v1's init script).
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres <<SQL
CREATE ROLE app_owner LOGIN PASSWORD '${APP_OWNER_PASSWORD}';
CREATE ROLE core_rw   LOGIN PASSWORD '${CORE_RW_PASSWORD}';
CREATE DATABASE appdata OWNER app_owner;
REVOKE CONNECT ON DATABASE appdata FROM PUBLIC;
GRANT  CONNECT ON DATABASE appdata TO app_owner, core_rw;
SQL
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d appdata <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO app_owner;
GRANT USAGE ON SCHEMA public TO core_rw;
SQL
