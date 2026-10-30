"""Nudges (31.10.2026): small reminders sent as phone notifications (web push), checked every 5 minutes by cron:
`docker exec logger-v2-core python -m app.nudges`. The times count from the day's wake-up time; nothing is sent before
waking, after a bed time logged that evening, or more than 16 hours after waking (quiet hours). Each nudge goes at most
once a logging day (push_sent). The owner turns them on and picks them in Settings > Notifications (ui_settings "nudges")."""
import json
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from . import logbook, webpush
from .db import connect
from .when import BERLIN

KEY = "nudges"
DEFAULTS = {"on": True, "weight": True, "weight_after": 2.0, "habits": True, "habits_after": 14.0, "idle": True, "idle_hours": 5.0}
AWAKE_HOURS = 16
CHANNELS = ["web", "claude-chat", "mcp"]  # what counts as logging something (imports do not)


def load(cur) -> dict:
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (KEY,))
    r = cur.fetchone()
    return {**DEFAULTS, **(r["value"] if r else {})}


def save(values: dict) -> None:
    with connect() as c:
        c.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (KEY, Jsonb(values)))


def due(cur, now: datetime) -> list[dict]:
    """The nudges to send now and not sent yet this logging day."""
    s = load(cur)
    if not s["on"]:
        return []
    cur.execute("SELECT log_day(%s) AS d", (now,))
    day = cur.fetchone()["d"]
    cur.execute("""SELECT e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = 'wake' AND e.day = %s ORDER BY e.event_at DESC LIMIT 1""", (day,))
    w = cur.fetchone()
    if not w:
        return []  # not up yet
    wake = w["event_at"]
    if now < wake or now > wake + timedelta(hours=AWAKE_HOURS):
        return []
    cur.execute("""SELECT 1 FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = 'bed' AND e.event_at > %s AND e.event_at <= %s""", (wake, now))
    if cur.fetchone():
        return []  # gone to bed
    out = []
    if s["weight"] and now >= wake + timedelta(hours=float(s["weight_after"])):
        cur.execute("SELECT 1 FROM measurements m JOIN log_types t ON t.id = m.type_id WHERE t.name = 'weight' AND m.day = %s", (day,))
        if not cur.fetchone():
            out.append({"kind": "weight", "title": "Weight", "body": "No weight logged yet today."})
    if s["habits"] and now >= wake + timedelta(hours=float(s["habits_after"])):
        cur.execute("SELECT label FROM checklist_status(%s) WHERE NOT complete", (day,))
        still = [r["label"] for r in cur.fetchall()]
        if still:
            out.append({"kind": "habits", "title": "Habits still open", "body": ", ".join(still)})
    if s["idle"]:
        cur.execute("SELECT max(received_at) AS t FROM raw_inbox WHERE channel = ANY(%s)", (CHANNELS,))
        last = cur.fetchone()["t"]
        since = max(x for x in (last, wake) if x)
        hours = float(s["idle_hours"])
        if now - since >= timedelta(hours=hours):  # one nudge per quiet stretch (its start names it)
            out.append({"kind": f"idle-{since.astimezone(BERLIN):%H%M}", "title": "Nothing logged",
                        "body": f"Nothing logged for {hours:g} hours. What did you eat or do?"})
    cur.execute("SELECT kind FROM push_sent WHERE day = %s", (day,))
    sent = {r["kind"] for r in cur.fetchall()}
    return [dict(n, day=day) for n in out if n["kind"] not in sent]


def deliver(message: dict) -> int:
    """Send one message to every subscribed device; drop the ones the push service says are gone. Devices reached."""
    with connect() as c:
        subs = c.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions ORDER BY id").fetchall()
    ok = 0
    for s in subs:
        status = webpush.send(s, message)
        with connect() as c:
            if status in (404, 410):
                c.execute("DELETE FROM push_subscriptions WHERE id = %s", (s["id"],))
            elif 200 <= status < 300:
                ok += 1
                c.execute("UPDATE push_subscriptions SET last_ok = now(), failures = 0 WHERE id = %s", (s["id"],))
            else:
                c.execute("UPDATE push_subscriptions SET failures = failures + 1 WHERE id = %s", (s["id"],))
    return ok


def run(now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    with connect() as conn, conn.cursor() as cur:
        todo = due(cur, now)
        cur.execute("SELECT count(*) AS n FROM push_subscriptions")
        devices = cur.fetchone()["n"]
    sent = 0
    if devices:
        for n in todo:
            sent += bool(deliver({"title": n["title"], "body": n["body"], "url": "/web/", "tag": n["kind"]}))
            with connect() as c, c.cursor() as cur:  # marked even when no device took it: no repeats every 5 minutes
                cur.execute("INSERT INTO push_sent (day, kind) VALUES (%s, %s) ON CONFLICT DO NOTHING", (n["day"], n["kind"]))
                logbook._audit(cur, "nudge", "sent", {"kind": n["kind"]}, "nudges")
    return {"due": len(todo), "sent": sent, "devices": devices}


if __name__ == "__main__":
    print(json.dumps(run()))
