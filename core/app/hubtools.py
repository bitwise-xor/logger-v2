"""The hub's tools (13.11.2026): what the home dashboard reads and taps over MCP with its service token (auth.hub_access).
today_summary is the Today page's own data (todaypage.load) as plain JSON: numbers and the names of habits, foods and
chores, never private, gratitude or thought text. The actions call the web app's own (webactions, webfood), so the rules,
the audit rows and the channel ("web") are the web app's: a tap on the hub is a tap on Today. mcp_app.py wraps each as a tool."""
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from . import held, home, rules, todaypage, uiprefs, webactions, webentry, webfood, webquestions
from .config import APP_HOST
from .db import connect

BASE = f"https://{APP_HOST}/web"
TODAY = BASE + "/"
USUAL_DAYS, USUAL_MIN = 30, 5  # a habit's usual hour: from the ticks of the last 30 days, 5 or more of them
HISTORY_DAYS = 7
SHOWN = 5  # review items and chores listed; the counts are of all of them
WATER_ML = (50, 2000)


def _num(x):
    """A plain JSON number from a database value (Decimal): whole numbers without ".0"."""
    if x is None:
        return None
    v = float(x)
    return int(v) if v == int(v) else v


# ---- today_summary -----------------------------------------------------------------------------------

def _habits(d: dict) -> list:
    """The habits as Today lists them: without the hidden ones, in the chosen order (todaypage._habit_section)."""
    prefs = d["prefs"]
    return uiprefs.ordered([h for h in d["habits"] if uiprefs.shown("habit", h["type_name"], prefs)],
                           lambda h: h["type_name"], prefs["order"].get("habit", []))


def _usual_hours(cur, day: date, names: list) -> dict:
    """name -> the median clock hour (Europe/Berlin, 8.5 = 08:30) of its ticks in the last 30 logging days. Ticks without a time
    (a past day ticked on the Habits page) are left out; a habit with fewer than 5 is not in the result."""
    cur.execute("""SELECT t.name, e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = ANY(%s) AND e.time_known AND e.day > %s AND e.day <= %s""",
                (names, day - timedelta(days=USUAL_DAYS), day))
    hours: dict = {}
    for r in cur.fetchall():
        at = r["event_at"].astimezone(rules.BERLIN)
        hours.setdefault(r["name"], []).append(at.hour + at.minute / 60)
    return {name: round(statistics.median(h), 2) for name, h in hours.items() if len(h) >= USUAL_MIN}


def _history(cur, day: date, habits: list, avoid: set) -> dict:
    """The last 7 logging days of the same habits: done on a day as the database's checklist counts it; for a habit to avoid, a
    day without it (what the Habits page's calendar counts as a good day)."""
    days = [day - timedelta(days=i) for i in range(HISTORY_DAYS - 1, -1, -1)]
    cur.execute("""SELECT g.day::date AS day, s.type_name, s.done, s.complete
                   FROM generate_series(%s::date, %s::date, interval '1 day') AS g(day), LATERAL checklist_status(g.day::date) s""",
                (days[0], day))
    got = {(r["type_name"], r["day"]): r for r in cur.fetchall()}

    def good(name: str, x: date) -> bool:
        r = got.get((name, x))
        return bool(r) and (r["done"] == 0 if name in avoid else bool(r["complete"]))

    return {"days": [x.isoformat() for x in days],
            "rows": [{"name": h["type_name"], "label": h["label"], "done": [good(h["type_name"], x) for x in days]} for h in habits]}


def _food(d: dict, cur) -> dict:
    total = next((r for r in d["food"] if r["meal"] is None), None)
    if total:
        targets = {k: total["target_" + k] for k, _l, _u in todaypage.MACROS}
    else:  # nothing eaten yet: the day has no total row to bring the targets (v_food_day), so they are read here
        cur.execute("SELECT kcal, protein, fat, carbs FROM food_targets WHERE valid_from <= %s ORDER BY valid_from DESC LIMIT 1", (d["day"],))
        targets = cur.fetchone() or {}
    return {**{k: _num(total[k]) if total else 0 for k, _l, _u in todaypage.MACROS},
            "targets": {k: _num(targets.get(k)) for k, _l, _u in todaypage.MACROS}}


def _health(d: dict) -> dict:
    h = d["health"]

    def one(k):
        return _num(h[k]["v"][0]) if k in h else None

    def pair(k):
        return [_num(h[k]["v"][0]), _num(h[k]["v"][1])] if k in h else None

    w = h.get("wake")
    # 12 hours after the wake-up the page empties the field for the next sleep and keeps the time as "last 07:10" (todaypage._next_sleep)
    wake = (w["v"][0] or w["at"].removeprefix("last ") or None) if w else None
    return {"weight_kg": one("weight"), "bp_morning": pair("bp_morning"), "bp_evening": pair("bp_evening"), "blood_sugar": one("sugar"),
            "wake": wake, "mood": d["checkin"].get("mood"), "energy": d["checkin"].get("energy")}


def _what(r: dict) -> str:
    """A review item's short label as Today shows it (todaypage._review_item). A thought is named by its type only: its words
    never leave through this tool."""
    n = todaypage._n
    if r["kind"] == "thought":
        return f'Thought ({r["a"]})' if r["a"] else "Thought"
    if r["kind"] == "food":
        return f'{r["what"]} {n(r["v1"])} {r["unit"]}'
    if r["kind"] == "measurement":
        return f'{r["what"]} {n(r["v1"], 1)}{"/" + n(r["v2"]) if r["v2"] is not None else ""} {r["unit"] or ""}'.strip()
    return f'{r["what"]} {r["a"]}'


def _chores(cur) -> dict:
    """The Home page's Now list ("Longest since last done", homepage.render_now): one row per chore, the longest waiting first,
    without those marked "Not today". The Home page counts nothing as overdue (home.py), so neither does this."""
    d = home.load(cur)
    rows = home.worst(home.visible(d["pairs"], home.skipped(cur, d["today"])))
    return {"count": len(rows),
            "items": [{"name": p["chore"]["name"], "zone": p["zone"]["name"], "link": f'{BASE}/home/chore?id={p["chore"]["id"]}'}
                      for p in rows[:SHOWN]],
            "link": BASE + "/home"}


def today_summary() -> dict:
    d = todaypage.load()
    day, avoid = d["day"], d["avoid"]
    habits = _habits(d)
    with connect() as conn, conn.cursor() as cur:
        food = _food(d, cur)
        usual = _usual_hours(cur, day, [h["type_name"] for h in habits])
        history = _history(cur, day, habits, avoid)
        chores = _chores(cur)
    water = d["water"] or {}
    return {
        "day": day.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "links": {"today": TODAY, "habits": BASE + "/habits", "health": BASE + "/health", "food": BASE + "/food", "home": BASE + "/home"},
        "food": food,
        "water": {"ml": _num(water.get("drunk")) or 0, "target_ml": _num(water.get("target"))},
        "habits": [{"name": h["type_name"], "label": h["label"], "done": h["done"], "target": h["target"], "complete": bool(h["complete"]),
                    "avoid": h["type_name"] in avoid, "usual_hour": usual.get(h["type_name"]),
                    "link": f'{BASE}/habits#hab-{quote(h["type_name"])}'} for h in habits],
        "habit_history": history,
        "health": _health(d),
        "review": {"count": len(d["review"]),
                   "items": [{"ref": r["ref"], "kind": r["kind"], "what": _what(r), "reason": r["reason"] or "", "link": TODAY}
                             for r in d["review"][:SHOWN]]},
        # count: the open questions (the number on the Today link); not_read: messages kept but not read yet, shown above them
        "questions": {"count": webquestions.count(), "not_read": len(held.rows()), "link": TODAY},
        "chores": chores,
    }


# ---- actions -------------------------------------------------------------------------------------------

def _reply(out: dict, **more) -> dict:
    """A web action's result as a tool's reply: the outcome, the head the web app's reply box would show (webentry.reply_html),
    the reason when there is one, and the link to Today. Nothing else of the result is passed on (no receipt lines, no notes)."""
    head = webentry._HEAD["replayed"] if out.get("replayed") else out.get("head") or webentry._HEAD.get(out["outcome"], out["outcome"])
    return {"outcome": out["outcome"], "head": head, **{k: out[k] for k in ("reason", "replayed") if out.get(k)},
            "link": TODAY, **more}


def _habit(name: str) -> Optional[dict]:
    """The habit as it stands now on today's checklist; None for a name that is not on it."""
    with connect() as c:
        r = c.execute("SELECT type_name AS name, done, target, complete FROM checklist_status(log_day(now())) WHERE type_name = %s",
                      (name,)).fetchone()
    return dict(r) if r else None


def tick_habit(name: str, key: Optional[str] = None) -> dict:
    return _reply(webactions.habit(name, "tick", "", key), habit=_habit(name))


def untick_habit(name: str) -> dict:
    with connect() as c:  # today's last tick of it, the one Today's button sends (todaypage.load, last_tick); 0 = none
        r = c.execute("""SELECT e.id FROM events e JOIN log_types t ON t.id = e.type_id
                         WHERE t.name = %s AND e.day = log_day(now()) ORDER BY e.event_at DESC, e.id DESC LIMIT 1""", (name,)).fetchone()
    return _reply(webactions.habit(name, "untick", str(r["id"] if r else 0), None), habit=_habit(name))


def add_water(ml: int = todaypage.WATER_GLASS, key: Optional[str] = None) -> dict:
    lo, hi = WATER_ML
    if not lo <= ml <= hi:
        return _reply(webactions._rejected(f"Give the water in ml, from {lo} to {hi}."))
    return _reply(webfood.water({}, key, ml))


def set_checkin(mood: Optional[int] = None, energy: Optional[int] = None, key: Optional[str] = None) -> dict:
    """Mood, energy or both for today, each saved as one tap on Today's check-in card (webactions.checkin, which has the range)."""
    asked = [(kind, v) for kind, v in (("mood", mood), ("energy", energy)) if v is not None]
    if not asked:
        return _reply(webactions._rejected("Give mood, energy or both."))
    top = {kind: len(words) for kind, _label, words in todaypage.CHECKIN}
    # a value outside the card's range goes first, so its refusal comes before the other value is saved
    asked.sort(key=lambda kv: 1 <= kv[1] <= top[kv[0]])
    out = {}
    for kind, v in asked:
        out = webactions.checkin({"type": kind, "value": v}, f"{key}:{kind}" if key else None)  # one key per value: each is its own message
        if out["outcome"] != "saved":
            break
    return _reply(out)
