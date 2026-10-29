"""The web app's Week page (decided 29.10.2026): the Sheet's weekly summary on the web, with fat next to calories and protein.
Only complete days count (today is left out). Periods: the last 7 days (the default), last week, the last 30 days, this week so far.
Food against the targets, how far from the targets, weight, blood pressure, fasting sugar, sleep, habits, the days, the foods that
count most, and the daily charts of calories, protein and fat."""
from datetime import date, timedelta

from . import trendspage
from .db import connect
from .foodpage import _pct_class
from .todaypage import _n
from .webentry import esc

PERIODS = (("7", "Last 7 days"), ("last", "Last week"), ("30", "Last 30 days"), ("this", "This week so far"))
MACROS = (("kcal", "Calories", "kcal"), ("protein", "Protein", "g"), ("fat", "Fat", "g"), ("carbs", "Carbs", "g"))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def bounds(p: str, today: date) -> tuple[date, date]:
    yesterday = today - timedelta(days=1)
    if p == "last":
        return _monday(today) - timedelta(days=7), _monday(today) - timedelta(days=1)
    if p == "this":
        return _monday(today), yesterday
    return yesterday - timedelta(days=29 if p == "30" else 6), yesterday


def _avg(xs) -> float | None:
    xs = [float(x) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _bars(totals: dict) -> str:
    rows = []
    days = sorted(totals)
    for key, label, unit in MACROS:
        target = next((float(totals[d]["target_" + key]) for d in reversed(days) if totals[d]["target_" + key]), None)
        mean = _avg(totals[d][key] for d in days)
        if mean is None or not target:
            rows.append(f'<tr><td>{label}</td><td class="small" colspan="2">{"no target set" if mean is not None else "no complete days"}</td></tr>')
            continue
        pct = mean / target * 100
        cls = _pct_class(key, mean, target)
        rows.append(f'<tr><td>{label}</td><td class="bar"><svg class="meter" viewBox="0 0 150 4" preserveAspectRatio="none" aria-hidden="true">'
                    f'<rect class="track" width="150" height="4" rx="2"/><rect class="fill {"over" if cls in ("warn", "bad") and key != "protein" else ""}" '
                    f'width="{min(150, pct):.1f}" height="4" rx="2"/><rect class="goal" x="100" width="1" height="4"/></svg></td>'
                    f'<td class="{cls}">{pct:.0f}%</td></tr>')
    return ('<table class="ft plain far"><tbody>' + "".join(rows) + "</tbody></table>"
            '<p class="small hint">Average day as a share of the target; the thin line is 100%. Calories, fat and carbs are green up to the target, '
            'protein from the target on.</p>')


def _cards(totals: dict) -> str:
    days = sorted(totals)
    out = []
    for key, label, unit in MACROS:
        mean = _avg(totals[d][key] for d in days)
        target = next((float(totals[d]["target_" + key]) for d in reversed(days) if totals[d]["target_" + key]), None)
        out.append(f'<div class="total"><div class="lbl">{label} per day</div><span class="n num">{_n(mean) if mean is not None else "–"}'
                   f'{"" if unit == "kcal" else " g"}</span>' + (f' <span class="of num">/ {_n(target)} {unit}</span>' if target else "") + "</div>")
    return f'<div class="totals">{"".join(out)}</div>'


def _health(cur, start: date, end: date, ndays: int) -> str:
    cur.execute("""SELECT t.name, m.period, m.day, m.value1, m.value2 FROM measurements m JOIN log_types t ON t.id = m.type_id
                   WHERE t.name IN ('weight','bp','blood_sugar') AND m.day BETWEEN %s AND %s ORDER BY m.event_at, m.id""", (start, end))
    rows = cur.fetchall()
    weights = {}
    for r in rows:
        if r["name"] == "weight":
            weights[r["day"]] = float(r["value1"])
    bp = {p: [r for r in rows if r["name"] == "bp" and r["period"] == p] for p in ("morning", "evening")}
    sugar = [r["value1"] for r in rows if r["name"] == "blood_sugar"]
    cur.execute("SELECT hours FROM v_sleep WHERE wake_day BETWEEN %s AND %s AND hours > 0 AND hours < 24", (start, end))
    sleep = _avg(r["hours"] for r in cur.fetchall())
    cur.execute("""SELECT t.name, t.daily_target, e.day, count(*)::int AS n FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.on_checklist AND t.active AND t.name <> 'wake' AND e.day BETWEEN %s AND %s GROUP BY 1, 2, 3""", (start, end))
    done = cur.fetchall()
    cur.execute("SELECT count(*) AS n FROM log_types WHERE on_checklist AND active AND kind = 'event' AND name <> 'wake'")
    habits = cur.fetchone()["n"]
    full = sum(1 for r in done if r["n"] >= (r["daily_target"] or 1))
    items = []
    if weights:
        ds = sorted(weights)
        change = weights[ds[-1]] - weights[ds[0]]
        items.append(("Weight", f'{weights[ds[-1]]:.1f} kg <span class="small">({"+" if change > 0 else "−" if change < 0 else "±"}{abs(change):.1f} kg since {ds[0]:%d.%m.})</span>'))
    for p in ("morning", "evening"):
        if bp[p]:
            items.append((f"Blood pressure {p}", f'{_avg(r["value1"] for r in bp[p]):.0f}/{_avg(r["value2"] for r in bp[p]):.0f} <span class="small">average of {len(bp[p])}</span>'))
    if sugar:
        items.append(("Fasting sugar", f'{_avg(sugar):.0f} mg/dL <span class="small">average of {len(sugar)}</span>'))
    if sleep is not None:
        items.append(("Sleep", f'{int(sleep)} h {round((sleep % 1) * 60):02d} <span class="small">average night</span>'))
    if habits and ndays:
        items.append(("Habits", f'{full / (habits * ndays) * 100:.0f}% <span class="small">of habit-days done ({full} of {habits * ndays})</span>'))
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in items) or '<tr><td class="empty">Nothing logged in this period.</td></tr>'
    return f'<table class="ft plain"><tbody>{body}</tbody></table>'


def _days(cur, totals: dict, start: date, end: date) -> str:
    cur.execute("""SELECT m.day, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id
                   WHERE t.name = 'weight' AND m.day BETWEEN %s AND %s ORDER BY m.event_at, m.id""", (start, end))
    weights = {r["day"]: float(r["value1"]) for r in cur.fetchall()}
    rows = []
    for d in sorted(totals, reverse=True):
        t = totals[d]
        rows.append(f'<tr><td>{d:%a %d.%m.}</td>' + "".join(f"<td>{_n(t[k])}</td>" for k, _l, _u in MACROS)
                    + f'<td>{f"{weights[d]:.1f}" if d in weights else "–"}</td></tr>')
    return ('<div class="tablewrap"><table class="ft"><thead><tr><th>Day</th><th>kcal</th><th>Protein g</th><th>Fat g</th><th>Carbs g</th><th>kg</th></tr></thead>'
            f'<tbody>{"".join(rows) or "<tr><td colspan=6 class=empty>No complete days with food.</td></tr>"}</tbody></table></div>')


def _foods(cur, start: date, end: date) -> str:
    cur.execute("""SELECT f.name, round(sum(e.kcal)) AS kcal, count(*)::int AS n FROM food_entries e JOIN foods f ON f.id = e.food_id
                   WHERE e.day BETWEEN %s AND %s GROUP BY f.name""", (start, end))
    rows = cur.fetchall()
    by_kcal = sorted(rows, key=lambda r: (-r["kcal"], r["name"]))[:8]
    by_n = sorted(rows, key=lambda r: (-r["n"], r["name"]))[:8]
    li = lambda rs, f: "".join(f"<tr><td>{esc(r['name'])}</td><td>{f(r)}</td></tr>" for r in rs) or "<tr><td class=empty>Nothing.</td></tr>"
    return ('<div class="grid2"><div><h3>Most calories</h3><table class="ft plain"><tbody>' + li(by_kcal, lambda r: f"{_n(r['kcal'])} kcal")
            + '</tbody></table></div><div><h3>Eaten most often</h3><table class="ft plain"><tbody>' + li(by_n, lambda r: f"{r['n']}×")
            + "</tbody></table></div></div>")


def render(csrf: str, q) -> str:
    p = q.get("p") if q.get("p") in dict(PERIODS) else "7"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        start, end = bounds(p, today)
        ndays = max(0, (end - start).days + 1)
        totals = trendspage.day_totals(cur, start, end) if ndays else {}
        seg = "".join(f'<a href="/web/week?p={k}"{" aria-current=\"page\"" if k == p else ""}>{lbl}</a>' for k, lbl in PERIODS)
        head = (f'<div class="head"><h1>Week</h1><nav class="seg" aria-label="Period">{seg}</nav></div>'
                f'<p class="small">{start:%a %d.%m.} to {end:%a %d.%m.%Y} · {len(totals)} of {ndays} days with food · today is left out, it is not over yet</p>' if ndays else
                f'<div class="head"><h1>Week</h1><nav class="seg" aria-label="Period">{seg}</nav></div>'
                '<div class="card"><p class="empty">No complete day in this period yet (this week so far starts after Monday).</p></div>')
        if not ndays:
            return head
        body = (f'<div class="card"><h2>Food</h2>{_cards(totals)}{_bars(totals)}</div>'
                f'<div class="grid2"><div class="card"><h2>Health and habits</h2>{_health(cur, start, end, ndays)}</div>'
                f'<div class="card"><h2>Foods that count most</h2>{_foods(cur, start, end)}</div></div>'
                f'<div class="grid2">{trendspage.cards(totals, start, end)}</div>'
                f'<div class="card"><h2>The days</h2>{_days(cur, totals, start, end)}</div>')
    return head + body
