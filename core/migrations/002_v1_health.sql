-- Copied from logger-app sql/schema-v3-health.sql (v1 schema v3). Only change: role mcp_writer -> core_rw, NocoDB grants removed.
-- schema-v3-health.sql  (additive only: new tables, views, functions; nothing existing is changed)
-- Run as app_owner in database appdata:  psql -v ON_ERROR_STOP=1 -U app_owner -d appdata -f schema-v3-health.sql
-- Whole file is one transaction: on any error nothing is created.
--
-- Shape: log_types (type registry) drives two tables.
--   measurements = numbers (weight, sugar, BP with two values)
--   events       = things that happened (wake, bed, meds, habits)
-- Every row carries raw_id (verbatim source) and extractor_version, so it can be re-extracted.
-- event_at = when it happened, logged_at = when it was recorded.
-- day = logical day. Rule for live logging: local time minus 5 hours (see log_day), except wake events,
-- which always belong to their own calendar date. Imported sheet rows use the sheet row date.

BEGIN;

CREATE TABLE log_types (
  id           smallserial PRIMARY KEY,
  name         text NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9_]+$'),
  label        text NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('measurement','event')),
  category     text NOT NULL CHECK (category IN ('vitals','sleep','intake','practice','learning','daily','behaviour')),
  unit         text,                       -- measurements only
  min1         numeric, max1 numeric,      -- plausible range of value1 (read back if outside)
  min2         numeric, max2 numeric,      -- plausible range of value2 (BP diastolic)
  aliases      text[] NOT NULL DEFAULT '{}',
  on_checklist boolean NOT NULL DEFAULT false,
  daily_target smallint NOT NULL DEFAULT 1 CHECK (daily_target >= 1),
  active       boolean NOT NULL DEFAULT true,
  sort_order   smallint NOT NULL DEFAULT 100,
  notes        text
);

CREATE TABLE measurements (
  id                bigserial PRIMARY KEY,
  raw_id            bigint NOT NULL REFERENCES raw_inbox(id),
  type_id           smallint NOT NULL REFERENCES log_types(id),
  day               date NOT NULL,
  event_at          timestamptz NOT NULL,
  time_known        boolean NOT NULL DEFAULT false,   -- false = only the date is known (stored at 12:00)
  period            text CHECK (period IN ('morning','evening','fasted')),
  value1            numeric NOT NULL,                 -- weight, sugar, systolic
  value2            numeric,                          -- diastolic
  note              text,
  details           jsonb NOT NULL DEFAULT '{}',
  logged_at         timestamptz NOT NULL DEFAULT now(),
  extractor_version text NOT NULL,
  needs_review      boolean NOT NULL DEFAULT false,
  review_reason     text,
  import_key        text UNIQUE
);
CREATE INDEX measurements_type_day ON measurements (type_id, day);

CREATE TABLE events (
  id                bigserial PRIMARY KEY,
  raw_id            bigint NOT NULL REFERENCES raw_inbox(id),
  type_id           smallint NOT NULL REFERENCES log_types(id),
  day               date NOT NULL,
  event_at          timestamptz NOT NULL,
  time_known        boolean NOT NULL DEFAULT false,
  note              text,
  details           jsonb NOT NULL DEFAULT '{}',      -- e.g. {"session":2}
  logged_at         timestamptz NOT NULL DEFAULT now(),
  extractor_version text NOT NULL,
  needs_review      boolean NOT NULL DEFAULT false,
  review_reason     text,
  import_key        text UNIQUE
);
CREATE INDEX events_type_day ON events (type_id, day);
CREATE INDEX events_day ON events (day);

-- Logical day for a timestamp (live-logging rule). Wake events are the exception and use their own date.
CREATE FUNCTION log_day(ts timestamptz) RETURNS date
  LANGUAGE sql STABLE AS
$$ SELECT ((ts AT TIME ZONE 'Europe/Berlin') - interval '5 hours')::date $$;

-- Readable views (for NocoDB and for questions)
CREATE VIEW v_measurements AS
SELECT m.id, m.day, t.name AS type, m.period, m.value1, m.value2, t.unit,
       m.event_at, m.time_known, m.note, m.needs_review, m.review_reason, m.raw_id
FROM measurements m JOIN log_types t ON t.id = m.type_id;

CREATE VIEW v_events AS
SELECT e.id, e.day, t.name AS type, e.event_at, e.time_known, e.details, e.note,
       e.needs_review, e.review_reason, e.raw_id
FROM events e JOIN log_types t ON t.id = e.type_id;

-- One row per wake-up, paired with the latest bed event before it (at most 24 h earlier, no wake-up in between).
CREATE VIEW v_sleep AS
SELECT w.day AS wake_day, b.event_at AS bed_at, w.event_at AS wake_at,
       round(extract(epoch FROM (w.event_at - b.event_at)) / 3600.0, 2) AS hours
FROM events w
JOIN log_types tw ON tw.id = w.type_id AND tw.name = 'wake'
JOIN LATERAL (
  SELECT e.event_at
  FROM events e JOIN log_types tb ON tb.id = e.type_id AND tb.name = 'bed'
  WHERE e.event_at < w.event_at AND e.event_at >= w.event_at - interval '24 hours'
    AND NOT EXISTS (   -- no other wake-up between that bed time and this wake-up
      SELECT 1 FROM events x JOIN log_types tx ON tx.id = x.type_id AND tx.name = 'wake'
      WHERE x.event_at > e.event_at AND x.event_at < w.event_at)
  ORDER BY e.event_at DESC LIMIT 1
) b ON true;

-- "What is left today?": checklist types with how many events exist for the day.
CREATE FUNCTION checklist_status(p_day date)
RETURNS TABLE (type_name text, label text, done integer, target integer, complete boolean)
LANGUAGE sql STABLE AS
$$
  SELECT t.name, t.label, count(e.id)::integer, t.daily_target::integer,
         count(e.id) >= t.daily_target
  FROM log_types t
  LEFT JOIN events e ON e.type_id = t.id AND e.day = p_day
  WHERE t.on_checklist AND t.active AND t.kind = 'event'
  GROUP BY t.id, t.name, t.label, t.daily_target, t.sort_order
  ORDER BY t.sort_order, t.name
$$;

-- Type registry seed. Aliases are lower case. Units: kg, mg/dL, mmHg.
INSERT INTO log_types (name, label, kind, category, unit, min1, max1, min2, max2, aliases, on_checklist, sort_order, active, notes) VALUES
 ('weight',         'Weight',               'measurement','vitals','kg',   30, 300, NULL, NULL, '{gewicht,kg}', false, 10, true, NULL),
 ('blood_sugar',    'Fasted blood sugar',   'measurement','vitals','mg/dL',30, 600, NULL, NULL, '{sugar,bs,blutzucker,glucose}', false, 11, true, 'period = fasted'),
 ('bp',             'Blood pressure',       'measurement','vitals','mmHg', 60, 260, 30, 160,   '{blutdruck,"blood pressure"}', false, 12, true, 'value1 = systolic, value2 = diastolic; period morning or evening'),
 ('wake',           'Wake-up',              'event','sleep',NULL,NULL,NULL,NULL,NULL, '{up,woke,wakeup,"wake up",aufgestanden}', true, 20, true, 'day = own calendar date; "up" without a time means now'),
 ('bed',            'Bedtime',              'event','sleep',NULL,NULL,NULL,NULL,NULL, '{sleep,slept,"to bed",schlafen}', false, 21, true, '"bed" without a time means now'),
 ('meds',           'Medication',           'event','intake',NULL,NULL,NULL,NULL,NULL, '{medication,medikamente,pills,tabletten}', true, 30, true, 'intake only, no name or dose (decided 14.10.2026)'),
 ('supplements',    'Supplements',          'event','intake',NULL,NULL,NULL,NULL,NULL, '{supplement,vitamins}', false, 31, true, NULL),
 ('ayurveda',       'Ayurveda medicines',   'event','intake',NULL,NULL,NULL,NULL,NULL, '{}', false, 32, true, NULL),
 ('triphala',       'Triphala',             'event','intake',NULL,NULL,NULL,NULL,NULL, '{}', false, 33, true, NULL),
 ('isabgol',        'Isabgol',              'event','intake',NULL,NULL,NULL,NULL,NULL, '{}', false, 34, true, NULL),
 ('ghee',           'Ghee 1 tbsp',          'event','intake',NULL,NULL,NULL,NULL,NULL, '{}', false, 35, true, NULL),
 ('morning_concoction','Morning concoction','event','intake',NULL,NULL,NULL,NULL,NULL, '{concoction}', false, 36, true, NULL),
 ('night_hot_drink','Night hot drink',      'event','intake',NULL,NULL,NULL,NULL,NULL, '{}', false, 37, false, 'history only (12-22.09.2026); inactive'),
 ('shoonya',        'Shoonya',              'event','practice',NULL,NULL,NULL,NULL,NULL, '{"shoonya 1","shoonya 2"}', true, 40, true, 'one event per session; details {"session":1|2}; daily_target 1 for now'),
 ('surya_kriya',    'Surya Kriya/Namaskar', 'event','practice',NULL,NULL,NULL,NULL,NULL, '{surya,namaskar}', false, 41, true, NULL),
 ('bhuta_shuddhi',  'Bhuta Shuddhi',        'event','practice',NULL,NULL,NULL,NULL,NULL, '{}', false, 42, true, NULL),
 ('shambhavi',      'Shambhavi',            'event','practice',NULL,NULL,NULL,NULL,NULL, '{}', false, 43, true, NULL),
 ('sck',            'SCK',                  'event','practice',NULL,NULL,NULL,NULL,NULL, '{}', false, 44, true, NULL),
 ('yoga',           'Yoga',                 'event','practice',NULL,NULL,NULL,NULL,NULL, '{}', false, 45, true, NULL),
 ('gym',            'Gym',                  'event','practice',NULL,NULL,NULL,NULL,NULL, '{}', false, 46, true, NULL),
 ('walk',           '1.5 mile walk',        'event','practice',NULL,NULL,NULL,NULL,NULL, '{"1.5 mile walk",spaziergang}', false, 47, true, NULL),
 ('sunlight',       'Sunlight 15 mins',     'event','practice',NULL,NULL,NULL,NULL,NULL, '{sun}', false, 48, true, NULL),
 ('social',         'Social connection',    'event','daily',NULL,NULL,NULL,NULL,NULL, '{}', false, 50, true, NULL),
 ('french',         'French practice',      'event','learning',NULL,NULL,NULL,NULL,NULL, '{}', false, 51, true, NULL),
 ('german',         'German practice',      'event','learning',NULL,NULL,NULL,NULL,NULL, '{}', false, 52, true, NULL),
 ('ai_practice',    'AI practice',          'event','learning',NULL,NULL,NULL,NULL,NULL, '{}', false, 53, true, NULL),
 ('journaling',     'Evening journaling',   'event','daily',NULL,NULL,NULL,NULL,NULL, '{}', false, 54, true, NULL),
 ('brush_teeth',    'Brush teeth',          'event','daily',NULL,NULL,NULL,NULL,NULL, '{}', false, 60, true, NULL),
 ('shower',         'Shower',               'event','daily',NULL,NULL,NULL,NULL,NULL, '{dusche}', false, 61, true, NULL),
 ('dishes',         'Dishes in the sink',   'event','daily',NULL,NULL,NULL,NULL,NULL, '{}', false, 62, true, NULL),
 ('five_min_cleaning','5 min cleaning',     'event','daily',NULL,NULL,NULL,NULL,NULL, '{}', false, 63, true, NULL),
 ('scrolling',      'Scrolling',            'event','behaviour',NULL,NULL,NULL,NULL,NULL, '{}', false, 70, true, NULL),
 ('p',              'P',                    'event','behaviour',NULL,NULL,NULL,NULL,NULL, '{}', false, 71, true, NULL),
 ('fanta_m',        'Fanta M',              'event','behaviour',NULL,NULL,NULL,NULL,NULL, '{fanta}', false, 72, true, NULL);

-- Rights, per table on purpose (same pattern as the thought tables)
GRANT SELECT ON log_types TO core_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON measurements, events TO core_rw;
GRANT SELECT ON v_measurements, v_events, v_sleep TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE log_types_id_seq, measurements_id_seq, events_id_seq TO core_rw;
GRANT EXECUTE ON FUNCTION log_day(timestamptz), checklist_status(date) TO core_rw;

COMMIT;
