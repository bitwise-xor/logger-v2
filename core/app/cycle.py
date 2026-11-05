"""The day follows the wake-up (05.11.2026, for an irregular or drifting sleep cycle; migration 029, IMPROVEMENTS items 59 to 62).

- An entry's day is the day of the last wake-up in the 18 hours before it (as food always was), else the 05:00 rule. SQL log_day()
  is the same rule, so every page's "today" changes at the wake-up, also when it is between midnight and 05:00.
- A wake-up's day is its own calendar date, or the next date when an earlier wake-up already has that date (SQL wake_day()).
- A wake-up soon after the last one, or after a short sleep, asks first: a new day, or up from a nap or a night waking? A nap
  (type 'nap') counts as sleep but starts no day.
- The watch's sleep (Apple Health import) can suggest a wake-up time that was not typed."""
from datetime import date, datetime, timedelta
from typing import Optional

from .when import BERLIN, _day_of

DAY_H = 18  # food_day()'s window: entries up to 18 hours after a wake-up belong to its day
NEW_DAY_H = 12  # a wake-up less than 12 hours after the last one asks: new day, or a nap / night waking?
SHORT_SLEEP_H = 3  # ... and so does one after less than 3 hours of sleep
NAP_ANSWER = "nap"
NAP_HINT = "if it was a nap or a night waking, answer nap"  # in the read-back; logbook offers the nap answer when it sees it
WATCH_H = 12  # the watch's wake-up is offered for 12 hours
WATCH_SLACK_H = 2  # ... unless a wake-up or nap was logged from 2 hours before it on


def today(cur) -> date:
    cur.execute("SELECT log_day(now()) AS d")
    return cur.fetchone()["d"]


def hours(span: timedelta) -> str:
    h, m = divmod(round(span.total_seconds() / 60), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def entry_day(cur, name: str, ts: datetime, date_only: bool, wakes=(), except_id: Optional[int] = None) -> date:
    """The day of a health entry. wakes: [(time, day)] of wake-ups in the same message, not saved yet. except_id: the event itself,
    when its time or type is changed (a wake-up that becomes a nap must not start its own day)."""
    if date_only:
        return _day_of(ts, name)
    if name == "wake":
        cur.execute("SELECT wake_day(%s, %s) AS d", (ts, except_id))
        return cur.fetchone()["d"]
    cur.execute("""SELECT e.event_at, e.day FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = 'wake' AND e.event_at <= %s AND e.event_at > %s AND e.id IS DISTINCT FROM %s
                   ORDER BY e.event_at DESC LIMIT 1""", (ts, ts - timedelta(hours=DAY_H), except_id))
    r = cur.fetchone()
    found = [(r["event_at"], r["day"])] if r else []
    found += [w for w in wakes if w[0] <= ts < w[0] + timedelta(hours=DAY_H)]
    return max(found)[1] if found else _day_of(ts, name)


def _last_bed(cur, ts: datetime, beds=()) -> Optional[datetime]:
    """The bed time of the sleep that ends at ts: the latest bed in the 24 hours before with no wake-up or nap in between."""
    cur.execute("""SELECT e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = 'bed' AND e.event_at < %s AND e.event_at >= %s
                     AND NOT EXISTS (SELECT 1 FROM events x JOIN log_types tx ON tx.id = x.type_id AND tx.name IN ('wake', 'nap')
                                     WHERE x.event_at > e.event_at AND x.event_at < %s)
                   ORDER BY e.event_at DESC LIMIT 1""", (ts, ts - timedelta(hours=24), ts))
    r = cur.fetchone()
    found = ([r["event_at"]] if r else []) + [b for b in beds if ts - timedelta(hours=24) <= b < ts]
    return max(found) if found else None


def new_day_question(cur, ts: datetime, day: date, beds=()) -> Optional[str]:
    """The read-back for a wake-up that may be a nap or a night waking: less than NEW_DAY_H hours after the last wake-up, or after
    less than SHORT_SLEEP_H hours of sleep. beds: bed times in the same message. None when it is clearly a new day."""
    cur.execute("""SELECT e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = 'wake' AND e.event_at < %s ORDER BY e.event_at DESC LIMIT 1""", (ts,))
    r = cur.fetchone()
    since = ts - r["event_at"] if r else None
    bed = _last_bed(cur, ts, beds)
    slept = ts - bed if bed else None
    soon = since is not None and since < timedelta(hours=NEW_DAY_H)
    short = slept is not None and slept < timedelta(hours=SHORT_SLEEP_H)
    if not (soon or short):
        return None
    why = []
    if soon:
        why.append(f"{hours(since)} after the last wake-up ({r['event_at'].astimezone(BERLIN):%d.%m. %H:%M})")
    if short:
        why.append(f"after {hours(slept)} of sleep")
    return (f"up at {ts.astimezone(BERLIN):%H:%M}, " + " and ".join(why) + f". As a wake-up it starts a new day ({day:%d.%m.}); "
            + NAP_HINT)


def naps(cur, day: date) -> list[dict]:
    """The day's naps and night wakings that have a bed time: [{bed_at, wake_at, hours}]."""
    cur.execute("SELECT bed_at, wake_at, hours FROM v_sleeps WHERE nap AND wake_day = %s ORDER BY wake_at", (day,))
    return cur.fetchall()


def watch_wake(cur, now: datetime) -> Optional[datetime]:
    """When the watch says the last sleep ended, if that was in the last WATCH_H hours and no wake-up or nap was typed for it."""
    from . import watch  # here: watch imports logbook, which imports this module
    cur.execute("""SELECT extra FROM watch_metrics WHERE metric = 'sleep_analysis' AND at >= %s AND at <= %s""",
                (now - timedelta(hours=36), now))
    ends = []
    for r in cur.fetchall():
        ex = r["extra"] or {}
        if ex.get("kind") == "night":
            end = ex.get("sleepEnd") or ex.get("inBedEnd")
        elif ex.get("kind") == "segment" and str(ex.get("stage", "")).lower().replace(" ", "") not in ("awake", "inbed"):
            end = ex.get("end")
        else:
            end = None
        try:
            if end:
                ends.append(watch.when(end))
        except ValueError:
            continue
    end = max(ends) if ends else None
    if not end or end > now or now - end > timedelta(hours=WATCH_H):
        return None
    cur.execute("""SELECT 1 FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name IN ('wake', 'nap') AND e.event_at >= %s LIMIT 1""", (end - timedelta(hours=WATCH_SLACK_H),))
    return None if cur.fetchone() else end.astimezone(BERLIN)
