"""The web app's food trends (decided 29.10.2026): calories, protein and fat per day over 7, 30 or 90 days against the targets, with
the 7-day average. Only complete days count: today is left out because it is not over yet. The charts are drawn by app.js from
the numbers in each chart's data-chart attribute. The same charts are used by the Week page."""
import json
from datetime import date, timedelta

from .db import connect
from .webentry import esc

RANGES = (7, 30, 90)
DEFAULT_RANGE = 30
NUTRIENTS = (("kcal", "Calories", " kcal", "kcal"), ("protein", "Protein", " g", "g"), ("fat", "Fat", " g", "g"))


def _dm(d: date) -> str:
    return d.strftime("%a %d.%m.")


def day_totals(cur, start: date, end: date) -> dict:
    """{day: row of v_food_day's total line} for the days that have food."""
    cur.execute("""SELECT day, kcal, protein, fat, carbs, target_kcal, target_protein, target_fat, target_carbs
                   FROM v_food_day WHERE meal IS NULL AND day BETWEEN %s AND %s AND kcal IS NOT NULL ORDER BY day""", (start, end))
    return {r["day"]: r for r in cur.fetchall()}


def _chart(cid: str, start: date, days: int, series: list, **opts) -> str:
    data = {"start": start.isoformat(), "days": days, "series": series, **opts}
    return f'<div class="chart" id="{cid}" data-chart="{esc(json.dumps(data, separators=(",", ":")))}"></div>'


def cards(totals: dict, start: date, end: date) -> str:
    """One card per nutrient: daily bars, the 7-day average, the target line, a line of numbers."""
    days = (end - start).days + 1
    have = sorted(totals)
    out = []
    for key, label, unit, short in NUTRIENTS:
        vals = {d: float(totals[d][key]) for d in have}
        pts = [[(d - start).days, round(v, 1), f"{_dm(d)}: {v:,.0f}{unit}"] for d, v in vals.items()]
        avg7 = []
        for d in have:
            window = [vals[x] for x in have if d - timedelta(days=6) <= x <= d]
            avg7.append([(d - start).days, round(sum(window) / len(window), 1)])
        target = next((float(totals[d]["target_" + key]) for d in reversed(have) if totals[d]["target_" + key]), None)
        series = [{"mark": "bar", "cls": "a", "pts": pts}, {"mark": "line", "cls": "b", "pts": avg7}]
        chart = _chart(f"c-trend-{key}", start, days, series, label=f"{label} per day", min=0, unit="" if key == "kcal" else unit,  # "2,000 kcal" does not fit the axis
                       **({"lines": [[target, "t", f"{target:,.0f}"]]} if target else {}))
        if vals:
            mean = sum(vals.values()) / len(vals)
            if key == "protein":
                hit = sum(1 for d in have if totals[d]["target_protein"] and vals[d] >= float(totals[d]["target_protein"]))
                note = f' · target reached on <b>{hit}</b> of {len(have)} days' if target else ""
            else:
                over = sum(1 for d in have if totals[d]["target_" + key] and vals[d] > float(totals[d]["target_" + key]))
                note = f' · over the target on <b>{over}</b> of {len(have)} days' if target else ""
            stat = f'average <b>{mean:,.0f}{unit}</b>' + (f" · target {target:,.0f}" if target else "") + note
        else:
            stat = "no complete days with food in this range"
        legend = ('<i class="key sq a"></i>day <i class="key line b"></i>7-day average'
                  + ('<i class="key ref t"></i>target' if target else ""))
        out.append(f'<div class="card"><h2>{label} <span class="small">per day</span></h2><p class="stat">{stat}</p>{chart}<p class="legend">{legend}</p></div>')
    return "".join(out)


def render(csrf: str, q) -> str:
    r = str(q.get("r", ""))
    days = int(r) if r in {str(x) for x in RANGES} else DEFAULT_RANGE
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        end = today - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        body = cards(day_totals(cur, start, end), start, end)
    seg = "".join(f'<a href="/web/food/trends?r={x}"{" aria-current=\"page\"" if x == days else ""}>{x} days</a>' for x in RANGES)
    return (f'<div class="head sub"><h2>Last {days} complete days <span class="small">· {start:%d.%m.} to {end:%d.%m.}, today not counted</span></h2>'
            f'<nav class="seg" aria-label="Range">{seg}</nav></div><div class="grid2">{body}</div>')
