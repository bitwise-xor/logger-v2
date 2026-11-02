-- Watch data (02.11.2026, Gym step d; plan and the owner's answers in the doc "Logger v2: status and web app plan", section
-- "Step (d), Activity"): Apple Health readings sent by the iPhone app Health Auto Export to POST /watch/health (watch.py),
-- about every 30 minutes. Each run sends yesterday and today again, so a reading is kept once per (metric, time, source) and
-- overwritten, and a workout once per Apple's id. Shown on the Gym section's Activity tab; not in the Google Sheet.
CREATE TABLE watch_metrics (
  id          bigserial PRIMARY KEY,
  metric      text NOT NULL,            -- step_count, active_energy, apple_exercise_time, ..., sleep_analysis, heart_rate
  at          timestamptz NOT NULL,     -- the reading's time: the start of its hour or day when summarised
  value       numeric,                  -- in the unit below (energy in kcal, distance in km); sleep in hours; heart rate the average
  unit        text NOT NULL DEFAULT '',
  source      text NOT NULL DEFAULT '',
  extra       jsonb NOT NULL DEFAULT '{}',  -- sleep: the stages in hours and the times; a sleep segment: its stage and end; heart rate: min, avg, max
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (metric, at, source)
);
CREATE INDEX watch_metrics_at ON watch_metrics (at);

CREATE TABLE watch_workouts (
  id          bigserial PRIMARY KEY,
  apple_id    text NOT NULL UNIQUE,
  name        text NOT NULL,
  start_at    timestamptz NOT NULL,
  end_at      timestamptz NOT NULL,
  kcal        numeric,
  km          numeric,
  hr_avg      numeric,
  hr_max      numeric,
  source      text NOT NULL DEFAULT '',
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX watch_workouts_start ON watch_workouts (start_at);

GRANT SELECT, INSERT, UPDATE, DELETE ON watch_metrics, watch_workouts TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE watch_metrics_id_seq, watch_workouts_id_seq TO core_rw;
