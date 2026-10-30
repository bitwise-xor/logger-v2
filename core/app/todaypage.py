"""The web app's Today page: food with its four macros against the day's targets, usual meals with editable weights,
habit toggles, the Health card (edited in place) and items to review. web.py wraps it in the page and the sign-in."""
import math
from datetime import date, datetime, timedelta

from . import healthpage, rules, uiprefs
from .db import connect
from .webentry import esc, hidden, icon, key

REVIEW_LIMIT = 20
MACROS = (("kcal", "Calories", "kcal"), ("protein", "Protein", "g"), ("fat", "Fat", "g"), ("carbs", "Carbs", "g"))
THOUGHT_TYPES = ("idea", "question", "task", "project", "note", "journal", "reading", "progress")
AREAS = ("health", "projects", "finance", "immigration", "learning", "personal")
# The Health card's rows: key, label, sub-label, log type, period, unit
HEALTH_ROWS = (("bed", "Bed", "night before", "bed", None, ""), ("wake", "Woke up", "", "wake", None, ""), ("weight", "Weight", "", "weight", None, "kg"),
               ("sugar", "Blood sugar", "fasted", "blood_sugar", "fasted", "mg/dL"),
               ("bp_morning", "Blood pressure", "morning", "bp", "morning", "mmHg"), ("bp_evening", "Blood pressure", "evening", "bp", "evening", "mmHg"))


def _n(x, digits: int = 0) -> str:
    if x is None:
        return "–"
    v = float(x)
    return f"{v:,.{digits}f}" if digits else f"{round(v):,}"


def _plain(x) -> str:
    """A number as an input field's value: no thousands separator, no trailing zeros."""
    return "" if x is None else format(float(x), "g")


def _hm(ts) -> str:
    return ts.astimezone(rules.BERLIN).strftime("%H:%M")


def pick_day(raw=None) -> tuple:
    """The day to show (today unless an earlier day is asked for) and today's logging day."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
    try:
        asked = date.fromisoformat(str(raw))
    except ValueError:
        return today, today
    return min(asked, today), today


def load(asked: date | None = None) -> dict:
    """Everything the page shows for a logging day: today, or the earlier day passed in."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        day = min(asked, today) if asked else today
        cur.execute("""SELECT meal, meal_label, kcal, protein, fat, carbs, target_kcal, target_protein, target_fat, target_carbs
                       FROM v_food_day WHERE day = %s ORDER BY meal IS NULL, meal = 0, meal""", (day,))
        food = cur.fetchall()
        cur.execute("""SELECT id, meal, time, food, amount, unit, portion_text, kcal, protein, fat, carbs FROM v_food_log
                       WHERE day = %s ORDER BY meal, time NULLS LAST, id""", (day,))
        foods = cur.fetchall()
        cur.execute("""SELECT u.id AS meal_id, u.name AS meal, i.id, i.grams, f.name, f.basis, f.kcal, f.protein, f.fat, f.carbs
                       FROM usual_meals u JOIN usual_meal_items i ON i.meal_id = u.id JOIN foods f ON f.id = i.food_id
                       ORDER BY u.name, i.sort_order, i.id""")
        usual: dict = {}
        for r in cur.fetchall():
            usual.setdefault((r["meal_id"], r["meal"]), []).append(r)
        cur.execute("SELECT type_name, label, done, target, complete FROM checklist_status(%s)", (day,))
        habits = cur.fetchall()
        cur.execute("""SELECT DISTINCT ON (t.name) t.name, e.id FROM events e JOIN log_types t ON t.id = e.type_id
                       WHERE e.day = %s ORDER BY t.name, e.event_at DESC, e.id DESC""", (day,))
        last_tick = {r["name"]: r["id"] for r in cur.fetchall()}
        cur.execute("""SELECT m.id, t.name, m.value1, m.value2, m.period, m.event_at, m.time_known FROM measurements m
                       JOIN log_types t ON t.id = m.type_id WHERE m.day = %s AND t.name IN ('weight','bp','blood_sugar')
                       ORDER BY m.event_at, m.id""", (day,))
        health: dict = {}
        for m in cur.fetchall():  # the latest of each row wins
            k = {"weight": "weight", "blood_sugar": "sugar"}.get(m["name"]) or ("bp_evening" if m["period"] == "evening" else "bp_morning")
            health[k] = {"id": f"m{m['id']}", "v": [m["value1"], m["value2"]], "at": _hm(m["event_at"]) if m["time_known"] else ""}
        cur.execute("""SELECT e.id, e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                       WHERE t.name = 'wake' AND e.day = %s ORDER BY e.event_at DESC LIMIT 1""", (day,))
        w = cur.fetchone()
        if w:
            health["wake"] = {"id": f"e{w['id']}", "v": [_hm(w["event_at"])], "at": ""}
        lo, hi = healthpage.bed_window(day)  # the bed time of the night before (noon of the day before to noon of today)
        cur.execute("""SELECT e.id, e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                       WHERE t.name = 'bed' AND e.event_at >= %s AND e.event_at < %s ORDER BY e.event_at DESC, e.id DESC LIMIT 1""", (lo, hi))
        b = cur.fetchone()
        if b:
            health["bed"] = {"id": f"e{b['id']}", "v": [_hm(b["event_at"])], "at": ""}
        cur.execute("""SELECT * FROM (
                         SELECT 't' || t.id AS ref, 'thought' AS kind, t.summary AS what, (t.logged_at AT TIME ZONE 'Europe/Berlin')::date AS day,
                                t.review_reason AS reason, t.logged_at AS at, t.thought_type AS a, t.area AS b, NULL::numeric AS v1, NULL::numeric AS v2, NULL AS unit
                           FROM thoughts t WHERE t.needs_review
                         UNION ALL SELECT 'f' || e.id, 'food', f.name, e.day, e.review_reason, e.logged_at, NULL, NULL, e.amount, NULL, f.basis
                           FROM food_entries e JOIN foods f ON f.id = e.food_id WHERE e.needs_review
                         UNION ALL SELECT 'm' || m.id, 'measurement', lt.label, m.day, m.review_reason, m.logged_at, lt.name, NULL, m.value1, m.value2, lt.unit
                           FROM measurements m JOIN log_types lt ON lt.id = m.type_id WHERE m.needs_review
                         UNION ALL SELECT 'e' || e.id, 'event', lt.label, e.day, e.review_reason, e.logged_at, to_char(e.event_at AT TIME ZONE 'Europe/Berlin', 'HH24:MI'), NULL, NULL, NULL, NULL
                           FROM events e JOIN log_types lt ON lt.id = e.type_id WHERE e.needs_review
                       ) x ORDER BY at DESC LIMIT %s""", (REVIEW_LIMIT,))
        review = cur.fetchall()
        prefs = uiprefs.load(cur)
    return {"day": day, "food": food, "foods": foods, "usual": usual, "habits": habits, "last_tick": last_tick,
            "health": health, "review": review, "prefs": prefs, "today": today, "past": day < today}


# ---- food -----------------------------------------------------------------------------------------

def _bar(value, target, k: str = "") -> str:
    """A thin bar in the macro's colour; past the target it turns amber (not for protein, a goal to reach)."""
    pct = min(100.0, float(value or 0) / float(target) * 100) if target else 0
    over = " over" if target and k != "protein" and float(value or 0) > float(target) else ""
    return (f'<svg class="meter" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden="true">'
            f'<rect class="track" width="100" height="4" rx="2"/><rect class="fill {k}{over}" width="{pct:.1f}" height="4" rx="2"/></svg>')


def _left(value, target, unit: str, k: str) -> str:
    """Under a tile: what is left of the target, or how far over it (protein is a goal to reach, the others a limit)."""
    if not target:
        return ""
    gap = float(target) - float(value or 0)
    amount = f'{_n(abs(gap))}{" g" if unit == "g" else " kcal"}'
    if gap >= 0:
        return f'<div class="left small num">{amount} {"to go" if k == "protein" else "left"}</div>'
    if k == "protein":
        return '<div class="left small num met">target reached</div>'
    return f'<div class="left small num over">{amount} over</div>'


def _head() -> str:
    return ('<thead><tr><th>Food</th><th>kcal</th><th><span class="long">Protein g</span><span class="short">P g</span></th>'
            '<th><span class="long">Fat g</span><span class="short">F g</span></th>'
            '<th><span class="long">Carbs g</span><span class="short">C g</span></th></tr></thead>')


def _cols(r: dict) -> str:
    return "".join(f"<td>{_n(r[k])}</td>" for k, _, _ in MACROS)


def _food_section(d: dict, csrf: str) -> str:
    total = next((r for r in d["food"] if r["meal"] is None), None)
    tiles = "".join(
        f'<div class="total {k}"><div class="lbl">{lbl}</div><span class="n num">{_n(total[k] if total else 0)}{" g" if unit == "g" else ""}</span>'
        + (f' <span class="of num">/ {_n(total["target_" + k])} {unit}</span>' if total and total["target_" + k] else
           (f' <span class="of">{unit}</span>' if unit == "kcal" else ""))
        + (_bar(total[k], total["target_" + k], k) + _left(total[k], total["target_" + k], unit, k) if total and total["target_" + k] else "")
        + "</div>"
        for k, lbl, unit in MACROS)
    items: dict = {}
    for f in d["foods"]:
        amount = f["portion_text"] or f"{_n(f['amount'], 0)} {f['unit']}"
        items.setdefault(f["meal"], []).append(f)
        f["shown"] = amount
    groups = []
    for r in d["food"]:
        if r["meal"] is None:
            continue
        rows = items.get(r["meal"], [])
        times = [f["time"] for f in rows if f["time"]]
        when = f' <span class="small">{min(times).strftime("%H:%M")}</span>' if times and r["meal"] != 0 else ""
        edit = lambda f: f'/web/food?day={d["day"].isoformat()}&amp;edit=f{f["id"]}#f{f["id"]}'  # opens the entry on the Food log to change it
        lines = "".join(f'<tr class="item"><td><a href="{edit(f)}" title="Change or delete">{esc(f["food"])}</a> '
                        f'<span class="small">{esc(f["shown"])}</span></td>{_cols(f)}</tr>' for f in rows)
        groups.append(f'<tbody><tr class="mealrow"><td>{esc(r["meal_label"])}{when}</td>{_cols(r)}</tr>{lines}</tbody>')
    table = (f'<div class="tablewrap"><table class="ft">{_head()}{"".join(groups)}</table></div>' if groups
             else f'<p class="empty">Nothing logged {"on this day" if d["past"] else "yet today"}.</p>')
    usual = "" if d["past"] else _usual_section(d, csrf)  # meals are logged for today only
    return (f'<div class="totals">{tiles}</div>{_macro_block(total)}'
            f'<div class="card"><h2>Food and drinks<a href="/web/food?day={d["day"].isoformat()}">Open food log</a></h2>{table}{usual}</div>')


def _usual_section(d: dict, csrf: str) -> str:
    prefs = d["prefs"]
    meals = [kv for kv in d["usual"].items() if uiprefs.shown("usual", kv[0][0], prefs)]
    meals = uiprefs.ordered(meals, lambda kv: kv[0][0], prefs["order"].get("usual", []))
    if not meals:
        return ""
    now = _hm(datetime.now(rules.BERLIN))
    out = []
    for (mid, name), its in meals:
        rows = "".join(
            f'<tr class="item" data-per100="{float(i["kcal"])},{float(i["protein"])},{float(i["fat"])},{float(i["carbs"])}">'
            f'<td><div class="edrow"><label><input type="checkbox" name="on_{i["id"]}" id="u{mid}-on-{i["id"]}" checked> {esc(i["name"])}</label>'
            f'<span><input type="number" name="g_{i["id"]}" id="u{mid}-g-{i["id"]}" min="0" max="5000" step="any" inputmode="decimal" '
            f'value="{_plain(i["grams"])}" aria-label="{esc(i["name"])} in {i["basis"]}"> {i["basis"]}</span></div></td>'
            + "".join(f'<td class="m">{_n(float(i[k]) * float(i["grams"]) / 100)}</td>' for k, _, _ in MACROS) + "</tr>"
            for i in its)
        tot = [sum(float(i[k]) * float(i["grams"]) / 100 for i in its) for k, _, _ in MACROS]
        out.append(
            f'<details class="usual"><summary>+ {esc(name)}</summary><div class="editor">'
            f'<form class="usual-edit" method="post" action="/web/usual">{hidden("csrf", csrf)}{hidden("key", key())}{hidden("meal", str(mid))}'
            f'<div class="tablewrap"><table class="ft">{_head()}<tbody>{rows}'
            f'<tr class="sum"><td>This meal</td>{"".join(f"<td class=m>{_n(v)}</td>" for v in tot)}</tr></tbody></table></div>'
            f'<div class="foot"><label>Time <input type="time" name="time" id="u{mid}-time" value="{now}"></label>'
            '<button class="primary" type="submit">Log meal</button></div></form></div></details>')
    return f'<div class="usual-row"><span class="small">Repeat:</span>{"".join(out)}</div>'


# ---- habits, health, review ------------------------------------------------------------------------

def _habit_section(d: dict, csrf: str) -> str:
    prefs = d["prefs"]
    habits = uiprefs.ordered([h for h in d["habits"] if uiprefs.shown("habit", h["type_name"], prefs)],
                             lambda h: h["type_name"], prefs["order"].get("habit", []))
    if not habits:
        return ""
    btns = []
    for h in habits:
        done = h["complete"]
        count = f' <span class="cnt">{h["done"]}/{h["target"]}</span>' if h["target"] > 1 else ""
        if d["past"]:
            btns.append(f'<span class="habit static{" done" if done else ""}"><span class="box">{icon("check")}</span>{esc(h["label"])}{count}</span>')
            continue
        act = hidden("action", "untick") + hidden("eid", str(d["last_tick"].get(h["type_name"], ""))) if done else \
            hidden("action", "tick") + hidden("key", key())
        btns.append(f'<form method="post" action="/web/habit">{hidden("csrf", csrf)}{hidden("name", h["type_name"])}{act}'
                    f'<button class="habit{" done" if done else ""}" type="submit" aria-pressed="{str(done).lower()}">'
                    f'<span class="box">{icon("check")}</span>{esc(h["label"])}{count}</button></form>')
    n_done = sum(1 for h in habits if h["complete"])
    hint = "" if d["past"] else '<p class="small hint">Click to tick, click again to untick.</p>'
    return (f'<div class="card"><h2><span>Habits · {n_done} of {len(habits)} done</span><a href="/web/habits">All habits</a></h2>'
            f'<div class="habits">{"".join(btns)}</div>'
            f'{hint}</div>')


def _health_section(d: dict, csrf: str) -> str:
    rows = []
    prefs = d["prefs"]
    shown_rows = uiprefs.ordered([r for r in HEALTH_ROWS if uiprefs.shown("health", r[0], prefs)], lambda r: r[0], prefs["order"].get("health", []))
    if not shown_rows:
        return ""
    for k, label, sub, _t, _p, unit in shown_rows:
        cur = d["health"].get(k)
        if k in ("wake", "bed"):
            v = cur["v"][0] if cur else ""
            now_button = ' <button type="button" class="ghost sm" data-now="h-wake">Now</button>' if k == "wake" else ""
            fields = (f'<input type="time" name="t" id="h-{k}" value="{v}" data-orig="{v}" '
                      f'aria-label="{"Woke up at" if k == "wake" else "Went to bed at, the night before"}">{now_button}')
        elif k.startswith("bp"):
            a, b = (_plain(cur["v"][0]), _plain(cur["v"][1])) if cur else ("", "")
            fields = (f'<input type="number" class="sm" name="v1" id="h-{k}-1" step="1" inputmode="numeric" value="{a}" data-orig="{a}" placeholder="–" aria-label="{label} {sub} systolic">'
                      f'/<input type="number" class="sm" name="v2" id="h-{k}-2" step="1" inputmode="numeric" value="{b}" data-orig="{b}" placeholder="–" aria-label="{label} {sub} diastolic">')
        else:
            a = _plain(cur["v"][0]) if cur else ""
            fields = (f'<input type="number" name="v1" id="h-{k}" step="any" inputmode="decimal" value="{a}" data-orig="{a}" placeholder="–" aria-label="{label}">')
        small = " · ".join(x for x in (sub, unit, cur["at"] if cur else "") if x)
        if d["past"]:
            said = "–" if not cur else cur["v"][0] if k in ("wake", "bed") else "/".join(_plain(v) for v in cur["v"] if v is not None)
            rows.append(f'<div class="hrow ro"><span class="lbl">{label}{f"<span class=small>{esc(small)}</span>" if small else ""}</span>'
                        f'<span class="val">{said}</span><span class="end"></span></div>')
            continue
        rows.append(f'<form class="hrow" method="post" action="/web/health">{hidden("csrf", csrf)}{hidden("row", k)}'
                    f'{hidden("id", cur["id"] if cur else "")}{hidden("key", key())}'
                    f'<span class="lbl">{label}{f"<span class=small>{esc(small)}</span>" if small else ""}</span>'
                    f'<span class="val">{fields}</span><span class="end"><button class="primary save" type="submit">Save</button></span></form>')
    hint = ("Values of this day, shown only." if d["past"] else
            "Type a value and press Enter or Save. An empty field logs a new value; a filled one corrects today's entry.")
    return ('<div class="card"><h2>Health</h2>' + "".join(rows) + f'<p class="small hint">{hint}</p></div>')


def _review_item(r: dict, csrf: str) -> str:
    base = hidden("csrf", csrf) + hidden("ref", r["ref"])
    if r["kind"] == "thought":
        what = f'"{esc(r["what"])}"'
        opts = lambda vals, cur: "".join(f'<option{" selected" if v == cur else ""}>{v}</option>' for v in vals)
        change = (f'<select name="thought_type" aria-label="Type">{opts(THOUGHT_TYPES, r["a"] or "note")}</select>'
                  f'<select name="area" aria-label="Area">{opts(AREAS, r["b"] or "personal")}</select>')
    elif r["kind"] == "food":
        what = f'{esc(r["what"])} {_n(r["v1"])} {r["unit"]}'
        change = f'<input type="number" name="amount" min="0" step="any" value="{_plain(r["v1"])}" aria-label="Amount"> {r["unit"]}'
    elif r["kind"] == "measurement":
        what = f'{esc(r["what"])} {_n(r["v1"], 1)}{"/" + _n(r["v2"]) if r["v2"] is not None else ""} {r["unit"] or ""}'
        change = (f'<input type="number" name="v1" step="any" value="{_plain(r["v1"])}" aria-label="Value">'
                  + (f'/<input type="number" name="v2" step="any" value="{_plain(r["v2"])}" aria-label="Second value">' if r["v2"] is not None else ""))
    else:
        what = f'{esc(r["what"])} {r["a"]}'
        change = f'<input type="time" name="t" value="{r["a"]}" aria-label="Time">'
    reason = f' <span class="small">{esc(r["reason"])}</span>' if r["reason"] else ""
    return (f'<div class="rev"><div class="what"><span class="d">{r["day"].strftime("%d.%m.")}</span><span>{what}{reason}</span></div>'
            f'<div class="row"><form method="post" action="/web/review">{base}{hidden("act", "ok")}<button class="ghost" type="submit">Looks right</button></form>'
            f'<details><summary>Change</summary><form class="edit" method="post" action="/web/review">{base}{hidden("act", "change")}{change}'
            '<button class="primary" type="submit">Save</button></form></details>'
            f'<details class="bad"><summary>Delete</summary><form class="edit" method="post" action="/web/review">{base}{hidden("act", "delete")}'
            'Delete it? <button class="ghost bad" type="submit">Yes, delete</button></form></details></div></div>')


def _review_section(d: dict, csrf: str) -> str:
    if not uiprefs.shown("review", "box", d["prefs"]):  # hidden: only a one-line notice while something waits, so it is never forgotten
        n = len(d["review"])
        return (f'<p class="small notice">{n} item{"" if n == 1 else "s"} to review is hidden here · '
                '<a href="/web/settings?tab=today">show the box</a></p>') if n else ""
    body = ("".join(_review_item(r, csrf) for r in d["review"])
            or f'<div class="allgood"><span class="ok-dot">{icon("check")}</span><p class="empty">Nothing to review.</p></div>')
    return f'<div class="card"><h2>To review</h2>{body}</div>'


# ---- day picker, macro pie ---------------------------------------------------------------------------

MACRO_KCAL = (("protein", "Protein", 4, "p"), ("carbs", "Carbs", 4, "c"), ("fat", "Fat", 9, "f"))  # key, label, kcal per gram, colour class


def _pie(shares: list, label: str, middle: str = "") -> str:
    """A donut as inline SVG from (colour class, fraction) pairs, `middle` (the kcal) in its hole; one full share is a circle."""
    parts = [(c, v) for c, v in shares if v > 0]
    if len(parts) == 1:
        body = f'<circle class="{parts[0][0]}" cx="50" cy="50" r="48"/>'
    else:
        body, a0 = "", -math.pi / 2
        for c, v in parts:
            a1 = a0 + 2 * math.pi * v
            x0, y0, x1, y1 = 50 + 48 * math.cos(a0), 50 + 48 * math.sin(a0), 50 + 48 * math.cos(a1), 50 + 48 * math.sin(a1)
            body += f'<path class="{c}" d="M50 50 L{x0:.2f} {y0:.2f} A48 48 0 {1 if v > 0.5 else 0} 1 {x1:.2f} {y1:.2f} Z"/>'
            a0 = a1
    return (f'<div class="donut"><svg class="pie" viewBox="0 0 100 100" role="img" aria-label="{esc(label)}">{body}'
            f'<circle class="hole" cx="50" cy="50" r="31"/></svg>'
            f'<div class="mid" aria-hidden="true"><b class="num">{middle}</b><span>kcal</span></div></div>')


def _macro_block(total) -> str:
    """Calories from protein, carbs and fat as a pie and a table: the share of each against the share the day's targets ask for."""
    if not total:
        return ""
    kcal = {k: float(total[k] or 0) * m for k, _, m, _ in MACRO_KCAL}
    whole = sum(kcal.values())
    if whole <= 0:
        return ""
    aim = {k: float(total["target_" + k] or 0) * m for k, _, m, _ in MACRO_KCAL}
    aim_whole = sum(aim.values())
    rows, said = [], []
    for k, label, _m, cls in MACRO_KCAL:
        share = kcal[k] / whole * 100
        ideal = aim[k] / aim_whole * 100 if aim_whole else None
        diff = None if ideal is None else share - ideal
        level = "" if diff is None else "ok" if abs(diff) <= 5 else "warn" if abs(diff) <= 10 else "bad"
        gap = "–" if diff is None else f'<span class="dp {level}">{diff:+.1f} pts</span>'
        said.append(f"{label.lower()} {share:.0f} %")
        rows.append(f'<tr><td><span class="sw {cls}"></span>{label}</td><td>{_n(total[k], 1)}</td><td>{_n(kcal[k])}</td><td>{share:.1f} %</td>'
                    f'<td>{"–" if ideal is None else f"{ideal:.1f} %"}</td><td>{gap}</td></tr>')
    head = "<thead><tr><th>Macro</th><th>g</th><th>kcal</th><th>Share</th><th>Ideal</th><th>Diff.</th></tr></thead>"
    foot = f'<tfoot><tr><td>Total</td><td></td><td>{_n(whole)}</td><td>100 %</td><td>{"100 %" if aim_whole else "–"}</td><td></td></tr></tfoot>'
    note = ("The ideal share comes from the day's targets." if aim_whole else "No targets for this day, so no ideal share.")
    pie = _pie([(cls, kcal[k] / whole) for k, _, _, cls in MACRO_KCAL], "Calories from " + ", ".join(said), _n(whole))
    return (f'<div class="card macrobox"><h2>Energy from macros<span class="small">kcal from protein, carbs (4 per g) and fat (9 per g)</span></h2>'
            f'<div class="macros">{pie}<div><div class="tablewrap">'
            f'<table class="ms">{head}<tbody>{"".join(rows)}</tbody>{foot}</table></div><p class="small">{note}</p></div></div></div>')


def _daybar(day: date, today: date) -> str:
    """The day as a title (Today, Yesterday, n days ago above it) with arrows and a date field to go to another day."""
    prev, nxt = day - timedelta(days=1), day + timedelta(days=1)
    back = (today - day).days
    kicker = "Today" if back == 0 else "Yesterday" if back == 1 else f"{back} days ago"
    year = "" if day.year == today.year else f" {day.year}"
    after = (f'<a class="iconbtn" href="/web/?day={nxt.isoformat()}" aria-label="Next day">{icon("right")}</a>' if day < today
             else f'<span class="iconbtn off">{icon("right")}</span>')
    return (f'<div class="daybar"><div><div class="kicker">{kicker}</div><h1>{day:%A}, {day.day} {day:%B}{year}</h1></div>'
            '<form class="row" method="get" action="/web/">'
            f'<a class="iconbtn" href="/web/?day={prev.isoformat()}" aria-label="Previous day">{icon("left")}</a>'
            f'<input type="date" name="day" value="{day.isoformat()}" max="{today.isoformat()}" aria-label="Day" class="autosubmit">'
            '<button class="ghost sm nojs" type="submit">Show</button>'
            + after + ("" if day == today else '<a class="ghost" href="/web/">Today</a>') + '</form></div>')


def render(csrf: str, asked: date | None = None) -> str:
    d = load(asked)
    note = (f'<p class="notice earlier">{icon("clock")}An earlier day: habits and health values are only shown here. '
            'Click a food to change it on the Food page.</p>' if d["past"] else "")
    review = "" if d["past"] else _review_section(d, csrf)
    return (f'{_daybar(d["day"], d["today"])}{note}<div class="grid"><section>{_food_section(d, csrf)}</section>'
            f'<aside>{_habit_section(d, csrf)}{_health_section(d, csrf)}{review}'
            '<p class="small"><a href="/web/settings?tab=today">Customize this page</a></p></aside></div>')


def day_title(day: date) -> str:
    return day.strftime("%a %d.%m.%Y")
