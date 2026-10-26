-- Read-only access for the Google Sheets sync (tools/sheets/). Creating the role needs a superuser and a password, so
-- tools/sheets/create-role.sh does that; this migration only gives an existing role its grants (nothing happens when
-- the role does not exist yet, create-role.sh grants the same things itself).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sheets_reader') THEN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO sheets_reader', current_database());
    GRANT USAGE ON SCHEMA public TO sheets_reader;
    GRANT SELECT ON v_food_log, v_food_day, v_measurements, v_sleep, v_events TO sheets_reader;
  END IF;
END
$$;
