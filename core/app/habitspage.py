"""The web app's Habits page (decided 29.10.2026): today's habits as large buttons to tick, and the last 30 days of every
habit as squares that can be clicked to tick or untick that day. All active habits are shown here; hiding one on the Today page
(Settings > Today page) does not hide it here. The forms post to /web/habit and /web/habit/day (webactions)."""
from datetime import timedelta

from .db import connect
from .webentry import esc, hidden, here, key

DAYS = 30
LOCKED = {"wake"}  # the Wake-up habit is the day's wake time (it starts the logging day and the sleep): Today only, never a past day


def _dm(d) -> str:
    return d.strftime("%a %d.%m.")


def load(cur) -> dict:
    cur.execute("SELECT log_day(now()) AS d")
    today = cur.fetchone()["d"]
    cur.execute("SELECT type_name, label, done, target, complete FROM checklist_status(%s)", (today,))
    now = cur.fetchall()
    cur.execute("""SELECT DISTINCT ON (t.name) t.name, e.id FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE e.day = %s ORDER BY t.name, e.event_at DESC, e.id DESC""", (today,))
    last_tick = {r["name"]: r["id"] for r in cur.fetchall()}
    cur.execute("""SELECT name, label, daily_target FROM log_types WHERE on_checklist AND active AND kind = 'event'
                   ORDER BY sort_order, name""")
    habits = cur.fetchall()
    start = today - timedelta(days=DAYS - 1)
    cur.execute("""SELECT t.name, e.day, count(*)::int AS n FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.on_checklist AND e.day BETWEEN %s AND %s GROUP BY 1, 2""", (start, today))
    done = {(r["name"], r["day"]): r["n"] for r in cur.fetchall()}
    return {"today": today, "start": start, "now": now, "last_tick": last_tick, "habits": habits, "done": done}


def _today(d: dict, csrf: str) -> str:
    if not d["now"]:
        return '<p class="empty">No habits yet. Add one under Settings > Habits.</p>'
    btns = []
    for h in d["now"]:
        done = h["complete"]
        count = f' <span class="cnt">{h["done"]}/{h["target"]}</span>' if h["target"] > 1 else ""
        act = (hidden("action", "untick") + hidden("eid", str(d["last_tick"].get(h["type_name"], "")))) if done else (hidden("action", "tick") + hidden("key", key()))
        btns.append(f'<form method="post" action="/web/habit">{hidden("csrf", csrf)}{hidden("name", h["type_name"])}{act}{here("/web/habits")}'
                    f'<button class="habit big{" done" if done else ""}" type="submit" aria-pressed="{str(done).lower()}">'
                    f'<span class="box">{"&#10003;" if done else ""}</span>{esc(h["label"])}{count}</button></form>')
    n_done = sum(1 for h in d["now"] if h["complete"])
    return (f'<h2>Today · {n_done} of {len(d["now"])} done</h2><div class="habits">{"".join(btns)}</div>'
            '<p class="small hint">Click to tick, click again to untick.</p>')


def _calendar(d: dict, csrf: str) -> str:
    if not d["habits"]:
        return ""
    days = [d["start"] + timedelta(days=i) for i in range(DAYS)]
    rows = []
    for h in d["habits"]:
        target, squares, full = h["daily_target"] or 1, [], 0
        for day in days:
            n = d["done"].get((h["name"], day), 0)
            full += n >= target
            cls = "d" if n >= target else "h" if n else ""
            state = "done" if n >= target else f"{n} of {target}" if n else "not logged"
            tip = f"{_dm(day)}: {state}"
            if h["name"] in LOCKED:
                squares.append(f'<span class="sq {cls}" title="{esc(tip)}"></span>')
            else:
                what = "click to take the ticks off" if n >= target else "click to tick"
                squares.append(f'<button class="sq {cls}" type="submit" name="day" value="{day.isoformat()}" title="{esc(tip)}" '
                               f'aria-label="{esc(h["label"])} {esc(tip)}, {what}"></button>')
        rows.append(f'<form class="calrow" method="post" action="/web/habit/day">{hidden("csrf", csrf)}{hidden("name", h["name"])}'
                    f'{hidden("key", key())}{here("/web/habits")}'
                    f'<span class="lbl">{esc(h["label"])} <span class="small">{full}/{DAYS}</span></span><span class="days">{"".join(squares)}</span></form>')
    note = (' The Wake-up row is read-only: it is the day\'s wake time, set on Today or the Health page.'
            if any(h["name"] in LOCKED for h in d["habits"]) else "")
    return (f'<h2>Last {DAYS} days</h2><div class="hcal">{"".join(rows)}'
            f'<div class="calrow"><span></span><span class="axis"><span>{d["start"]:%d.%m.}</span><span>today</span></span></div></div>'
            '<p class="legend"><i class="key sq d"></i>done <i class="key sq h"></i>partly <i class="key sq"></i>not logged</p>'
            '<p class="small hint">Click a square to tick that day; click a done square to take that day\'s ticks off. '
            f'A tick for a past day has no time of its own.{note}</p>')


def render(csrf: str) -> str:
    with connect() as conn, conn.cursor() as cur:
        d = load(cur)
    return ('<div class="head"><h1>Habits</h1><a class="ghost" href="/web/settings?tab=habits">Edit habits</a></div>'
            f'<div class="card">{_today(d, csrf)}</div><div class="card">{_calendar(d, csrf)}</div>')
