"""The web app's Gym section (02.11.2026, step (a) of the plan in the doc "Logger v2: status and web app plan"): the Workout
tab (today's list: one tap on the circle logs an exercise with the numbers shown, a tap on the name opens − / + to change
them; Add exercise; the last 4 weeks), the Exercises tab (the owner's list with Retire / Bring back and New exercise) and the
Gym card on Today. Until the split (the Plan tab, next step) exists, today's list is every active exercise in the list's
order. The actions are in gym.py."""
from datetime import timedelta

from . import gym, rules
from .db import connect
from .webentry import esc, here, hidden, icon, key

TABS = (("workout", "/web/gym", "Workout"), ("exercises", "/web/gym/exercises", "Exercises"))
# The first time an exercise is done there is nothing to repeat: its row opens these numbers to set first
DEFAULT = {"weights": {"sets": 3, "reps": 8, "kg": None, "minutes": None, "km": None},
           "bodyweight": {"sets": 3, "reps": 10, "kg": None, "minutes": None, "km": None},
           "cardio": {"sets": None, "reps": None, "kg": None, "minutes": 20, "km": None}}
FIELDS = {"weights": (("sets", "Sets", 1, 1, 50), ("reps", "Reps", 1, 1, 500), ("kg", "kg", 2.5, 0, 1000)),
          "bodyweight": (("sets", "Sets", 1, 1, 50), ("reps", "Reps", 1, 1, 500)),
          "cardio": (("minutes", "Minutes", 1, 1, 600), ("km", "km", 0.1, 0, 200))}
VALS = ("sets", "reps", "kg", "minutes", "km")


def _tabs(on: str) -> str:
    links = "".join(f'<a href="{href}"' + (' aria-current="page"' if k == on else "") + f">{label}</a>" for k, href, label in TABS)
    return f'<div class="head"><h1>Gym</h1><nav class="seg" aria-label="Gym">{links}</nav></div>'


def _plain(x) -> str:
    return "" if x is None else format(float(x), "g")


def load(cur) -> dict:
    cur.execute("SELECT log_day(now()) AS d")
    today = cur.fetchone()["d"]
    cur.execute("SELECT * FROM exercises ORDER BY sort_order, lower(name)")
    exercises = cur.fetchall()
    cur.execute("""SELECT s.*, x.kind, w.started_at FROM workout_sets s JOIN workouts w ON w.id = s.workout_id
                   JOIN exercises x ON x.id = s.exercise_id WHERE w.day = %s ORDER BY s.sort_order, s.id""", (today,))
    now_rows = cur.fetchall()
    # each exercise's last earlier session: all its rows of that day
    cur.execute("""WITH last AS (SELECT s.exercise_id, max(w.day) AS day FROM workout_sets s JOIN workouts w ON w.id = s.workout_id
                                 WHERE w.day < %s GROUP BY s.exercise_id)
                   SELECT s.*, x.kind, w.day FROM workout_sets s JOIN workouts w ON w.id = s.workout_id JOIN exercises x ON x.id = s.exercise_id
                   JOIN last l ON l.exercise_id = s.exercise_id AND l.day = w.day ORDER BY s.exercise_id, s.sort_order, s.id""", (today,))
    last: dict = {}
    for r in cur.fetchall():
        last.setdefault(r["exercise_id"], []).append(r)
    monday = today - timedelta(days=today.weekday())
    cur.execute("SELECT DISTINCT day FROM workouts WHERE day BETWEEN %s AND %s", (monday - timedelta(days=21), today))
    days = {r["day"] for r in cur.fetchall()}
    return {"today": today, "monday": monday, "exercises": exercises, "now": now_rows, "last": last, "days": days,
            "started": min((r["started_at"] for r in now_rows), default=None)}


def _field(ex_id: int, kind: str, vals: dict) -> str:
    out = []
    for name, label, step, lo, hi in FIELDS[kind]:
        fid = f"g{ex_id}-{name}"
        out.append(f'<label class="gstep" for="{fid}"><span class="small">{label}</span><span class="stepper">'
                   f'<button type="button" class="ghost sm" data-step="-{step}" data-for="{fid}" aria-label="{label} down by {step}">−</button>'
                   f'<input type="number" id="{fid}" name="{name}" value="{_plain(vals.get(name))}" min="{lo}" max="{hi}" step="any" inputmode="decimal">'
                   f'<button type="button" class="ghost sm" data-step="{step}" data-for="{fid}" aria-label="{label} up by {step}">+</button></span></label>')
    return "".join(out)


def _row(ex: dict, d: dict, csrf: str) -> str:
    mine = [r for r in d["now"] if r["exercise_id"] == ex["id"]]
    prev = d["last"].get(ex["id"], [])
    pre = mine[-1] if mine else prev[0] if prev else None  # today's last row, else the last session's first row
    vals = {k: pre[k] for k in VALS} if pre else dict(DEFAULT[ex["kind"]])
    up = ""
    if mine and prev and ex["kind"] == "weights":
        top_now, top_then = max(float(r["kg"] or 0) for r in mine), max(float(r["kg"] or 0) for r in prev)
        if top_now > top_then:
            up = f' <span class="pill up">+{_plain(top_now - top_then)} kg</span>'
    said = ", ".join(gym.row_text(r) for r in mine) if mine else gym.fmt(ex["kind"], **vals) if pre else "first time: set the numbers"
    lastline = f'Last {prev[0]["day"]:%a %d.%m.}: {", ".join(gym.row_text(r) for r in prev)}' if prev else "Not done before"
    base = hidden("csrf", csrf) + hidden("exercise", str(ex["id"])) + here("/web/gym")
    if mine:
        tick = (f'<form method="post" action="/web/gym/remove">{base}<button class="gtick done" type="submit" aria-pressed="true" '
                f'aria-label="Take {esc(ex["name"])} off today’s workout"><span class="box">{icon("check")}</span></button></form>')
    elif pre:
        tick = (f'<form method="post" action="/web/gym/tick">{base}{hidden("key", key())}'
                + "".join(hidden(k, _plain(vals[k])) for k in VALS if vals[k] is not None)
                + f'<button class="gtick" type="submit" aria-pressed="false" aria-label="Log {esc(ex["name"])}: {esc(said)}">'
                  f'<span class="box">{icon("check")}</span></button></form>')
    else:
        tick = (f'<span class="gtick off" title="Set the numbers first: tap the name"><span class="box">{icon("check")}</span></span>')
    rows = "".join(f'<form class="gline" method="post" action="/web/gym/remove">{hidden("csrf", csrf)}{hidden("set", str(r["id"]))}{here("/web/gym")}'
                   f'<span>{esc(gym.row_text(r))}</span><button class="icon" type="submit" aria-label="Remove {esc(gym.row_text(r))}" title="Remove">&times;</button></form>'
                   for r in mine) if len(mine) > 1 else ""
    editor = (f'<form class="fe-form gform" method="post" action="/web/gym/tick">{base}{hidden("key", key())}{_field(ex["id"], ex["kind"], vals)}'
              f'<button class="primary sm" type="submit">{"Add another row" if mine else "Log"}</button></form>{rows}')
    return (f'<div class="gx{" done" if mine else ""}">{tick}<details class="gedit"{"" if pre or mine else " open"}>'
            f'<summary><span class="gname">{esc(ex["name"])}</span><span class="gval">{esc(said)}{up}</span>'
            f'<span class="small">{esc(lastline)}</span></summary>{editor}</details></div>')


def _new(csrf: str, d: dict, back: str, open_: bool = False) -> str:
    gone = "".join(f'<option value="{esc(x["name"])}"></option>' for x in d["exercises"] if not x["active"])
    kinds = "".join(f'<option value="{k}">{v}</option>' for k, v in gym.KINDS.items())
    return (f'<details class="lookup"{" open" if open_ else ""}><summary class="ghost">+ {"New" if open_ else "Add"} exercise</summary>'
            f'<form class="fbox" method="post" action="/web/gym/new">{hidden("csrf", csrf)}{here(back)}<div class="fields">'
            '<label>Name<input name="name" maxlength="60" required list="gym-retired" autocomplete="off" placeholder="e.g. Bench press"></label>'
            f'<label>Kind<select name="kind">{kinds}</select></label>'
            '<label>Other words (optional)<input name="words" maxlength="200" placeholder="e.g. bench, chest press"></label></div>'
            f'<datalist id="gym-retired">{gone}</datalist><p class="row"><button class="primary sm" type="submit">Add</button>'
            '<span class="small">A retired exercise of the same name comes back with its history.</span></p></form></details>')


def _weeks(d: dict) -> str:
    """The last 4 weeks, Monday to Sunday, a square per day: consistency without streaks."""
    out, good = [], 0
    heads = "".join(f'<span class="dh">{x}</span>' for x in ("M", "T", "W", "T", "F", "S", "S"))
    for i in range(3, -1, -1):
        start = d["monday"] - timedelta(days=7 * i)
        cells = [start + timedelta(days=j) for j in range(7)]
        n = sum(c in d["days"] for c in cells)
        good += n >= 2
        label = "This week" if i == 0 else "Last week" if i == 1 else f"{start:%d.%m.}"
        sq = "".join(f'<span class="gd{" on" if c in d["days"] else ""}{" today" if c == d["today"] else ""}{" later" if c > d["today"] else ""}"></span>'
                     for c in cells)
        out.append(f'<div class="wk4" role="img" aria-label="{label}: {n} workout{"" if n == 1 else "s"}"><span class="small">{label}</span>{sq}'
                   f'<span class="small num">{n}</span></div>')
    return (f'<h2><span>Last 4 weeks</span><span class="small">{good} of 4 weeks with 2+ workouts</span></h2>'
            f'<div class="wk4 head" aria-hidden="true"><span></span>{heads}<span></span></div>{"".join(out)}')


def render_workout(csrf: str) -> str:
    with connect() as conn, conn.cursor() as cur:
        d = load(cur)
    done_ids = {r["exercise_id"] for r in d["now"]}
    shown = [x for x in d["exercises"] if x["active"] or x["id"] in done_ids]
    status = (f'started {d["started"].astimezone(rules.BERLIN):%H:%M} · {len(done_ids)} done' if d["started"]
              else "not started: the first tick starts it")
    rows = "".join(_row(x, d, csrf) for x in shown) or '<p class="empty">No exercises yet: add your first one below.</p>'
    hint = ('<p class="small hint">Tap the circle to log the numbers shown, tap the name to change them. Undo is in the bar above.</p>'
            if shown else "")
    return (_tabs("workout") + f'<div class="card"><h2><span>Today’s workout</span><span class="small">{status}</span></h2>'
            f'<div class="gx-list">{rows}</div>{hint}{_new(csrf, d, "/web/gym")}</div><div class="card">{_weeks(d)}</div>')


def render_exercises(csrf: str) -> str:
    with connect() as conn, conn.cursor() as cur:
        d = load(cur)
        cur.execute("SELECT s.exercise_id, max(w.day) AS day, count(DISTINCT w.day) AS n FROM workout_sets s JOIN workouts w ON w.id = s.workout_id GROUP BY 1")
        use = {r["exercise_id"]: r for r in cur.fetchall()}
    rows = []
    for x in d["exercises"]:
        u = use.get(x["id"])
        meta = " · ".join(v for v in (gym.KINDS[x["kind"]], f'also: {", ".join(x["words"])}' if x["words"] else "",
                                      f'{u["n"]} workout{"" if u["n"] == 1 else "s"}, last {u["day"]:%d.%m.}' if u else "not done yet") if v)
        btn = (f'<form method="post" action="/web/gym/active">{hidden("csrf", csrf)}{hidden("exercise", str(x["id"]))}{hidden("on", "0" if x["active"] else "1")}'
               f'{here("/web/gym/exercises")}<button class="ghost sm" type="submit">{"Retire" if x["active"] else "Bring back"}</button></form>')
        rows.append(f'<div class="hm{"" if x["active"] else " retired"}"><span class="hm-main">{esc(x["name"])}<span class="small">{esc(meta)}</span></span>{btn}</div>')
    n = sum(1 for x in d["exercises"] if x["active"])
    body = "".join(rows) or '<p class="empty">No exercises yet.</p>'
    return (_tabs("exercises") + f'<div class="card"><h2><span>Your exercises</span><span class="small">{n} active</span></h2>{body}'
            f'{_new(csrf, d, "/web/gym/exercises", open_=not rows)}</div>')


def today_card() -> str:
    """The Gym card on Today: the last workout, the last 14 days as squares, this week and last week."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        cur.execute("SELECT DISTINCT day FROM workouts WHERE day BETWEEN %s AND %s", (today - timedelta(days=20), today))
        days = {r["day"] for r in cur.fetchall()}
        cur.execute("SELECT max(day) AS d FROM workouts")
        last = cur.fetchone()["d"]
    monday = today - timedelta(days=today.weekday())
    this_w = sum(1 for x in days if x >= monday)
    last_w = sum(1 for x in days if monday - timedelta(days=7) <= x < monday)
    two = [today - timedelta(days=13 - i) for i in range(14)]
    sq = "".join(f'<span class="gd{" on" if x in days else ""}{" today" if x == today else ""}"></span>' for x in two)
    n14 = sum(x in days for x in two)
    say = lambda n: "none yet" if n == 0 else f"{n} workout{'' if n == 1 else 's'}"
    pill = f'Last: {last:%a %d.%m.}' if last else "No workouts yet"
    return (f'<div class="card gymcard"><h2><span>{icon("gym")}Gym</span><span class="pill">{pill}</span></h2>'
            f'<div class="gdays" role="img" aria-label="Last 14 days: {n14} workout{"" if n14 == 1 else "s"}">{sq}</div>'
            f'<p class="small">Last week {say(last_w)} · this week {say(this_w)}</p>'
            '<a class="primary gopen" href="/web/gym">Open today’s workout</a></div>')
