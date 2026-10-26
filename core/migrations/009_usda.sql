-- v2: a local copy of USDA FoodData Central SR Legacy (2018, public domain) for looking up common foods by name
-- (decided 26.10.2026: local copy, no API key). app/usda.py fills it from core/data at startup when it is empty.
-- food_units may now say a weight came from a database (USDA portion weights).
BEGIN;

CREATE TABLE usda_foods (
  fdc_id       integer PRIMARY KEY,
  description  text NOT NULL,
  category     text,
  kcal         numeric NOT NULL,
  protein      numeric NOT NULL,
  fat          numeric NOT NULL,
  sat_fat      numeric,
  carbs        numeric NOT NULL,          -- by difference, fibre included (US convention)
  fibre        numeric,
  sugar_total  numeric,
  sodium_mg    numeric,
  caffeine_mg  numeric
);
CREATE INDEX usda_foods_fts_idx ON usda_foods USING gin (to_tsvector('english', description));

CREATE TABLE usda_portions (
  fdc_id  integer NOT NULL REFERENCES usda_foods(fdc_id),
  amount  numeric NOT NULL,
  unit    text NOT NULL,                  -- USDA's words: "cup", "medium (2-3/4\" dia)", "tbsp", "slice" ...
  grams   numeric NOT NULL
);
CREATE INDEX usda_portions_fdc_idx ON usda_portions (fdc_id);

ALTER TABLE food_units DROP CONSTRAINT food_units_source_check;
ALTER TABLE food_units ADD CONSTRAINT food_units_source_check CHECK (source IN ('label','estimate','database'));

GRANT SELECT ON usda_foods, usda_portions TO core_rw;

COMMIT;