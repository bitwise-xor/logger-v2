-- Home page step 2 (03.11.2026; plan in the doc "Logger v2: status and web app plan", tab Current): "Not today" (a job hidden for one
-- logging day) and the pick timer. A timer keeps its end on the server (ends_at; NULL while paused, then left_s holds the seconds left), so
-- a reload or a locked phone loses nothing; the page only draws the countdown. pairs = [[chore id, zone id], ...] (one job, or a zone
-- session). A timer that ran to its end counts as a session for the weekly goal (worked out from home_done and home_timers, nothing is
-- stored twice). Not visible to the Google Sheet's reader.
CREATE TABLE home_skips (
  chore_id bigint NOT NULL REFERENCES home_chores,
  zone_id  bigint NOT NULL REFERENCES home_zones,
  day      date NOT NULL,                  -- the logging day it is hidden on
  PRIMARY KEY (chore_id, zone_id, day)
);

CREATE TABLE home_timers (
  id         bigserial PRIMARY KEY,
  day        date NOT NULL,                -- log_day when started
  minutes    integer NOT NULL CHECK (minutes BETWEEN 1 AND 240),
  pairs      jsonb NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  ends_at    timestamptz,                  -- NULL while paused or when stopped before the end
  left_s     integer CHECK (left_s >= 0),  -- seconds left while paused
  closed     boolean NOT NULL DEFAULT false
);
CREATE INDEX home_timers_open ON home_timers (id DESC) WHERE NOT closed;

GRANT SELECT, INSERT, UPDATE, DELETE ON home_skips, home_timers TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE home_timers_id_seq TO core_rw;
