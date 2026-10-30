"""The web app's Insights page (31.10.2026): plain statements from the owner's own numbers of the last 90 days, each with a
small chart: food close to bedtime and the next morning's fasting sugar, short nights and the next day's weight, the carb
share of a day and the next morning's fasting sugar. "Late" counts from the bed time, not the clock, because the owner's
day often starts in the afternoon. A statement shows once both groups have enough days; it is a pattern in the numbers,
not proof of a cause."""
from datetime import timedelta
from statistics import mean, median

from .db import connect
from .webentry import esc

DAYS = 90
MIN_EACH = 4  # days needed in each group
LATE_H = 3  # last food less than this many hours before bed
SHORT_H = 6  # a short night: less than this many hours asleep


def load() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        start = today - timedelta(days=DAYS)

        def daily(name: str) -> dict:
            cur.execute("""SELECT m.day, avg(m.value1) AS v FROM measurements m JOIN log_types t ON t.id = m.type_id
                           WHERE t.name = %s AND m.day >= %s GROUP BY m.day""", (name, start))
            return {r["day"]: float(r["v"]) for r in cur.fetchall()}

        sugar, weight = daily("blood_sugar"), daily("weight")
        cur.execute("""SELECT e.day, min(e.event_at) AS at FROM events e JOIN log_types t ON t.id = e.type_id
                       WHERE t.name = 'wake' AND e.day >= %s GROUP BY e.day""", (start,))
        wake = {r["day"]: r["at"] for r in cur.fetchall()}
        # a bed time belongs to the night before the day it ends in (noon to noon, as on the Health page)
        cur.execute("""SELECT ((e.event_at AT TIME ZONE 'Europe/Berlin') + interval '12 hours')::date AS d, max(e.event_at) AS at
                       FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = 'bed' AND e.event_at >= %s GROUP BY 1""",
                    (start - timedelta(days=1),))
        bed = {r["d"]: r["at"] for r in cur.fetchall()}
        cur.execute("SELECT day, max(eaten_at) FILTER (WHERE time_known) AS last FROM food_entries WHERE day >= %s GROUP BY day", (start,))
        last = {r["day"]: r["last"] for r in cur.fetchall() if r["last"]}
        cur.execute("SELECT day, protein, carbs, fat FROM v_food_day WHERE meal IS NULL AND day >= %s AND day < %s", (start, today))
        macros = {r["day"]: (float(r["protein"] or 0), float(r["carbs"] or 0), float(r["fat"] or 0)) for r in cur.fetchall()}
    return {"today": today, "sugar": sugar, "weight": weight, "wake": wake, "bed": bed, "last": last, "macros": macros}


def insights(d: dict) -> list[dict]:
    """Each insight: its title, the two groups' values (True = the group the statement is about) and how to say it."""
    one = timedelta(days=1)
    late = []
    for day, last in d["last"].items():
        bed, s = d["bed"].get(day + one), d["sugar"].get(day + one)
        if bed and s is not None and bed > last:
            gap = (bed - last).total_seconds() / 3600
            if gap < 16:
                late.append((gap < LATE_H, s))
    short = []
    for day, up in d["wake"].items():
        bed, w, w0 = d["bed"].get(day), d["weight"].get(day), d["weight"].get(day - one)
        if bed and w is not None and w0 is not None:
            hours = (up - bed).total_seconds() / 3600
            if 2 <= hours <= 14:
                short.append((hours < SHORT_H, w - w0))
    shares = []
    for day, (p, c, f) in d["macros"].items():
        s, energy = d["sugar"].get(day + one), p * 4 + c * 4 + f * 9
        if s is not None and energy > 0:
            shares.append((c * 4 / energy * 100, s))
    cut = median(x for x, _ in shares) if shares else 0
    return [
        {"key": "late", "title": "Food close to bedtime and fasting sugar", "pairs": late, "unit": "mg/dL", "digits": 0, "sign": False,
         "yes": f"Last food under {LATE_H} h before bed", "no": f"{LATE_H} h or more before bed",
         "say": "On the {ny} days your last food was less than " + str(LATE_H) + " hours before bed, fasting sugar the next morning "
                "averaged {my}, against {mn} after the {nn} days with a longer gap.",
         "needs": "days with a bed time, the time of the last food and a fasting sugar the next morning"},
        {"key": "sleep", "title": "Short nights and weight", "pairs": short, "unit": "kg", "digits": 1, "sign": True,
         "yes": f"Under {SHORT_H} h of sleep", "no": f"{SHORT_H} h or more",
         "say": "After the {ny} nights with less than " + str(SHORT_H) + " hours of sleep, weight changed by {my} from the day before on "
                "average, against {mn} after the {nn} longer nights.",
         "needs": "nights with a bed and wake-up time and a weight that day and the day before"},
        {"key": "carbs", "title": "Carb share and fasting sugar", "pairs": [(x > cut, s) for x, s in shares], "unit": "mg/dL", "digits": 0,
         "sign": False, "yes": f"Carbs above {cut:.0f} % of calories", "no": f"{cut:.0f} % or less",
         "say": "After the {ny} days with more of the calories from carbs (above " + f"{cut:.0f}" + " %), fasting sugar the next "
                "morning averaged {my}, against {mn} after the {nn} days with less.",
         "needs": "days with food logged and a fasting sugar the next morning"},
    ]


def _fmt(v: float, digits: int, sign: bool, unit: str) -> str:
    return f"{v:+.{digits}f} {unit}" if sign else f"{v:.{digits}f} {unit}"


def _bars(rows: list[tuple[str, float, int, str]], digits: int, sign: bool, unit: str) -> str:
    """One bar per group, from zero (zero in the middle when a value is below it), the value written beside it."""
    vals = [v for _, v, _, _ in rows]
    lo, hi = min(0.0, *vals), max(0.0, *vals)
    span = (hi - lo) or 1.0
    zero = (0 - lo) / span * 100
    out = []
    for label, v, n, cls in rows:
        x = (v - lo) / span * 100
        out.append(f'<div class="ib"><span class="ib-l">{esc(label)} <span class="small">· {n} days</span></span>'
                   f'<svg class="ibar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true"><rect class="track" width="100" height="8" rx="4"/>'
                   f'<rect class="fill {cls}" x="{min(zero, x):.1f}" width="{max(abs(x - zero), 0.8):.1f}" height="8" rx="4"/></svg>'
                   f'<span class="ib-v num">{_fmt(v, digits, sign, unit)}</span></div>')
    return "".join(out)


def card(i: dict) -> str:
    yes = [v for g, v in i["pairs"] if g]
    no = [v for g, v in i["pairs"] if not g]
    if len(yes) < MIN_EACH or len(no) < MIN_EACH:
        return (f'<div class="card insight wait"><h2>{esc(i["title"])}</h2>'
                f'<p class="small">Not enough days yet: {len(yes)} for "{esc(i["yes"].lower())}" and {len(no)} for "{esc(i["no"].lower())}"; '
                f'each needs {MIN_EACH}. It counts {esc(i["needs"])}.</p></div>')
    my, mn = mean(yes), mean(no)
    fmt = lambda v: _fmt(v, i["digits"], i["sign"], i["unit"])  # noqa: E731
    diff = my - mn
    said = i["say"].format(ny=len(yes), nn=len(no), my=f"<b>{fmt(my)}</b>", mn=f"<b>{fmt(mn)}</b>")
    return (f'<div class="card insight"><h2>{esc(i["title"])}<span class="dp">{diff:+.{i["digits"]}f} {i["unit"]}</span></h2>'
            f'<p class="isay">{said}</p>{_bars([(i["yes"], my, len(yes), "a"), (i["no"], mn, len(no), "b")], i["digits"], i["sign"], i["unit"])}</div>')


def render(csrf: str) -> str:
    d = load()
    cards = "".join(card(i) for i in insights(d))
    return (f'<div class="head"><h1>Insights</h1><span class="small">From the last {DAYS} days</span></div>'
            f'<div class="grid2 insights">{cards}</div>'
            '<p class="small hint">These are patterns in your own numbers, not proof that one thing causes the other: other things change '
            'on the same days. They get steadier with more days. "Late" counts from your bed time, not the clock.</p>')
