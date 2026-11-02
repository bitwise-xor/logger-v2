-- The Gym section (02.11.2026; plan in the doc "Logger v2: status and web app plan", section "Gym page plan"): the owner's
-- exercises (three kinds), workouts (one per logging day, started by the first tick), one row per exercise and set group
-- ("3 x 8 at 60 kg"; a differing set is a second row), and the split days with their exercises (the Plan tab, built next).
-- A tick also writes a raw_inbox row ("gym: ...") so it shows on Recent, and ticks the habit "gym" once a day.
CREATE TABLE exercises (
  id         bigserial PRIMARY KEY,
  name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  words      text[] NOT NULL DEFAULT '{}',  -- other names for it (for typed gym lines, later)
  kind       text NOT NULL CHECK (kind IN ('weights', 'bodyweight', 'cardio')),
  active     boolean NOT NULL DEFAULT true,
  sort_order integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX exercises_name ON exercises (lower(name));

CREATE TABLE workout_days (  -- the split: Day 1 Push, Day 2 Pull, ...
  id         bigserial PRIMARY KEY,
  name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  sort_order integer NOT NULL DEFAULT 0,
  active     boolean NOT NULL DEFAULT true
);
CREATE TABLE workout_day_items (
  id          bigserial PRIMARY KEY,
  day_id      bigint NOT NULL REFERENCES workout_days ON DELETE CASCADE,
  exercise_id bigint NOT NULL REFERENCES exercises,
  sort_order  integer NOT NULL DEFAULT 0,
  sets        integer CHECK (sets BETWEEN 1 AND 50),
  reps        integer CHECK (reps BETWEEN 1 AND 500)
);

CREATE TABLE workouts (
  id           bigserial PRIMARY KEY,
  raw_id       bigint REFERENCES raw_inbox (id),
  day          date NOT NULL,
  started_at   timestamptz NOT NULL,
  ended_at     timestamptz,
  split_day_id bigint REFERENCES workout_days ON DELETE SET NULL,
  note         text,
  logged_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workouts_day ON workouts (day);

CREATE TABLE workout_sets (
  id          bigserial PRIMARY KEY,
  workout_id  bigint NOT NULL REFERENCES workouts ON DELETE CASCADE,
  exercise_id bigint NOT NULL REFERENCES exercises,
  raw_id      bigint REFERENCES raw_inbox (id),
  sets        integer CHECK (sets BETWEEN 1 AND 50),
  reps        integer CHECK (reps BETWEEN 1 AND 500),
  kg          numeric(6,2) CHECK (kg >= 0 AND kg <= 1000),
  minutes     numeric(6,1) CHECK (minutes > 0 AND minutes <= 600),
  km          numeric(6,2) CHECK (km >= 0 AND km <= 200),
  note        text,
  sort_order  integer NOT NULL DEFAULT 0,
  logged_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workout_sets_workout ON workout_sets (workout_id);
CREATE INDEX workout_sets_exercise ON workout_sets (exercise_id, logged_at);

GRANT SELECT, INSERT, UPDATE, DELETE ON exercises, workout_days, workout_day_items, workouts, workout_sets TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE exercises_id_seq, workout_days_id_seq, workout_day_items_id_seq, workouts_id_seq, workout_sets_id_seq TO core_rw;
