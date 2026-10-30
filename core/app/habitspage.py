"""The web app's Habits page. Today (decided 29.10.2026): today's habits as large buttons to tick, and the last 30 days of every tracked
habit as squares that can be clicked to tick or untick that day. Your habits (decided 30.10.2026): every habit of the database in one
list, grouped, with a Track switch (Today and the calendar show tracked habits only), Edit, Retire / Bring back, Delete (only a habit
that was never ticked) and a form for a new habit. Wake-up and Bedtime are times, not habits: they are not listed here (the Health card
and the Health page record them). A habit is tracked (on the checklist and active), not tracked (active only) or retired (not active).
The forms post to /web/habit, /web/habit/day (webactions) and /web/habits/<action> (webhabits)."""
from datetime import timedelta

from .db import connect
from .webentry import esc, hidden, here, key

DAYS = 30
LOCKED = {"wake"}  # a time, not a habit; migration 017 took it off the checklist, this stays as a guard for the past-day squares
GROUPS = (("intake", "Intake"), ("practice", "Practice"), ("learning", "Learning"), ("daily", "Daily routine"), ("behaviour", "Behaviour"))
SHOWS = (("tracked", "Tracked"), ("untracked", "Not tracked"), ("retired", "Retired"), ("all", "All"))
HELP = {"tracked": "Tracked habits show on Today (unless hidden under Settings > Today page) and in the calendar below.",
        "untracked": "Not tracked: in the database and still loggable in the chat, but not shown on Today. Switch Track on to start.",
        "retired": "Retired: hidden everywhere; the history stays. Bring one back any time.",
        "all": "Every habit in the database, grouped."}


def _dm(d) -> str:
    return d.strftime("%a %d.%m.")


def state(r) -> str:
    return "retired" if not r["active"] else "tracked" if r["on_checklist"] else "untracked"


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
    cur.execute("""SELECT t.name, t.label, t.category, t.daily_target, t.on_checklist, t.active,
                          count(e.id)::int AS ticks, count(DISTINCT e.day) FILTER (WHERE e.day BETWEEN %s AND %s)::int AS days30,
                          max(e.day) AS last_day
                   FROM log_types t LEFT JOIN events e ON e.type_id = t.id
                   WHERE t.kind = 'event' AND t.category <> 'sleep' GROUP BY t.id ORDER BY t.sort_order, t.name""", (start, today))
    manage = cur.fetchall()
    return {"today": today, "start": start, "now": now, "last_tick": last_tick, "habits": habits, "done": done, "manage": manage}


def _today(d: dict, csrf: str) -> str:
    if not d["now"]:
        return '<p class="empty">No habit is tracked. Switch Track on for one below.</p>'
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


def _stats(r: dict, today) -> str:
    if not r["ticks"]:
        return "Never ticked"
    last = r["last_day"]
    when = f"{last:%d.%m.}" if last.year == today.year else f"{last:%d.%m.%Y}"
    if r["days30"]:
        return f'{r["days30"]} of the last {DAYS} days · last {when}'
    return f"Last ticked {when}"


def _row(r: dict, csrf: str, today, group: str) -> str:
    st, name, label = state(r), r["name"], esc(r["label"])
    form = lambda action: (f'<form method="post" action="/web/habits/{action}">{hidden("csrf", csrf)}{hidden("name", name)}'
                           f'{here("/web/habits")}')
    tag = {"retired": ' <span class="pill warn">Retired</span>', "untracked": ' <span class="pill">Not tracked</span>', "tracked": ""}[st]
    ctl = []
    if st != "retired":
        on = st == "tracked"
        ctl.append(f'{form("track")}{hidden("on", "0" if on else "1")}<button class="hm-sw{" on" if on else ""}" type="submit" role="switch" '
                   f'aria-checked="{str(on).lower()}" aria-label="Track {label}"></button><span class="small">Track</span></form>')
    ctl.append(f'<details class="hm-pop"><summary class="hm-link">Edit</summary>{form("edit")}'
               f'<input name="label" value="{esc(r["label"])}" maxlength="40" required aria-label="Name of {label}">'
               f'<input type="number" name="daily_target" min="1" max="10" value="{r["daily_target"]}" aria-label="Times a day for {label}">'
               '<span class="small">times a day</span><button class="primary sm" type="submit">Save</button></form></details>')
    ctl.append(f'{form("restore")}<button class="hm-link" type="submit">Bring back</button></form>' if st == "retired"
               else f'{form("retire")}<button class="hm-link" type="submit">Retire</button></form>')
    if not r["ticks"]:
        ctl.append(f'<details class="hm-pop"><summary class="hm-link bad">Delete</summary>{form("delete")}{hidden("confirmed", "1")}'
                   f'<span>Delete {label}? It was never ticked, so nothing is lost. This cannot be undone.</span>'
                   '<button class="ghost bad sm" type="submit">Yes, delete</button></form></details>')
    plural = "time" if r["daily_target"] == 1 else "times"
    return (f'<div class="hm" id="hab-{esc(name)}"><div class="hm-main"><b>{label}</b>{tag}'
            f'<span class="small">{esc(group)} · {r["daily_target"]} {plural} a day</span></div>'
            f'<div class="hm-stats small">{_stats(r, today)}</div><div class="hm-ctl">{"".join(ctl)}</div></div>')


def _manage(d: dict, csrf: str, show: str) -> str:
    rows = d["manage"]
    counts = {k: sum(1 for r in rows if k == "all" or state(r) == k) for k, _l in SHOWS}
    chips = ""
    for k, label in SHOWS:
        here_ = ' aria-current="true"' if k == show else ""
        chips += f'<a class="chip{" on" if k == show else ""}" href="/web/habits?show={k}"{here_}>{label} · {counts[k]}</a>'
    known = dict(GROUPS)
    shown = [r for r in rows if show == "all" or state(r) == show]
    out = []
    for cat, title in list(GROUPS) + [("", "Other")]:
        part = [r for r in shown if (r["category"] == cat if cat else r["category"] not in known)]
        if part:
            out.append(f'<h3>{title} · {len(part)}</h3>' + "".join(_row(r, csrf, d["today"], title) for r in part))
    return (f'<h2>Your habits</h2><div class="chips" role="group" aria-label="Show">{chips}</div>'
            f'<p class="small hint">{HELP[show]}</p>{"".join(out) or "<p class=empty>Nothing here.</p>"}')


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
            state_ = "done" if n >= target else f"{n} of {target}" if n else "not logged"
            tip = f"{_dm(day)}: {state_}"
            if h["name"] in LOCKED:
                squares.append(f'<span class="sq {cls}" title="{esc(tip)}"></span>')
            else:
                what = "click to take the ticks off" if n >= target else "click to tick"
                squares.append(f'<button class="sq {cls}" type="submit" name="day" value="{day.isoformat()}" title="{esc(tip)}" '
                               f'aria-label="{esc(h["label"])} {esc(tip)}, {what}"></button>')
        rows.append(f'<form class="calrow" method="post" action="/web/habit/day">{hidden("csrf", csrf)}{hidden("name", h["name"])}'
                    f'{hidden("key", key())}{here("/web/habits")}'
                    f'<span class="lbl">{esc(h["label"])} <span class="small">{full}/{DAYS}</span></span><span class="days">{"".join(squares)}</span></form>')
    return (f'<h2>Last {DAYS} days</h2><div class="hcal">{"".join(rows)}'
            f'<div class="calrow"><span></span><span class="axis"><span>{d["start"]:%d.%m.}</span><span>today</span></span></div></div>'
            '<p class="legend"><i class="key sq d"></i>done <i class="key sq h"></i>partly <i class="key sq"></i>not logged</p>'
            '<p class="small hint">Click a square to tick that day; click a done square to take that day\'s ticks off. '
            'A tick for a past day has no time of its own. Only tracked habits are drawn.</p>')


def _new(csrf: str) -> str:
    opts = "".join(f'<option value="{k}"{" selected" if k == "practice" else ""}>{v}</option>' for k, v in GROUPS)
    return (f'<h2>New habit</h2><form class="row" method="post" action="/web/habits/new">{hidden("csrf", csrf)}{here("/web/habits")}'
            '<input name="label" maxlength="40" required class="grow" placeholder="Name, e.g. Stretching" aria-label="Name of the new habit">'
            f'<label class="small">Group <select name="group">{opts}</select></label>'
            '<label class="small">Times a day <input type="number" name="daily_target" min="1" max="10" value="1"></label>'
            '<label class="row small"><input type="checkbox" name="track" value="1" checked> Track it now</label>'
            '<button class="primary" type="submit">Add</button></form>'
            '<p class="small hint">Then log it in the chat or the entry box by its name ("did stretching") or tick it above.</p>')


def render(csrf: str, q=None) -> str:
    show = (q or {}).get("show")
    show = show if show in dict(SHOWS) else "tracked"
    with connect() as conn, conn.cursor() as cur:
        d = load(cur)
    return ('<div class="head"><h1>Habits</h1><span class="row"><a class="primary" href="#new-habit">+ New habit</a>'
            '<a class="ghost" href="/web/settings?tab=habits">Settings</a></span></div>'
            f'<div class="card">{_today(d, csrf)}</div><div class="card">{_manage(d, csrf, show)}</div>'
            f'<div class="card">{_calendar(d, csrf)}</div><div class="card" id="new-habit">{_new(csrf)}</div>')
