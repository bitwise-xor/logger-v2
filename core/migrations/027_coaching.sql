-- Coaching (web item 52, 04.11.2026; plan and the owner's answers in the doc "Logger v2: status and web app plan", tab Current;
-- mockup https://claude.ai/artifact/4z4HLimQeLi2jo4Z6ZX3ND): a day coaching (Today) or a week review (Week page) by Claude Opus,
-- on demand only. Each one is kept; "Coach me again" adds a new row. status: working while the AI reads (in a background thread),
-- then done (body = the coach's sections as JSON) or failed (error = the kind of failure, never data). Not visible to the Google
-- Sheet's reader.
CREATE TABLE coachings (
  id           bigserial PRIMARY KEY,
  kind         text NOT NULL CHECK (kind IN ('day', 'week')),
  start_day    date NOT NULL,              -- the day, or the first day of the week
  end_day      date NOT NULL,              -- the same day, or the last day of the week
  status       text NOT NULL DEFAULT 'working' CHECK (status IN ('working', 'done', 'failed')),
  body         jsonb,
  model        text,
  usd          numeric(8,5),
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  CHECK (end_day >= start_day)
);
CREATE INDEX coachings_period ON coachings (kind, start_day, end_day, id DESC);
CREATE INDEX coachings_created ON coachings (created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON coachings TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE coachings_id_seq TO core_rw;
