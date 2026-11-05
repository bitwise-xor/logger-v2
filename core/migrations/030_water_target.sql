-- Water as its own daily target (05.11.2026, IMPROVEMENTS item 40): ml a day, dated with the other food targets (food_targets).
-- What counts (the owner's rule A): every entry logged as a drink (meal 0), by its amount; a drink weighed in g counts 1 g = 1 ml.
-- Food never counts. Today shows the bar only while a water target is set.
ALTER TABLE food_targets ADD COLUMN water_ml numeric CHECK (water_ml >= 0);
