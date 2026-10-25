-- Data changes applied once, right after v1's data is restored into v2 (and to the test copy, so tests match).
-- Decided by the owner on 25.10.2026. Run as app_owner in one transaction:
--   psql -v ON_ERROR_STOP=1 -d appdata -f tools/import/v1_fixes.sql
BEGIN;

-- Words that mean one of the owner's products. These lived in v1's chat rules or are new choices.
INSERT INTO food_defaults (term, food_id) VALUES
  ('tea', 230), ('potato chips', 235), ('kettle chips', 235), ('kettle potato chips', 235),
  ('waffle', 216), ('skyr', 232), ('seed mix', 234), ('omega mix', 234), ('cashew peanut mix', 249)
ON CONFLICT (term) DO UPDATE SET food_id = EXCLUDED.food_id;

-- Whey eaten in a bowl is food, not a drink (v1 saved it as a drink).
UPDATE foods SET is_drink = false WHERE name = 'Protein powder, whey (Aldi Sports Whey Deluxe)';

-- The usual meal U7 "breakfast" from the rolled-back v1 trial is not carried over.
DELETE FROM usual_meal_items WHERE meal_id = (SELECT id FROM usual_meals WHERE id = 7 AND name = 'breakfast');
DELETE FROM usual_meals WHERE id = 7 AND name = 'breakfast';

COMMIT;
