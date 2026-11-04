"""Coaching (web item 52, 04.11.2026; plan and the owner's answers in the doc "Logger v2: status and web app plan", tab Current;
mockup https://claude.ai/artifact/4z4HLimQeLi2jo4Z6ZX3ND): a day coaching on Today and a week review on the Week page, by Claude
Opus at high effort, only when the owner presses the button. The coach reads that day's or week's food, health values, sleep,
habits, gym workouts and home chores, the days before it, the targets and the owner's own goals (Settings > Coach); never thoughts,
private or gratitude entries. A coaching runs in a background thread (20 to 60 s), so a closed page does not stop it; the row is
"working" until it is "done" (body = the coach's sections) or "failed". Every coaching is kept; at most DAILY_LIMIT a logging day.
Its cost is an audit_log row of the kind "coaching" (Usage page), without any text."""
import json
import os
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from psycopg.types.json import Jsonb

from . import gym, habitaim, logbook
from .ai.provider import AnthropicProvider, Provider
from .db import connect
from .when import BERLIN

MODEL = "claude-opus-5-5"  # the owner's choice (02.11.2026), at high effort
EFFORT = "high"
TIMEOUT = 240.0
MAX_TOKENS = 16000
DAILY_LIMIT = 10  # coachings per logging day: a stuck button cannot run up costs
STALE_MINUTES = 10  # a "working" row older than this was cut off (a restart): it is marked failed
CONTEXT_DAYS = 7  # the days before, for context
TOP_FOODS = 40
GOALS_KEY = "coach"  # ui_settings: {"goals": "...", "style": "...", "hidden": ["day:next_meal", ...]}
GOALS_MAX = 2000
SYSTEM = (Path(__file__).parent / "ai" / "prompts" / "coach.md").read_text(encoding="utf-8")
PROVIDER: Optional[Provider] = None  # created on first use; tests put a fake here
BACKGROUND = True  # tests run the coaching in the request instead
VITALS = ("weight", "bp", "blood_sugar", "mood", "energy", "waist", "hips", "chest", "upper_arm", "thigh")


def provider() -> Provider:
    global PROVIDER
    if PROVIDER is None:
        PROVIDER = AnthropicProvider(os.environ["ANTHROPIC_API_KEY"], effort=EFFORT, timeout=TIMEOUT, max_retries=1)
    return PROVIDER


# ---- the answer's form -----------------------------------------------------------------------------

def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_TEXT = {"type": "string"}
_LIST = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "day": _obj({"went_well": _LIST, "improve": _LIST,
                 "next_meal": {"anyOf": [_obj({"title": _TEXT, "why": _TEXT, "kcal": {"type": "number"}, "protein": {"type": "number"},
                                               "carbs": {"type": "number"}, "fat": {"type": "number"}}), {"type": "null"}]},
                 "gaps": _LIST, "doctor": _TEXT}),
    "week": _obj({"targets": _LIST, "trends": _LIST, "habits": _LIST, "best_day": _TEXT, "hardest_day": _TEXT, "focus": _LIST, "doctor": _TEXT}),
}


# ---- the owner's settings (Settings > Coach): goals, how to coach, which parts ---------------------------------

# The parts of each kind of coaching, in the order shown; a part switched off is neither asked for nor shown (04.11.2026)
PARTS = {"day": (("went_well", "What went well"), ("improve", "To improve"), ("next_meal", "Next meal"), ("gaps", "Missing in the log"),
                 ("doctor", "Doctor note")),
         "week": (("targets", "Against your targets"), ("trends", "Trends"), ("habits", "Habits, gym and home"),
                  ("days", "Best and hardest day"), ("focus", "Focus for next week"), ("doctor", "Doctor note"))}
FIELDS = {"days": ("best_day", "hardest_day")}  # a part that is more than one field of the answer


def prefs(cur) -> dict:
    """{"goals": text, "style": text, "hidden": {"day:next_meal", ...}} (ui_settings key "coach")."""
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (GOALS_KEY,))
    r = cur.fetchone()
    v = r["value"] if r else {}
    return {"goals": str(v.get("goals") or ""), "style": str(v.get("style") or ""), "hidden": {str(x) for x in v.get("hidden") or []}}


def goals(cur) -> str:
    return prefs(cur)["goals"]


def _clean(v) -> str:
    return "\n".join(" ".join(line.split()) for line in str(v or "").splitlines()).strip()[:GOALS_MAX]


def goals_set(form, key=None, session=None) -> dict:
    """Settings > Coach: goals, how to coach and the parts shown. A form without the parts' switches (parts=1 missing) keeps them."""
    new = {"goals": _clean(form.get("goals"))}
    if "style" in form:
        new["style"] = _clean(form.get("style"))
    if form.get("parts") == "1":
        hidden = [f"{kind}:{k}" for kind, parts in PARTS.items() for k, _ in parts if form.get(f"show_{kind}_{k}") != "1"]
        for kind, parts in PARTS.items():
            if all(f"{kind}:{k}" in hidden for k, _ in parts):
                return {"outcome": "rejected", "reason": f"Keep at least one part of the {'day coaching' if kind == 'day' else 'week review'}."}
        new["hidden"] = hidden
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = ui_settings.value || EXCLUDED.value, updated_at = now()""", (GOALS_KEY, Jsonb(new)))
        logbook._audit(cur, "web_change", "changed", {"what": "coach settings"}, logbook.WEB)
    return {"outcome": "changed", "head": "Saved: the coach reads your settings every time."}


def shown(kind: str, hidden: set) -> list:
    """The parts of this kind that are switched on, in order: [(key, label)]."""
    return [(k, lbl) for k, lbl in PARTS[kind] if f"{kind}:{k}" not in hidden]


def schema(kind: str, hidden: set) -> dict:
    """The answer's form with only the parts switched on."""
    keep = {f for k, _ in shown(kind, hidden) for f in FIELDS.get(k, (k,))}
    props = {k: v for k, v in SCHEMA[kind]["properties"].items() if k in keep}
    return _obj(props)


# ---- what the coach reads ---------------------------------------------------------------------------------

def _n(v, digits: int = 0):
    if v is None:
        return None
    x = round(float(v), digits)
    return int(x) if digits == 0 else x


def _hm(ts) -> Optional[str]:
    return ts.astimezone(BERLIN).strftime("%H:%M") if ts else None


def _vitals(cur, start: date, end: date) -> dict:
    """{day: ["weight 82.4 kg 07:40", "bp 128/82 morning", ...]} for the vital signs, mood, energy and body measurements."""
    cur.execute("""SELECT day, type, period, value1, value2, unit, event_at, time_known FROM v_measurements
                   WHERE day BETWEEN %s AND %s AND type = ANY(%s) ORDER BY day, event_at, id""", (start, end, list(VITALS)))
    out: dict = {}
    for r in cur.fetchall():
        v = f'{_n(r["value1"], 1):g}' + (f'/{_n(r["value2"], 1):g}' if r["value2"] is not None else "")
        bits = [r["type"], v, r["unit"] or "", r["period"] or "", _hm(r["event_at"]) if r["time_known"] else ""]
        out.setdefault(r["day"], []).append(" ".join(b for b in bits if b))
    return out


def _sleep(cur, start: date, end: date) -> dict:
    cur.execute("SELECT wake_day, bed_at, wake_at, hours FROM v_sleep WHERE wake_day BETWEEN %s AND %s", (start, end))
    return {r["wake_day"]: {"bed": _hm(r["bed_at"]), "woke": _hm(r["wake_at"]), "hours": _n(r["hours"], 1)} for r in cur.fetchall()}


def _habits(cur, day: date) -> list:
    avoid = habitaim.avoid(cur)
    cur.execute("SELECT type_name, label, done, target FROM checklist_status(%s)", (day,))
    return [f'{r["label"]} {r["done"]}/{r["target"]}' + (" (a habit to avoid: 0 is the aim)" if r["type_name"] in avoid else "")
            for r in cur.fetchall()]


def _gym(cur, start: date, end: date) -> dict:
    cur.execute("""SELECT w.day, x.name, x.kind, s.sets, s.reps, s.kg, s.minutes, s.km FROM workout_sets s JOIN workouts w ON w.id = s.workout_id
                   JOIN exercises x ON x.id = s.exercise_id WHERE w.day BETWEEN %s AND %s ORDER BY w.day, s.sort_order, s.id""", (start, end))
    out: dict = {}
    for r in cur.fetchall():
        out.setdefault(r["day"], []).append(f'{r["name"]} {gym.row_text(r)}'.strip())
    return out


def _home(cur, start: date, end: date) -> dict:
    cur.execute("""SELECT d.day, c.name AS chore, z.name AS zone FROM home_done d JOIN home_chores c ON c.id = d.chore_id
                   JOIN home_zones z ON z.id = d.zone_id WHERE d.day BETWEEN %s AND %s AND d.source <> 'start' ORDER BY d.done_at""", (start, end))
    out: dict = {}
    for r in cur.fetchall():
        out.setdefault(r["day"], []).append(f'{r["chore"]} · {r["zone"]}')
    return out


def _totals(cur, start: date, end: date) -> dict:
    cur.execute("""SELECT day, kcal, protein, fat, carbs, target_kcal, target_protein, target_fat, target_carbs FROM v_food_day
                   WHERE meal IS NULL AND day BETWEEN %s AND %s""", (start, end))
    return {r["day"]: r for r in cur.fetchall()}


def _short_day(day: date, tot, vit: dict, sleep: dict, gymd: dict, homed: dict) -> dict:
    """One day in a few numbers (the days before a day coaching, and every day of a week review)."""
    return {"day": f"{day:%a %d.%m.}", "kcal": _n(tot and tot["kcal"]), "protein_g": _n(tot and tot["protein"]), "carbs_g": _n(tot and tot["carbs"]),
            "fat_g": _n(tot and tot["fat"]), "values": vit.get(day, []), "sleep": sleep.get(day), "gym": gymd.get(day, []), "home": homed.get(day, [])}


def _targets(cur, day: date) -> Optional[dict]:
    """The food targets valid on that day."""
    cur.execute("SELECT kcal, protein, fat, carbs FROM food_targets WHERE valid_from <= %s ORDER BY valid_from DESC LIMIT 1", (day,))
    t = cur.fetchone()
    return {"kcal": _n(t["kcal"]), "protein_g": _n(t["protein"]), "fat_g": _n(t["fat"]), "carbs_g": _n(t["carbs"])} if t else None


def _top_foods(cur, end: date) -> list:
    """The foods eaten most in the 60 days up to `end`, with their values per 100 g (or ml) and the usual amount."""
    cur.execute("""SELECT f.name, f.basis, f.kcal, f.protein, f.fat, f.carbs, count(*) AS n,
                          percentile_disc(0.5) WITHIN GROUP (ORDER BY e.amount) AS usual
                   FROM food_entries e JOIN foods f ON f.id = e.food_id
                   WHERE e.day BETWEEN %s AND %s GROUP BY f.id ORDER BY n DESC, f.name LIMIT %s""", (end - timedelta(days=60), end, TOP_FOODS))
    return [f'{r["name"]}: per 100 {r["basis"]} {_n(r["kcal"])} kcal, {_n(r["protein"], 1):g} g protein, {_n(r["carbs"], 1):g} g carbs, '
            f'{_n(r["fat"], 1):g} g fat; usually {_n(r["usual"])} {r["basis"]} ({r["n"]} times)' for r in cur.fetchall()]


def context(cur, kind: str, start: date, end: date) -> dict:
    """Everything the coach reads, as one JSON-ready dict (no thoughts, no private or gratitude entries)."""
    cur.execute("SELECT log_day(now()) AS d, now() AS t")
    r = cur.fetchone()
    today, now = r["d"], r["t"]
    before = start - timedelta(days=CONTEXT_DAYS)
    tot, vit, sleep = _totals(cur, before, end), _vitals(cur, before, end), _sleep(cur, before, end)
    gymd, homed = _gym(cur, before, end), _home(cur, before, end)
    days_before = [_short_day(before + timedelta(days=i), tot.get(before + timedelta(days=i)), vit, sleep, gymd, homed)
                   for i in range(CONTEXT_DAYS)]
    p = prefs(cur)
    out = {"kind": kind, "goals": p["goals"] or "(none written)", "how_to_coach": p["style"] or "(none written)",
           "parts_wanted": [lbl for _, lbl in shown(kind, p["hidden"])], "targets": _targets(cur, end), "days_before": days_before}
    if kind == "day":
        cur.execute("""SELECT f.time, f.meal, f.food, f.amount, f.unit, f.portion_text, f.kcal, f.protein, f.fat, f.carbs, f.is_drink, d.meal_label
                       FROM v_food_log f LEFT JOIN v_food_day d ON d.day = f.day AND d.meal = f.meal
                       WHERE f.day = %s ORDER BY f.meal, f.time NULLS LAST, f.id""", (start,))
        food = [{"meal": r["meal_label"] or ("drink" if r["is_drink"] else ""), "time": r["time"].strftime("%H:%M") if r["time"] else None,
                 "food": r["food"], "amount": r["portion_text"] or f'{_n(r["amount"])} {r["unit"]}', "kcal": _n(r["kcal"]),
                 "protein_g": _n(r["protein"], 1), "carbs_g": _n(r["carbs"], 1), "fat_g": _n(r["fat"], 1)} for r in cur.fetchall()]
        t = tot.get(start)
        out["day"] = {**_short_day(start, t, vit, sleep, gymd, homed), "date": start.isoformat(), "food": food, "habits": _habits(cur, start),
                      "not_over_yet": start == today, "time_now": now.astimezone(BERLIN).strftime("%H:%M") if start == today else None}
        out["frequent_foods"] = _top_foods(cur, start)
    else:
        n = (end - start).days + 1
        cur.execute("""SELECT f.day, max(f.time) AS last_food FROM v_food_log f WHERE f.day BETWEEN %s AND %s AND NOT f.is_drink GROUP BY f.day""", (start, end))
        last = {r["day"]: r["last_food"] for r in cur.fetchall()}
        week = []
        for i in range(n):
            d = start + timedelta(days=i)
            row = _short_day(d, tot.get(d), vit, sleep, gymd, homed)
            row["habits"] = _habits(cur, d)
            row["last_food"] = last[d].strftime("%H:%M") if last.get(d) else None
            week.append(row)
        cur.execute("""SELECT f.name, count(*) AS n, round(sum(e.kcal)) AS kcal FROM food_entries e JOIN foods f ON f.id = e.food_id
                       WHERE e.day BETWEEN %s AND %s GROUP BY f.name ORDER BY kcal DESC LIMIT 10""", (start, end))
        out["week"] = {"from": start.isoformat(), "to": end.isoformat(), "days": week,
                       "foods_with_most_kcal": [f'{r["name"]}: {_n(r["kcal"])} kcal in {r["n"]} entries' for r in cur.fetchall()]}
        cur.execute("""SELECT start_day, body FROM coachings WHERE kind = 'day' AND status = 'done' AND start_day BETWEEN %s AND %s
                       ORDER BY start_day, id""", (start, end))
        out["day_coachings_this_week"] = [{"day": f'{r["start_day"]:%a %d.%m.}', **r["body"]} for r in cur.fetchall()]
    return out


# ---- running one -----------------------------------------------------------------------------------------

def _rejected(reason: str) -> dict:
    return {"outcome": "rejected", "reason": reason}


def _day(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def start(form, key=None, session=None) -> dict:
    """"Coach me" / "Review this week": a new coaching for the day or the period, read in the background."""
    kind = str(form.get("kind") or "")
    a, b = _day(form.get("start")), _day(form.get("end") or form.get("start"))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        if kind not in SCHEMA or not a or not b or b < a or b > today or (kind == "day" and a != b) or (b - a).days > 31:
            return _rejected("That is not a day or week to coach.")
        cur.execute(f"""UPDATE coachings SET status = 'failed', error = 'cut off', finished_at = now()
                        WHERE status = 'working' AND created_at < now() - interval '{STALE_MINUTES} minutes'""")
        cur.execute("SELECT 1 FROM coachings WHERE kind = %s AND start_day = %s AND end_day = %s AND status = 'working'", (kind, a, b))
        if cur.fetchone():
            return {"outcome": "changed", "head": "The coach is already on it."}
        cur.execute("SELECT count(*) AS n FROM coachings WHERE log_day(created_at) = %s", (today,))
        if cur.fetchone()["n"] >= DAILY_LIMIT:
            return _rejected(f"That is {DAILY_LIMIT} coachings today, the daily limit. Tomorrow there are new ones.")
        cur.execute("INSERT INTO coachings (kind, start_day, end_day) VALUES (%s, %s, %s) RETURNING id", (kind, a, b))
        cid = cur.fetchone()["id"]
    if BACKGROUND:
        threading.Thread(target=run, args=(cid,), name=f"coach-{cid}", daemon=True).start()
    else:
        run(cid)
    return {"outcome": "changed", "head": "The coach is reading your day: about 30 seconds." if kind == "day" else "The coach is reading your week: about 45 seconds."}


def run(cid: int) -> None:
    """Read the data (a short transaction), ask the AI with no connection open, save the answer and its cost."""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM coachings WHERE id = %s", (cid,))
            row = cur.fetchone()
            ctx = context(cur, row["kind"], row["start_day"], row["end_day"])
            form = schema(row["kind"], prefs(cur)["hidden"])
        user = ("Coach this day." if row["kind"] == "day" else "Review this week.") + " The data:\n" + json.dumps(ctx, ensure_ascii=False, default=str)
        body, usage = provider().json(SYSTEM, user, form, MODEL, MAX_TOKENS)
        with connect() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE coachings SET status = 'done', body = %s, model = %s, usd = %s, finished_at = now() WHERE id = %s""",
                        (Jsonb(body), usage.model, round(usage.usd, 5), cid))
            logbook._audit(cur, "coaching", "done", {"id": cid, "kind": row["kind"], "ai": logbook._ai([usage])}, logbook.WEB)
    except Exception as e:  # noqa: BLE001 - any failure is shown on the card as "did not work", never the data
        kind = type(e).__name__
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE coachings SET status = 'failed', error = %s, finished_at = now() WHERE id = %s", (kind[:60], cid))
                logbook._audit(cur, "coaching", "failed", {"id": cid, "error": kind}, logbook.WEB)
        except Exception:  # noqa: BLE001 - the database is gone too: the row turns "cut off" after STALE_MINUTES
            pass


def latest(cur, kind: str, a: date, b: date) -> Optional[dict]:
    cur.execute(f"""SELECT *, status = 'working' AND created_at < now() - interval '{STALE_MINUTES} minutes' AS stale FROM coachings
                    WHERE kind = %s AND start_day = %s AND end_day = %s ORDER BY id DESC LIMIT 1""", (kind, a, b))
    return cur.fetchone()


def status(cid: int) -> str:
    """working, done, failed or gone; a "working" row older than STALE_MINUTES was cut off (a restart) and is "failed", so the
    waiting card stops asking (until 04.11.2026 it kept spinning)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT status, status = 'working' AND created_at < now() - interval '{STALE_MINUTES} minutes' AS stale
                        FROM coachings WHERE id = %s""", (cid,))
        r = cur.fetchone()
    return "gone" if not r else "failed" if r["stale"] else r["status"]


ACTIONS = {"start": start}
