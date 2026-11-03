-- The Home page (03.11.2026; spec docs/specs/home-page-spec.md on the branch docs/home-page-spec, plan and the owner's answers in the doc
-- "Logger v2: status and web app plan", tab Current): household chores with an interval and a "last done", kept apart from the habits.
-- A zone is a place (Bathroom, Kitchen, ...). A chore belongs to one zone, or to the whole flat (zone_id NULL: Dust, Vacuum, Mop), and then
-- every active zone is tracked on its own: a done row always names its zone. zone_days overrides the interval for single zones
-- ({"<zone id>": 7}: Mop is weekly in the kitchen and the bathroom). Rows are retired (active = false), never deleted, so the history keeps
-- its names. The data is not visible to the Google Sheet's reader (the owner's choice, like the Gym).
CREATE TABLE home_zones (
  id         bigserial PRIMARY KEY,
  name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  short      text NOT NULL CHECK (length(short) BETWEEN 1 AND 12),  -- the tile's label ("Bath")
  spots      text[] NOT NULL DEFAULT '{}',                           -- the places in it: the steps of a whole-flat chore here
  words      text[] NOT NULL DEFAULT '{}',                           -- other names for it, for typed lines ("bedroom")
  sort_order integer NOT NULL DEFAULT 0,
  active     boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX home_zones_name ON home_zones (lower(name));

CREATE TABLE home_chores (
  id            bigserial PRIMARY KEY,
  name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
  zone_id       bigint REFERENCES home_zones,                        -- NULL = the whole flat, zone by zone
  interval_days integer NOT NULL CHECK (interval_days BETWEEN 1 AND 730),
  zone_days     jsonb NOT NULL DEFAULT '{}',
  minutes       integer NOT NULL DEFAULT 15 CHECK (minutes BETWEEN 1 AND 240),
  steps         text[] NOT NULL DEFAULT '{}',
  howto         text CHECK (length(howto) <= 300),
  words         text[] NOT NULL DEFAULT '{}',
  sort_order    integer NOT NULL DEFAULT 0,
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE home_done (
  id        bigserial PRIMARY KEY,
  chore_id  bigint NOT NULL REFERENCES home_chores,
  zone_id   bigint NOT NULL REFERENCES home_zones,
  done_at   timestamptz NOT NULL DEFAULT now(),
  day       date NOT NULL,                                           -- log_day(done_at)
  source    text NOT NULL CHECK (source IN ('web', 'chat', 'pick')),
  raw_id    bigint REFERENCES raw_inbox (id)
);
CREATE INDEX home_done_chore ON home_done (chore_id, zone_id, done_at DESC);
CREATE INDEX home_done_day ON home_done (day);
CREATE INDEX home_done_raw ON home_done (raw_id) WHERE raw_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE, DELETE ON home_zones, home_chores, home_done TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE home_zones_id_seq, home_chores_id_seq, home_done_id_seq TO core_rw;
