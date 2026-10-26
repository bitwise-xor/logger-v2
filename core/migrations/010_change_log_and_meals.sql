-- v2: the database keeps the change log and the meal numbers itself, for every write path (the logger, the web app,
-- NocoDB, a psql session); decided 25.10.2026. change_log says which row of which table changed, how, through which
-- login and from which message, and which columns changed: never the values (the catalogue keeps food_changes with
-- values, as in v1). Meals are renumbered whenever an entry is added or removed or its day, time or drink flag changes.
BEGIN;

CREATE TABLE change_log (
  id          bigserial PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  table_name  text NOT NULL,
  row_id      bigint,
  op          text NOT NULL CHECK (op IN ('insert','update','delete')),
  columns     text[],                    -- update: the columns that changed
  changed_by  text NOT NULL,             -- the login: core_rw = the logger, app_owner = migrations and imports, others by hand
  raw_id      bigint                     -- the message behind it when the writer says so (app.raw_id); no FK on purpose
);
CREATE INDEX change_log_row_idx ON change_log (table_name, row_id);
CREATE INDEX change_log_at_idx ON change_log (at);

CREATE FUNCTION log_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE cols text[]; rid bigint;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    SELECT array_agg(a.key ORDER BY a.key) INTO cols
      FROM jsonb_each(to_jsonb(OLD)) a JOIN jsonb_each(to_jsonb(NEW)) b USING (key)
      WHERE a.value IS DISTINCT FROM b.value;
    IF cols IS NULL THEN RETURN NULL; END IF;
    -- the 2-hour rule renumbering a timed entry is the database's own bookkeeping, not a change by anyone
    IF TG_TABLE_NAME = 'food_entries' AND cols = ARRAY['meal'] AND (to_jsonb(NEW) ->> 'time_known')::boolean THEN
      RETURN NULL;  -- (read through jsonb: plpgsql checks NEW's fields even for other tables)
    END IF;
    rid := (to_jsonb(NEW) ->> 'id')::bigint;
  ELSIF TG_OP = 'INSERT' THEN
    rid := (to_jsonb(NEW) ->> 'id')::bigint;
  ELSE
    rid := (to_jsonb(OLD) ->> 'id')::bigint;
  END IF;
  INSERT INTO change_log (table_name, row_id, op, columns, changed_by, raw_id)
  VALUES (CASE WHEN TG_TABLE_SCHEMA = 'public' THEN TG_TABLE_NAME ELSE TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME END,
          rid, lower(TG_OP), cols, session_user, nullif(current_setting('app.raw_id', true), '')::bigint);
  RETURN NULL;
END
$$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['food_entries', 'foods', 'food_units', 'food_defaults', 'recipes', 'recipe_items', 'usual_meals',
                           'usual_meal_items', 'food_targets', 'measurements', 'events', 'thoughts', 'thought_updates',
                           'thought_links', 'private.entries', 'lab_reports', 'lab_results', 'medications'] LOOP
    EXECUTE format('CREATE TRIGGER log_change_trg AFTER INSERT OR UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION log_change()', t);
  END LOOP;
END
$$;

CREATE FUNCTION food_entries_meals() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
$$
BEGIN
  IF TG_OP <> 'INSERT' THEN
    PERFORM assign_meals(OLD.day);
  END IF;
  IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.day IS DISTINCT FROM OLD.day) THEN
    PERFORM assign_meals(NEW.day);
  END IF;
  RETURN NULL;
END
$$;
-- assign_meals only writes the meal column, which is not in this list: no loop
CREATE TRIGGER food_entries_meals_trg AFTER INSERT OR DELETE OR UPDATE OF day, eaten_at, time_known, is_drink, day_rule
  ON food_entries FOR EACH ROW EXECUTE FUNCTION food_entries_meals();

GRANT SELECT ON change_log TO core_rw;

COMMIT;