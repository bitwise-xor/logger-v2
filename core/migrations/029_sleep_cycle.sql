-- A day follows the wake-up (05.11.2026, owner's request; IMPROVEMENTS items 59 to 62), for an irregular or drifting sleep cycle.
-- 1. log_day(ts), the logging day every page and entry uses: the day of the last wake-up in the 18 hours before ts (food_day, the rule
--    food already followed), else the 05:00 rule as before. A wake-up between midnight and 05:00 now starts its day at once; the pages
--    showed the day before until 05:00 and left the wake-up and the bed time out.
-- 2. wake_day(ts): a wake-up's day is its own calendar date, or the next date when an earlier wake-up already has that date (two
--    wake-ups on one date, 01:00 and 23:00: the second starts the next day instead of "already logged"). A date the cycle skips
--    (up 23:50 on one day, next up 00:30 two dates later) stays an empty day.
-- 3. A new type 'nap': getting up after a nap or a night waking. It counts as sleep (v_sleeps) but starts no day, and a bed before it
--    no longer pairs with the next real wake-up (v_sleep, v_sleeps).
CREATE OR REPLACE FUNCTION log_day(ts timestamptz) RETURNS date
  LANGUAGE sql STABLE AS
$$ SELECT coalesce(food_day(ts), ((ts AT TIME ZONE 'Europe/Berlin') - interval '5 hours')::date) $$;

CREATE FUNCTION wake_day(p_ts timestamptz, p_except bigint DEFAULT NULL) RETURNS date
  LANGUAGE sql STABLE AS
$$
  SELECT CASE WHEN EXISTS (SELECT 1 FROM events e JOIN log_types t ON t.id = e.type_id AND t.name = 'wake'
                           WHERE e.day = x.d AND e.event_at < p_ts AND e.id IS DISTINCT FROM p_except)
              THEN x.d + 1 ELSE x.d END
  FROM (SELECT (p_ts AT TIME ZONE 'Europe/Berlin')::date AS d) x
$$;
GRANT EXECUTE ON FUNCTION wake_day(timestamptz, bigint) TO core_rw;

CREATE INDEX IF NOT EXISTS events_type_at ON events (type_id, event_at);

INSERT INTO log_types (name, label, kind, category, aliases, on_checklist, active, sort_order, notes)
VALUES ('nap', 'Up from a nap', 'event', 'sleep', '{"up from a nap","up from nap","night waking","after a nap","nap over"}', false, true, 21,
        'getting up after a nap or a night waking: counts as sleep, starts no day')
ON CONFLICT (name) DO NOTHING;

-- One row per wake-up, paired with the latest bed time before it (at most 24 h earlier, no wake-up or nap in between).
CREATE OR REPLACE VIEW v_sleep AS
SELECT w.day AS wake_day, b.event_at AS bed_at, w.event_at AS wake_at,
       round(extract(epoch FROM (w.event_at - b.event_at)) / 3600.0, 2) AS hours
FROM events w
JOIN log_types tw ON tw.id = w.type_id AND tw.name = 'wake'
JOIN LATERAL (
  SELECT e.event_at
  FROM events e JOIN log_types tb ON tb.id = e.type_id AND tb.name = 'bed'
  WHERE e.event_at < w.event_at AND e.event_at >= w.event_at - interval '24 hours'
    AND NOT EXISTS (
      SELECT 1 FROM events x JOIN log_types tx ON tx.id = x.type_id AND tx.name IN ('wake', 'nap')
      WHERE x.event_at > e.event_at AND x.event_at < w.event_at)
  ORDER BY e.event_at DESC LIMIT 1
) b ON true;

-- Every sleep with a bed time: the main ones (nap = false, as v_sleep) and naps or night wakings (nap = true).
CREATE VIEW v_sleeps AS
SELECT w.id AS wake_id, w.day AS wake_day, tw.name = 'nap' AS nap, b.event_at AS bed_at, w.event_at AS wake_at,
       round(extract(epoch FROM (w.event_at - b.event_at)) / 3600.0, 2) AS hours
FROM events w
JOIN log_types tw ON tw.id = w.type_id AND tw.name IN ('wake', 'nap')
JOIN LATERAL (
  SELECT e.event_at
  FROM events e JOIN log_types tb ON tb.id = e.type_id AND tb.name = 'bed'
  WHERE e.event_at < w.event_at AND e.event_at >= w.event_at - interval '24 hours'
    AND NOT EXISTS (
      SELECT 1 FROM events x JOIN log_types tx ON tx.id = x.type_id AND tx.name IN ('wake', 'nap')
      WHERE x.event_at > e.event_at AND x.event_at < w.event_at)
  ORDER BY e.event_at DESC LIMIT 1
) b ON true;
GRANT SELECT ON v_sleeps TO core_rw;
