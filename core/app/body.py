"""Body measurements (03.11.2026, T1 of docs/specs/body-measurements-spec.md; the owner's answers in the doc "Logger v2: status
and web app plan", Current tab): five tape measurements in cm, about every 28 days. Stored as measurements like weight; all
values of one health day (local time minus 5 h) are one session. This module reads them for the Health card, the Today line
and the Week page, and holds the rules the saving code (v1_log) asks: a jump of 4 cm or more since last time is read back.
Settings live in ui_settings ("body": interval and hidden measures; "body_skip": the day "Not today" was pressed) and the
height in body_height (dated, so a later change does not rewrite past ratios)."""
from datetime import date, timedelta
from typing import Optional

# key, label, landmark (shown as a hint in the form); the order of the form, the table and the chips
TAPE = (("waist", "Waist", "At the navel, after a normal breath out"), ("hips", "Hips", "Widest point of the buttocks"),
        ("chest", "Chest", "Across the nipples, arms down"), ("upper_arm", "Upper arm", "Halfway shoulder to elbow, relaxed, right side"),
        ("thigh", "Thigh", "Halfway hip to knee, standing, right side"))
NAMES = tuple(k for k, _l, _h in TAPE)
LABEL = {k: lbl for k, lbl, _h in TAPE}
JUMP_CM = 4
INTERVAL_DAYS = 28
INTERVAL_RANGE = (7, 90)
HEIGHT_RANGE = (120, 230)
KEY, SKIP_KEY = "body", "body_skip"
PROTOCOL = "Morning, before eating, relaxed, tape level and snug. Right side for arm and thigh. Leave a field empty to skip it."


def cm(v) -> str:
    return f"{float(v):.1f}"


def signed(d: float) -> str:
    """A change as "+1.5", "−2.0" or "±0" (no colour: a change is not judged, spec section 6)."""
    if abs(d) < 0.05:
        return "±0"
    return ("+" if d > 0 else "−") + f"{abs(d):.1f}"


def settings(cur) -> dict:
    """{"interval": days, "hidden": set of measure keys}; the defaults when nothing was chosen."""
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (KEY,))
    row = cur.fetchone()
    v = row["value"] if row and isinstance(row["value"], dict) else {}
    n = v.get("interval")
    interval = n if isinstance(n, int) and INTERVAL_RANGE[0] <= n <= INTERVAL_RANGE[1] else INTERVAL_DAYS
    return {"interval": interval, "hidden": {str(x) for x in v.get("hidden", []) if x in NAMES}}


def shown(prefs: dict) -> list[tuple]:
    return [m for m in TAPE if m[0] not in prefs["hidden"]]


def sessions(cur) -> list[dict]:
    """Every session, oldest first: {"day", "vals": {key: {"id": "m12", "v": float}}}; a measure's latest value of the day wins."""
    cur.execute("""SELECT DISTINCT ON (t.name, m.day) m.id, t.name, m.day, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id
                   WHERE t.name = ANY(%s) ORDER BY t.name, m.day, m.event_at DESC, m.id DESC""", (list(NAMES),))
    by_day: dict = {}
    for r in cur.fetchall():
        by_day.setdefault(r["day"], {})[r["name"]] = {"id": f"m{r['id']}", "v": float(r["value1"])}
    return [{"day": d, "vals": by_day[d]} for d in sorted(by_day)]


def day_values(cur, day: date) -> dict:
    return next((s["vals"] for s in sessions(cur) if s["day"] == day), {})


def last_day(cur) -> Optional[date]:
    cur.execute("""SELECT max(m.day) AS d FROM measurements m JOIN log_types t ON t.id = m.type_id WHERE t.name = ANY(%s)""", (list(NAMES),))
    return cur.fetchone()["d"]


def last_before(cur, name: str, day: date) -> Optional[tuple[date, float]]:
    cur.execute("""SELECT m.day, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id
                   WHERE t.name = %s AND m.day < %s ORDER BY m.day DESC, m.event_at DESC, m.id DESC LIMIT 1""", (name, day))
    r = cur.fetchone()
    return (r["day"], float(r["value1"])) if r else None


def jump(cur, name: str, value: float, day: date) -> list[str]:
    """The read-back for a jump of JUMP_CM or more since the measure's last value before this day (spec section 8)."""
    prev = last_before(cur, name, day)
    if prev and abs(value - prev[1]) >= JUMP_CM - 1e-9:
        return [f"{LABEL[name]} {cm(value)} cm is {abs(value - prev[1]):.1f} cm away from last time ({cm(prev[1])}). Right?"]
    return []


def heights(cur) -> list[dict]:
    cur.execute("SELECT valid_from, height_cm FROM body_height ORDER BY valid_from DESC")
    return cur.fetchall()


def height_on(cur, day: date) -> Optional[float]:
    cur.execute("SELECT height_cm FROM body_height WHERE valid_from <= %s ORDER BY valid_from DESC LIMIT 1", (day,))
    r = cur.fetchone()
    return float(r["height_cm"]) if r else None


def skipped(cur, today: date) -> bool:
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (SKIP_KEY,))
    r = cur.fetchone()
    return bool(r and isinstance(r["value"], dict) and r["value"].get("day") == today.isoformat())


def due(cur, today: date, woke: bool) -> Optional[dict]:
    """Today's "Body measurements due" line (spec section 7): {"last": date or None, "days": n or None}, or None when not due,
    before the wake-up is logged, or after "Not today" on this health day."""
    if not woke or skipped(cur, today):
        return None
    last = last_day(cur)
    if last is None:
        return {"last": None, "days": None}
    n = (today - last).days
    return {"last": last, "days": n} if n >= settings(cur)["interval"] else None


def weight_rate(cur, today: date) -> Optional[tuple[float, float]]:
    """(kg per week, % of body weight per week): the 7-day average weight now against the one 28 days earlier, over 4 weeks.
    None without a weighing in both weeks."""
    cur.execute("""SELECT DISTINCT ON (m.day) m.day, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id
                   WHERE t.name = 'weight' AND m.day BETWEEN %s AND %s ORDER BY m.day, m.event_at DESC, m.id DESC""",
                (today - timedelta(days=34), today))
    w = {r["day"]: float(r["value1"]) for r in cur.fetchall()}
    now = [v for d, v in w.items() if d > today - timedelta(days=7)]
    then = [v for d, v in w.items() if today - timedelta(days=35) < d <= today - timedelta(days=28)]
    if not now or not then:
        return None
    a, b = sum(now) / len(now), sum(then) / len(then)
    rate = (a - b) / 4
    return rate, rate / a * 100
