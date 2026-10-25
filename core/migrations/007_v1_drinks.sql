-- Copied from logger-app sql/schema-v6-drinks.sql (v1 schema v6 drinks). Only change: role mcp_writer -> core_rw, NocoDB and sheets_reader grants removed.
-- schema-v6-drinks.sql : drinks are their own category (decided 15.10.2026)
-- Additive on schema v5 (food). Run as app_owner in ONE transaction:
--   docker compose exec -T db psql -U pgadmin -d appdata -v ON_ERROR_STOP=1 -c "SET ROLE app_owner" -f - < sql/schema-v6-drinks.sql
-- Rules:
--   * foods.is_drink       : the default for new entries of that food (tea, coffee, cola, juice ...).
--   * usual_meals.is_drink : all entries of that usual meal are drinks (the usual hot chocolate).
--   * food_entries.is_drink: the entry itself. On insert, NULL = take it from the food. Wins over the food (milk drunk = drink).
--   * A drink has meal 0, never opens a meal and never bridges two meals; its kcal and nutrients count in the day totals.
--   * Imported history keeps its meal numbers (all is_drink false).
BEGIN;

ALTER TABLE foods        ADD COLUMN is_drink boolean NOT NULL DEFAULT false;
ALTER TABLE usual_meals  ADD COLUMN is_drink boolean NOT NULL DEFAULT false;
ALTER TABLE food_entries ADD COLUMN is_drink boolean NOT NULL DEFAULT false;
ALTER TABLE food_entries ALTER COLUMN is_drink DROP DEFAULT;      -- inserts without a value are filled by the trigger below

ALTER TABLE food_entries DROP CONSTRAINT food_entries_meal_check;
ALTER TABLE food_entries ADD CONSTRAINT food_entries_meal_check CHECK (meal BETWEEN 0 AND 20);
ALTER TABLE food_entries ADD CONSTRAINT food_entries_drink_meal CHECK ((meal = 0) = is_drink);

-- Keeps is_drink and meal consistent: drink -> meal 0; not a drink and meal 0 -> meal 1 (assign_meals numbers it afterwards).
CREATE FUNCTION food_entries_drink() RETURNS trigger
LANGUAGE plpgsql AS
$$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.is_drink IS NULL THEN
      SELECT is_drink INTO NEW.is_drink FROM foods WHERE id = NEW.food_id;
    END IF;
  ELSIF NEW.food_id <> OLD.food_id AND NEW.is_drink IS NOT DISTINCT FROM OLD.is_drink THEN
    SELECT is_drink INTO NEW.is_drink FROM foods WHERE id = NEW.food_id;     -- another food: take its default
  END IF;
  IF NEW.is_drink IS NULL THEN NEW.is_drink := false; END IF;
  IF NEW.is_drink THEN
    NEW.meal := 0;
  ELSIF NEW.meal = 0 THEN
    NEW.meal := 1;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER food_entries_drink_trg BEFORE INSERT OR UPDATE OF food_id, is_drink, meal ON food_entries
  FOR EACH ROW EXECUTE FUNCTION food_entries_drink();

-- Meal numbers for one day: by time, an entry within 2 hours of the previous one joins its meal.
-- Drinks are left out (they keep meal 0). Only live entries with a known time are touched;
-- imported days and untimed entries keep their numbers.
CREATE OR REPLACE FUNCTION assign_meals(p_day date) RETURNS integer
LANGUAGE plpgsql AS
$$
DECLARE n integer;
BEGIN
  WITH o AS (
    SELECT id, eaten_at, lag(eaten_at) OVER (ORDER BY eaten_at, id) AS prev
    FROM food_entries
    WHERE day = p_day AND time_known AND day_rule <> 'import' AND NOT is_drink
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

-- Views: columns are only appended (CREATE OR REPLACE keeps the rights).
CREATE OR REPLACE VIEW v_food_log AS
SELECT e.id, e.day, CASE WHEN e.time_known THEN (e.eaten_at AT TIME ZONE 'Europe/Berlin')::time(0) END AS time,
       e.meal, f.name AS food, e.amount, f.basis AS unit, e.portion_text,
       e.kcal, e.protein, e.fat, e.sat_fat, e.carbs, e.fibre, e.sugar_total, e.sugar_added,
       e.sodium_mg, e.caffeine_mg, e.source, e.estimated, e.note, e.needs_review, e.review_reason,
       e.raw_id, e.logged_at, e.is_drink
FROM food_entries e JOIN foods f ON f.id = e.food_id;

-- Meal 0 = the drinks of a day (its own row). The day total row includes drinks and says how many kcal came from them.
CREATE OR REPLACE VIEW v_food_day AS
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
       CASE WHEN g.meal IS NULL THEN t.sodium_mg END   AS target_sodium_mg,
       CASE WHEN g.meal IS NULL THEN 'Day total' WHEN g.meal = 0 THEN 'Drinks' ELSE 'Meal ' || g.meal END AS meal_label,
       CASE WHEN g.meal IS NULL THEN coalesce(g.drink_kcal, 0) END AS drink_kcal
FROM (
  SELECT day, meal, count(*)::integer AS items,
         round(sum(kcal), 2) AS kcal, round(sum(protein), 2) AS protein, round(sum(fat), 2) AS fat,
         round(sum(sat_fat), 2) AS sat_fat, round(sum(carbs), 2) AS carbs, round(sum(fibre), 2) AS fibre,
         round(sum(sugar_total), 2) AS sugar_total, round(sum(sugar_added), 2) AS sugar_added,
         round(sum(sodium_mg), 2) AS sodium_mg, round(sum(caffeine_mg), 2) AS caffeine_mg,
         (count(*) FILTER (WHERE sat_fat IS NULL))::integer     AS sat_fat_unknown,
         (count(*) FILTER (WHERE sugar_total IS NULL))::integer AS sugar_total_unknown,
         (count(*) FILTER (WHERE sugar_added IS NULL))::integer AS sugar_added_unknown,
         (count(*) FILTER (WHERE caffeine_mg IS NULL))::integer AS caffeine_unknown,
         round(sum(kcal) FILTER (WHERE is_drink), 2) AS drink_kcal
  FROM food_entries
  GROUP BY GROUPING SETS ((day, meal), (day))
) g
LEFT JOIN LATERAL (SELECT * FROM food_targets ft WHERE ft.valid_from <= g.day
                   ORDER BY ft.valid_from DESC LIMIT 1) t ON true;

COMMIT;
