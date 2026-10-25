-- Copied from logger-app sql/schema-v5-food.sql (v1 schema v5 food). Only change: role mcp_writer -> core_rw, NocoDB and sheets_reader grants removed.
-- schema-v5-food.sql  (additive only: new tables, views, functions; nothing existing is changed)
-- Run as app_owner in database appdata:  psql -v ON_ERROR_STOP=1 -U app_owner -d appdata -f schema-v5-food.sql
-- Whole file is one transaction: on any error nothing is created.
-- Needs schema v2 (raw_inbox) and v3 (events, log_types, log_day) to exist.
--
-- Shape (design: food-design-v1.txt, decisions of 14.10.2026):
--   foods            catalogue (replaces the Foods tab). NULL = unknown, never zero.
--   food_entries     the log (replaces Log 2026), each entry keeps a nutrient SNAPSHOT.
--   food_changes     written by a trigger on every change to a food (who, old/new values, chat message).
--   food_units       household units in grams.   food_defaults  "skyr" -> your default product.
--   recipes + recipe_items, usual_meals + usual_meal_items, food_targets (periods with a start date).
-- Basis: foods.basis is 'g' (values per 100 g) or 'ml' (per 100 ml). Entry amounts are in the same unit.
-- Session settings read by the triggers (set by the connector with SET LOCAL, optional):
--   app.raw_id = chat message that caused the change.

BEGIN;

-- ---------------------------------------------------------------- catalogue
CREATE TABLE foods (
  id                 serial PRIMARY KEY,                       -- display id N<id>
  name               text NOT NULL UNIQUE,
  aliases            text[] NOT NULL DEFAULT '{}',
  basis              text NOT NULL DEFAULT 'g' CHECK (basis IN ('g','ml')),
  kcal               numeric NOT NULL CHECK (kcal >= 0),
  protein            numeric NOT NULL CHECK (protein >= 0),
  fat                numeric NOT NULL CHECK (fat >= 0),
  sat_fat            numeric CHECK (sat_fat >= 0),
  carbs              numeric NOT NULL CHECK (carbs >= 0),
  fibre              numeric CHECK (fibre >= 0),
  sugar_total        numeric CHECK (sugar_total >= 0),
  sugar_added        numeric CHECK (sugar_added >= 0),
  sodium_mg          numeric CHECK (sodium_mg >= 0),
  caffeine_mg        numeric CHECK (caffeine_mg >= 0),
  carbs_include_fibre boolean,                                 -- NULL = unknown (D3 convention per food)
  source             text NOT NULL CHECK (source IN ('label','database','estimate','calculated')),
  source_note        text,
  source_date        date,
  brand              text,
  barcode            text,
  category           text,
  state              text CHECK (state IN ('raw','cooked','dry')),
  variant_of         integer REFERENCES foods(id),             -- raw <-> cooked partner
  active             boolean NOT NULL DEFAULT true,            -- retire, never delete
  needs_review       boolean NOT NULL DEFAULT false,
  review_reason      text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  created_by         text NOT NULL DEFAULT 'claude' CHECK (created_by IN ('claude','user','import')),
  CONSTRAINT foods_satfat_le_fat    CHECK (sat_fat IS NULL OR sat_fat <= fat + 0.05),
  CONSTRAINT foods_sugar_le_carbs   CHECK (sugar_total IS NULL OR sugar_total <= carbs + 0.05),
  CONSTRAINT foods_added_le_total   CHECK (sugar_added IS NULL OR sugar_total IS NULL OR sugar_added <= sugar_total + 0.05)
);
CREATE INDEX foods_aliases_gin ON foods USING gin (aliases);

CREATE TABLE food_defaults (
  term     text PRIMARY KEY CHECK (term = lower(term)),
  food_id  integer NOT NULL REFERENCES foods(id)
);

CREATE TABLE food_units (
  id       serial PRIMARY KEY,
  food_id  integer NOT NULL REFERENCES foods(id),
  unit     text NOT NULL,                                      -- tsp, tbsp, cup, piece, slice, medium, scoop ...
  grams    numeric NOT NULL CHECK (grams > 0),                 -- g (or ml for basis ml) per unit
  source   text NOT NULL DEFAULT 'estimate' CHECK (source IN ('label','estimate')),
  UNIQUE (food_id, unit)
);

CREATE TABLE food_changes (
  id              bigserial PRIMARY KEY,
  changed_at      timestamptz NOT NULL DEFAULT now(),
  op              text NOT NULL CHECK (op IN ('update','delete','past')),
  food_id         integer NOT NULL,                            -- no FK on purpose (survives a delete)
  food_name       text,
  changed_by      text NOT NULL,
  old_values      jsonb NOT NULL DEFAULT '{}',
  new_values      jsonb NOT NULL DEFAULT '{}',
  raw_id          bigint REFERENCES raw_inbox(id),
  applied_to_past boolean NOT NULL DEFAULT false,
  note            text
);
CREATE INDEX food_changes_food ON food_changes (food_id, changed_at);

-- ---------------------------------------------------------------- the log
CREATE TABLE food_entries (
  id                bigserial PRIMARY KEY,                     -- display id f<id>
  raw_id            bigint NOT NULL REFERENCES raw_inbox(id),  -- the chat message = the batch
  food_id           integer NOT NULL REFERENCES foods(id),
  amount            numeric NOT NULL CHECK (amount > 0 AND amount <= 5000),  -- g or ml (foods.basis)
  portion_text      text,                                      -- as said: "2 eggs", "1 tbsp"
  estimated         boolean NOT NULL DEFAULT false,
  note              text,
  eaten_at          timestamptz NOT NULL,
  time_known        boolean NOT NULL DEFAULT false,            -- false = no time given (stored at 12:00)
  day               date NOT NULL,                             -- logging day (stored)
  day_rule          text NOT NULL CHECK (day_rule IN ('wake','fallback_05','asked','import')),
  meal              smallint NOT NULL DEFAULT 1 CHECK (meal BETWEEN 1 AND 20),
  -- nutrient snapshot of this entry (filled by trigger from foods when kcal is NULL on insert)
  kcal              numeric NOT NULL,
  protein           numeric NOT NULL,
  fat               numeric NOT NULL,
  sat_fat           numeric,
  carbs             numeric NOT NULL,
  fibre             numeric,
  sugar_total       numeric,
  sugar_added       numeric,
  sodium_mg         numeric,
  caffeine_mg       numeric,
  source            text NOT NULL CHECK (source IN ('label','database','estimate','calculated')),
  usual_meal_id     integer,                                   -- FK added below
  recipe_id         integer,                                   -- FK added below
  needs_review      boolean NOT NULL DEFAULT false,
  review_reason     text,
  logged_at         timestamptz NOT NULL DEFAULT now(),
  extractor_version text NOT NULL,
  import_key        text UNIQUE
);
CREATE INDEX food_entries_day  ON food_entries (day, meal);
CREATE INDEX food_entries_food ON food_entries (food_id);
CREATE INDEX food_entries_raw  ON food_entries (raw_id);

-- ---------------------------------------------------------------- recipes and usual meals
CREATE TABLE recipes (
  id                serial PRIMARY KEY,                        -- display id C<id>
  name              text NOT NULL UNIQUE,
  food_id           integer NOT NULL UNIQUE REFERENCES foods(id),   -- its row in foods (source calculated)
  finished_weight_g numeric NOT NULL CHECK (finished_weight_g > 0),
  weight_measured   boolean NOT NULL DEFAULT true,             -- false = estimated (Quinoa salad)
  note              text
);
CREATE TABLE recipe_items (
  id         serial PRIMARY KEY,
  recipe_id  integer NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
  food_id    integer NOT NULL REFERENCES foods(id),
  grams      numeric NOT NULL CHECK (grams > 0),
  sort_order smallint NOT NULL DEFAULT 100
);
CREATE INDEX recipe_items_recipe ON recipe_items (recipe_id);

CREATE TABLE usual_meals (
  id       serial PRIMARY KEY,                                 -- display id U<id>
  name     text NOT NULL UNIQUE,
  aliases  text[] NOT NULL DEFAULT '{}',
  note     text
);
CREATE TABLE usual_meal_items (
  id         serial PRIMARY KEY,
  meal_id    integer NOT NULL REFERENCES usual_meals(id) ON DELETE CASCADE,
  food_id    integer NOT NULL REFERENCES foods(id),
  grams      numeric NOT NULL CHECK (grams > 0),
  sort_order smallint NOT NULL DEFAULT 100
);
CREATE INDEX usual_meal_items_meal ON usual_meal_items (meal_id);

ALTER TABLE food_entries ADD FOREIGN KEY (usual_meal_id) REFERENCES usual_meals(id);
ALTER TABLE food_entries ADD FOREIGN KEY (recipe_id)     REFERENCES recipes(id);

CREATE TABLE food_targets (
  id          serial PRIMARY KEY,                              -- display id T<id>
  valid_from  date NOT NULL UNIQUE,
  kcal        numeric, protein numeric, fat numeric, sat_fat numeric, carbs numeric,
  fibre       numeric, sugar_added numeric, sodium_mg numeric,
  note        text,
  raw_id      bigint REFERENCES raw_inbox(id)
);

-- ---------------------------------------------------------------- functions
-- Logging day by the wake-up rule: the day of the last wake-up within p_max_hours before ts.
-- NULL = unclear (no wake-up logged, or the last one is too old): the caller asks the user.
CREATE FUNCTION food_day(p_ts timestamptz, p_max_hours integer DEFAULT 18) RETURNS date
LANGUAGE sql STABLE AS
$$
  SELECT w.day
  FROM events w JOIN log_types t ON t.id = w.type_id AND t.name = 'wake'
  WHERE w.event_at <= p_ts AND w.event_at > p_ts - make_interval(hours => p_max_hours)
  ORDER BY w.event_at DESC LIMIT 1
$$;

-- Meal numbers for one day: by time, an entry within 2 hours of the previous one joins its meal.
-- Only live entries with a known time are touched; imported days and untimed entries keep their numbers.
CREATE FUNCTION assign_meals(p_day date) RETURNS integer
LANGUAGE plpgsql AS
$$
DECLARE n integer;
BEGIN
  WITH o AS (
    SELECT id, eaten_at, lag(eaten_at) OVER (ORDER BY eaten_at, id) AS prev
    FROM food_entries
    WHERE day = p_day AND time_known AND day_rule <> 'import'
  ), b AS (
    SELECT id, sum(CASE WHEN prev IS NULL OR eaten_at - prev > interval '2 hours' THEN 1 ELSE 0 END)
                 OVER (ORDER BY eaten_at, id)::smallint AS m
    FROM o
  )
  UPDATE food_entries e SET meal = b.m FROM b WHERE e.id = b.id AND e.meal IS DISTINCT FROM b.m;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;

-- Snapshot trigger: insert without kcal -> take everything from foods; update of amount -> rescale;
-- update of food_id -> take from the new food.
CREATE FUNCTION food_entries_snapshot() RETURNS trigger
LANGUAGE plpgsql AS
$$
DECLARE f foods%ROWTYPE; k numeric;
BEGIN
  IF TG_OP = 'INSERT' AND NEW.kcal IS NULL
     OR TG_OP = 'UPDATE' AND NEW.food_id <> OLD.food_id AND NEW.kcal IS NOT DISTINCT FROM OLD.kcal THEN
    SELECT * INTO f FROM foods WHERE id = NEW.food_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'food % does not exist', NEW.food_id; END IF;
    k := NEW.amount / 100.0;
    NEW.kcal := round(k * f.kcal, 2);        NEW.protein := round(k * f.protein, 2);
    NEW.fat := round(k * f.fat, 2);          NEW.sat_fat := round(k * f.sat_fat, 2);
    NEW.carbs := round(k * f.carbs, 2);      NEW.fibre := round(k * f.fibre, 2);
    NEW.sugar_total := round(k * f.sugar_total, 2);  NEW.sugar_added := round(k * f.sugar_added, 2);
    NEW.sodium_mg := round(k * f.sodium_mg, 2);      NEW.caffeine_mg := round(k * f.caffeine_mg, 2);
    NEW.source := f.source;
  ELSIF TG_OP = 'UPDATE' AND NEW.amount <> OLD.amount AND NEW.kcal IS NOT DISTINCT FROM OLD.kcal THEN
    k := NEW.amount / OLD.amount;
    NEW.kcal := round(OLD.kcal * k, 2);      NEW.protein := round(OLD.protein * k, 2);
    NEW.fat := round(OLD.fat * k, 2);        NEW.sat_fat := round(OLD.sat_fat * k, 2);
    NEW.carbs := round(OLD.carbs * k, 2);    NEW.fibre := round(OLD.fibre * k, 2);
    NEW.sugar_total := round(OLD.sugar_total * k, 2); NEW.sugar_added := round(OLD.sugar_added * k, 2);
    NEW.sodium_mg := round(OLD.sodium_mg * k, 2);     NEW.caffeine_mg := round(OLD.caffeine_mg * k, 2);
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER food_entries_snapshot_trg BEFORE INSERT OR UPDATE OF amount, food_id ON food_entries
  FOR EACH ROW EXECUTE FUNCTION food_entries_snapshot();

-- History of the catalogue: every change by anyone (Claude, NocoDB, import) is recorded.
CREATE FUNCTION foods_touch() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;
CREATE TRIGGER foods_touch_trg BEFORE UPDATE ON foods FOR EACH ROW EXECUTE FUNCTION foods_touch();

CREATE FUNCTION foods_history() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE o jsonb; n jsonb; ov jsonb; nv jsonb;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    o := to_jsonb(OLD) - 'updated_at';  n := to_jsonb(NEW) - 'updated_at';
    SELECT coalesce(jsonb_object_agg(a.key, a.value), '{}'), coalesce(jsonb_object_agg(b.key, b.value), '{}')
      INTO ov, nv
      FROM jsonb_each(o) a JOIN jsonb_each(n) b USING (key)
      WHERE a.value IS DISTINCT FROM b.value;
    IF ov = '{}'::jsonb THEN RETURN NEW; END IF;
    INSERT INTO food_changes (op, food_id, food_name, changed_by, old_values, new_values, raw_id)
    VALUES ('update', OLD.id, OLD.name, session_user, ov, nv,
            nullif(current_setting('app.raw_id', true), '')::bigint);
    RETURN NEW;
  ELSE
    INSERT INTO food_changes (op, food_id, food_name, changed_by, old_values, raw_id)
    VALUES ('delete', OLD.id, OLD.name, session_user, to_jsonb(OLD),
            nullif(current_setting('app.raw_id', true), '')::bigint);
    RETURN OLD;
  END IF;
END
$$;
CREATE TRIGGER foods_history_trg AFTER UPDATE OR DELETE ON foods
  FOR EACH ROW EXECUTE FUNCTION foods_history();

-- History rule: rewrite snapshots of past entries from the food's current values.
--   p_only_unknown = true : fill only values that are still unknown (a value that was unknown, not different)
--   p_only_unknown = false: rewrite all nutrient snapshots (a correction applied to the past)
-- Returns the number of entries changed; one 'past' row goes to food_changes.
CREATE FUNCTION apply_food_to_past(p_food integer, p_only_unknown boolean DEFAULT false, p_note text DEFAULT NULL)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE f foods%ROWTYPE; n integer;
BEGIN
  SELECT * INTO f FROM foods WHERE id = p_food;
  IF NOT FOUND THEN RAISE EXCEPTION 'food % does not exist', p_food; END IF;
  IF p_only_unknown THEN
    UPDATE food_entries e SET
      sat_fat     = coalesce(e.sat_fat,     round(e.amount / 100.0 * f.sat_fat, 2)),
      fibre       = coalesce(e.fibre,       round(e.amount / 100.0 * f.fibre, 2)),
      sugar_total = coalesce(e.sugar_total, round(e.amount / 100.0 * f.sugar_total, 2)),
      sugar_added = coalesce(e.sugar_added, round(e.amount / 100.0 * f.sugar_added, 2)),
      sodium_mg   = coalesce(e.sodium_mg,   round(e.amount / 100.0 * f.sodium_mg, 2)),
      caffeine_mg = coalesce(e.caffeine_mg, round(e.amount / 100.0 * f.caffeine_mg, 2))
    WHERE e.food_id = p_food
      AND (e.sat_fat IS NULL OR e.fibre IS NULL OR e.sugar_total IS NULL
           OR e.sugar_added IS NULL OR e.sodium_mg IS NULL OR e.caffeine_mg IS NULL);
  ELSE
    UPDATE food_entries e SET
      kcal = round(e.amount / 100.0 * f.kcal, 2),       protein = round(e.amount / 100.0 * f.protein, 2),
      fat = round(e.amount / 100.0 * f.fat, 2),         sat_fat = round(e.amount / 100.0 * f.sat_fat, 2),
      carbs = round(e.amount / 100.0 * f.carbs, 2),     fibre = round(e.amount / 100.0 * f.fibre, 2),
      sugar_total = round(e.amount / 100.0 * f.sugar_total, 2),
      sugar_added = round(e.amount / 100.0 * f.sugar_added, 2),
      sodium_mg = round(e.amount / 100.0 * f.sodium_mg, 2),
      caffeine_mg = round(e.amount / 100.0 * f.caffeine_mg, 2),
      source = f.source
    WHERE e.food_id = p_food;
  END IF;
  GET DIAGNOSTICS n = ROW_COUNT;
  INSERT INTO food_changes (op, food_id, food_name, changed_by, raw_id, applied_to_past, note)
  VALUES ('past', f.id, f.name, session_user, nullif(current_setting('app.raw_id', true), '')::bigint, true,
          coalesce(p_note, format('applied to %s past entries (%s)', n,
                   CASE WHEN p_only_unknown THEN 'unknown values only' ELSE 'all nutrients' END)));
  RETURN n;
END
$$;

-- Recipes: per-100 values of the recipe's foods row from its items and the finished weight.
-- A nutrient is NULL (unknown) as soon as one ingredient lacks it.
CREATE FUNCTION recalc_recipe(p_recipe integer) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS
$$
DECLARE r recipes%ROWTYPE; cnt integer;
BEGIN
  SELECT * INTO r FROM recipes WHERE id = p_recipe;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT count(*) INTO cnt FROM recipe_items WHERE recipe_id = p_recipe;
  IF cnt = 0 THEN RETURN; END IF;
  UPDATE foods f SET
    basis = 'g', source = 'calculated',
    kcal    = x.kcal,    protein = x.protein, fat = x.fat, carbs = x.carbs,
    sat_fat = x.sat_fat, fibre = x.fibre, sugar_total = x.sugar_total, sugar_added = x.sugar_added,
    sodium_mg = x.sodium_mg, caffeine_mg = x.caffeine_mg
  FROM (
    SELECT
      round(sum(i.grams / 100.0 * g.kcal)    / r.finished_weight_g * 100, 2) AS kcal,
      round(sum(i.grams / 100.0 * g.protein) / r.finished_weight_g * 100, 2) AS protein,
      round(sum(i.grams / 100.0 * g.fat)     / r.finished_weight_g * 100, 2) AS fat,
      round(sum(i.grams / 100.0 * g.carbs)   / r.finished_weight_g * 100, 2) AS carbs,
      CASE WHEN bool_or(g.sat_fat     IS NULL) THEN NULL ELSE round(sum(i.grams / 100.0 * g.sat_fat)     / r.finished_weight_g * 100, 2) END AS sat_fat,
      CASE WHEN bool_or(g.fibre       IS NULL) THEN NULL ELSE round(sum(i.grams / 100.0 * g.fibre)       / r.finished_weight_g * 100, 2) END AS fibre,
      CASE WHEN bool_or(g.sugar_total IS NULL) THEN NULL ELSE round(sum(i.grams / 100.0 * g.sugar_total) / r.finished_weight_g * 100, 2) END AS sugar_total,
      CASE WHEN bool_or(g.sugar_added IS NULL) THEN NULL ELSE round(sum(i.grams / 100.0 * g.sugar_added) / r.finished_weight_g * 100, 2) END AS sugar_added,
      CASE WHEN bool_or(g.sodium_mg   IS NULL) THEN NULL ELSE round(sum(i.grams / 100.0 * g.sodium_mg)   / r.finished_weight_g * 100, 2) END AS sodium_mg,
      CASE WHEN bool_or(g.caffeine_mg IS NULL) THEN NULL ELSE round(sum(i.grams / 100.0 * g.caffeine_mg) / r.finished_weight_g * 100, 2) END AS caffeine_mg
    FROM recipe_items i JOIN foods g ON g.id = i.food_id
    WHERE i.recipe_id = p_recipe
  ) x
  WHERE f.id = r.food_id;
END
$$;

CREATE FUNCTION recipe_items_guard() RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
  IF EXISTS (SELECT 1 FROM recipes WHERE food_id = NEW.food_id) THEN
    RAISE EXCEPTION 'a recipe cannot contain another recipe (food %)', NEW.food_id;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER recipe_items_guard_trg BEFORE INSERT OR UPDATE OF food_id ON recipe_items
  FOR EACH ROW EXECUTE FUNCTION recipe_items_guard();

CREATE FUNCTION recipe_recalc_trg() RETURNS trigger LANGUAGE plpgsql AS
$$
BEGIN
  IF TG_TABLE_NAME = 'recipes' THEN
    PERFORM recalc_recipe(NEW.id);
  ELSE
    PERFORM recalc_recipe(CASE WHEN TG_OP = 'DELETE' THEN OLD.recipe_id ELSE NEW.recipe_id END);
  END IF;
  RETURN NULL;
END
$$;
CREATE TRIGGER recipe_items_recalc AFTER INSERT OR UPDATE OR DELETE ON recipe_items
  FOR EACH ROW EXECUTE FUNCTION recipe_recalc_trg();
CREATE TRIGGER recipes_recalc AFTER UPDATE OF finished_weight_g ON recipes
  FOR EACH ROW EXECUTE FUNCTION recipe_recalc_trg();

-- ---------------------------------------------------------------- views
CREATE VIEW v_food_log AS
SELECT e.id, e.day, CASE WHEN e.time_known THEN (e.eaten_at AT TIME ZONE 'Europe/Berlin')::time(0) END AS time,
       e.meal, f.name AS food, e.amount, f.basis AS unit, e.portion_text,
       e.kcal, e.protein, e.fat, e.sat_fat, e.carbs, e.fibre, e.sugar_total, e.sugar_added,
       e.sodium_mg, e.caffeine_mg, e.source, e.estimated, e.note, e.needs_review, e.review_reason,
       e.raw_id, e.logged_at
FROM food_entries e JOIN foods f ON f.id = e.food_id;

-- One row per day and meal, plus one total row per day (meal NULL) with the targets in force that day
-- and how many entries have an unknown value (so totals can say "3 foods unknown").
CREATE VIEW v_food_day AS
SELECT g.day, g.meal, (g.meal IS NULL) AS is_total, g.items,
       g.kcal, g.protein, g.fat, g.sat_fat, g.carbs, g.fibre, g.sugar_total, g.sugar_added, g.sodium_mg, g.caffeine_mg,
       g.sat_fat_unknown, g.sugar_total_unknown, g.sugar_added_unknown, g.caffeine_unknown,
       CASE WHEN g.meal IS NULL THEN t.kcal END        AS target_kcal,
       CASE WHEN g.meal IS NULL THEN t.protein END     AS target_protein,
       CASE WHEN g.meal IS NULL THEN t.fat END         AS target_fat,
       CASE WHEN g.meal IS NULL THEN t.sat_fat END     AS target_sat_fat,
       CASE WHEN g.meal IS NULL THEN t.carbs END       AS target_carbs,
       CASE WHEN g.meal IS NULL THEN t.fibre END       AS target_fibre,
       CASE WHEN g.meal IS NULL THEN t.sugar_added END AS target_sugar_added,
       CASE WHEN g.meal IS NULL THEN t.sodium_mg END   AS target_sodium_mg
FROM (
  SELECT day, meal, count(*)::integer AS items,
         round(sum(kcal), 2) AS kcal, round(sum(protein), 2) AS protein, round(sum(fat), 2) AS fat,
         round(sum(sat_fat), 2) AS sat_fat, round(sum(carbs), 2) AS carbs, round(sum(fibre), 2) AS fibre,
         round(sum(sugar_total), 2) AS sugar_total, round(sum(sugar_added), 2) AS sugar_added,
         round(sum(sodium_mg), 2) AS sodium_mg, round(sum(caffeine_mg), 2) AS caffeine_mg,
         (count(*) FILTER (WHERE sat_fat IS NULL))::integer     AS sat_fat_unknown,
         (count(*) FILTER (WHERE sugar_total IS NULL))::integer AS sugar_total_unknown,
         (count(*) FILTER (WHERE sugar_added IS NULL))::integer AS sugar_added_unknown,
         (count(*) FILTER (WHERE caffeine_mg IS NULL))::integer AS caffeine_unknown
  FROM food_entries
  GROUP BY GROUPING SETS ((day, meal), (day))
) g
LEFT JOIN LATERAL (SELECT * FROM food_targets ft WHERE ft.valid_from <= g.day
                   ORDER BY ft.valid_from DESC LIMIT 1) t ON true;

-- ---------------------------------------------------------------- rights (per table on purpose)
GRANT SELECT, INSERT, UPDATE, DELETE ON food_entries, food_defaults, food_units, recipes, recipe_items,
      usual_meals, usual_meal_items, food_targets TO core_rw;
GRANT SELECT, INSERT, UPDATE ON foods TO core_rw;            -- retire, never delete
-- (grant for nocodb_ui removed; NocoDB comes back after cutover)     -- FK keeps used foods; deletes are logged
GRANT SELECT ON food_changes TO core_rw;          -- written only by the trigger
GRANT SELECT ON v_food_log, v_food_day TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE foods_id_seq, food_units_id_seq, food_entries_id_seq, recipes_id_seq,
      recipe_items_id_seq, usual_meals_id_seq, usual_meal_items_id_seq, food_targets_id_seq
      TO core_rw;
GRANT EXECUTE ON FUNCTION food_day(timestamptz, integer), assign_meals(date),
      apply_food_to_past(integer, boolean, text), recalc_recipe(integer) TO core_rw;

COMMIT;
