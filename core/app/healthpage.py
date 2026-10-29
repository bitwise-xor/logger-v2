"""The web app's Health page: weight, blood pressure, fasted sugar and sleep over 7, 30 or 90 days, the checklist
habits of the last 30 days, and every day's values in one row to correct in place (decided 26.10.2026).
The charts are drawn by app.js from the numbers in each chart's data-chart attribute; without the script the
table below still shows every value."""
import json
from datetime import date, datetime, timedelta

from . import rules
from .db import connect
from .webentry import esc, hidden, key

RANGES = (7, 30, 90)
DEFAULT_RANGE = 30
SHOWN_DAYS = 14  # rows before "Show all"
CAL_DAYS = 30
# The table's columns: key, heading, sub-heading, log type, period, number of values ("t" = a time)
COLS = (("w", "Weight", "kg", "weight", None, 1), ("bm", "BP", "morning", "bp", "morning", 2),
        ("be", "BP", "evening", "bp", "evening", 2), ("s", "Sugar", "fasted", "blood_sugar", "fasted", 1),
        ("bed", "Bed", "night before", "bed", None, "t"), ("wake", "Woke up", "", "wake", None, "t"))
NAMES = {"w": "Weight", "bm": "BP morning", "be": "BP evening", "s": "Sugar", "bed": "Bed", "wake": "Woke up"}


def plain(x) -> str:
    """A number as an input field's value: no thousands separator, no trailing zeros."""
    return "" if x is None else format(float(x), "g")


def _hm(ts) -> str:
    return ts.astimezone(rules.BERLIN).strftime("%H:%M")


def bed_window(day: date) -> tuple[datetime, datetime]:
    """The bed time shown on a day's row: the night before, from noon of the day before to noon of the day."""
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=rules.BERLIN)
    return noon - timedelta(days=1), noon


def day_values(cur, first: date, last: date) -> dict:
    """{day: {col: {"id": "m12", "v": [values as text]}}} for each day from first to last; the latest entry wins."""
    out: dict = {}
    cur.execute("""SELECT m.id, t.name, m.period, m.value1, m.value2, m.day FROM measurements m JOIN log_types t ON t.id = m.type_id
                   WHERE m.day BETWEEN %s AND %s AND t.name IN ('weight','bp','blood_sugar') ORDER BY m.event_at, m.id""", (first, last))
    for m in cur.fetchall():
        col = {"weight": "w", "blood_sugar": "s"}.get(m["name"]) or ("be" if m["period"] == "evening" else "bm")
        vals = [plain(m["value1"])] + ([plain(m["value2"])] if m["name"] == "bp" else [])
        out.setdefault(m["day"], {})[col] = {"id": f"m{m['id']}", "v": vals}
    cur.execute("""SELECT e.id, e.day, e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = 'wake' AND e.day BETWEEN %s AND %s ORDER BY e.event_at, e.id""", (first, last))
    for e in cur.fetchall():
        out.setdefault(e["day"], {})["wake"] = {"id": f"e{e['id']}", "v": [_hm(e["event_at"])]}
    lo, hi = bed_window(first)[0], bed_window(last)[1]
    cur.execute("""SELECT e.id, e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                   WHERE t.name = 'bed' AND e.event_at >= %s AND e.event_at < %s ORDER BY e.event_at, e.id""", (lo, hi))
    for e in cur.fetchall():
        local = e["event_at"].astimezone(rules.BERLIN)
        row = local.date() + timedelta(days=1 if local.hour >= 12 else 0)
        out.setdefault(row, {})["bed"] = {"id": f"e{e['id']}", "v": [local.strftime("%H:%M")]}
    return out


def load(days: int) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        start = today - timedelta(days=days - 1)
        vals = day_values(cur, start, today)
        cur.execute("""SELECT m.day, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id
                       WHERE t.name = 'weight' AND m.day BETWEEN %s AND %s ORDER BY m.event_at, m.id""", (start - timedelta(days=6), today))
        weights: dict = {}
        for r in cur.fetchall():  # the day's last weighing, as in the table
            weights[r["day"]] = float(r["value1"])
        cur.execute("SELECT wake_day, bed_at, wake_at, hours FROM v_sleep WHERE wake_day BETWEEN %s AND %s ORDER BY wake_at", (start, today))
        sleep = {r["wake_day"]: r for r in cur.fetchall()}
        cal_start = today - timedelta(days=CAL_DAYS - 1)
        cur.execute("""SELECT name, label, daily_target FROM log_types WHERE on_checklist AND active AND kind = 'event'
                       ORDER BY sort_order, name""")
        habits = cur.fetchall()
        cur.execute("""SELECT t.name, e.day, count(*)::int AS n FROM events e JOIN log_types t ON t.id = e.type_id
                       WHERE t.on_checklist AND e.day BETWEEN %s AND %s GROUP BY 1, 2""", (cal_start, today))
        done = {(r["name"], r["day"]): r["n"] for r in cur.fetchall()}
    return {"today": today, "start": start, "days": days, "vals": vals, "weights": weights, "sleep": sleep,
            "habits": habits, "done": done, "cal_start": cal_start}


def _dm(d: date) -> str:
    return d.strftime("%a %d.%m.")


def _num(v: float) -> str:
    return format(round(v, 1), "g")


def _chart(cid: str, d: dict, series: list, **opts) -> str:
    data = {"start": d["start"].isoformat(), "days": d["days"], "series": series, **opts}
    return f'<div class="chart" id="{cid}" data-chart="{esc(json.dumps(data, separators=(",", ":")))}"></div>'


def _avg(xs: list) -> float:
    return sum(xs) / len(xs)


def _charts(d: dict) -> str:
    start, today, idx = d["start"], d["today"], lambda day: (day - d["start"]).days
    days = [start + timedelta(days=i) for i in range(d["days"])]
    # weight: the day's value and the 7-day average of the days that have one
    w = [(day, d["weights"][day]) for day in days if day in d["weights"]]
    avg7 = [[idx(day), round(_avg([v for k, v in d["weights"].items() if day - timedelta(days=6) <= k <= day]), 2), "7-day average"] for day, _ in w]
    if w:
        delta = w[-1][1] - w[0][1]
        sign = "+" if delta > 0 else "−" if delta < 0 else "±"
        wstat = f'<b>{_num(w[-1][1])} kg</b> last · <b>{sign}{abs(delta):.1f} kg</b> in {d["days"]} days'
    else:
        wstat = "no weighings in this range"
    weight = _chart("c-weight", d, [{"mark": "line", "cls": "b", "pts": avg7},
                                    {"mark": "dot", "cls": "a", "pts": [[idx(day), v, f"{_dm(day)}: {_num(v)} kg"] for day, v in w]}],
                    label="Weight in kg", decimals=1)
    # blood pressure: filled = morning, ring = evening; systolic and diastolic in two colours
    bp = {"bm": [], "be": []}
    for day in days:
        for col in ("bm", "be"):
            c = d["vals"].get(day, {}).get(col)
            if c:
                bp[col].append((day, float(c["v"][0]), float(c["v"][1])))
    tip = lambda day, a, b, per: f"{_dm(day)} {per}: {a:g}/{b:g}"
    series = []
    for col, mark, per in (("bm", "dot", "morning"), ("be", "ring", "evening")):
        series.append({"mark": mark, "cls": "a", "pts": [[idx(day), a, tip(day, a, b, per)] for day, a, b in bp[col]]})
        series.append({"mark": mark, "cls": "b", "pts": [[idx(day), b, tip(day, a, b, per)] for day, a, b in bp[col]]})
    mean = lambda rows: f"{round(_avg([r[1] for r in rows]))}/{round(_avg([r[2] for r in rows]))}" if rows else "–"
    bpstat = f'Morning avg <b>{mean(bp["bm"])}</b> · evening avg <b>{mean(bp["be"])}</b>'
    # dashed lines at the limits decided for the Sheet (27.10.2026): amber from 130/80, red from 140/90
    pressure = _chart("c-bp", d, series, label="Blood pressure in mmHg", lines=[[140, "r", "140"], [130, "w", "130"], [90, "r", "90"], [80, "w", "80"]])
    # fasted sugar
    s = [(day, float(d["vals"][day]["s"]["v"][0])) for day in days if d["vals"].get(day, {}).get("s")]
    sstat = f'avg <b>{round(_avg([v for _, v in s]))}</b> mg/dL · {len(s)} readings' if s else "no readings in this range"
    sugar = _chart("c-sugar", d, [{"mark": "dot", "cls": "a", "pts": [[idx(day), v, f"{_dm(day)}: {v:g} mg/dL"] for day, v in s]}],
                   label="Fasted blood sugar in mg/dL", lines=[[126, "r", "126"], [100, "w", "100"]])
    # sleep: hours from bed to wake-up, on the day of the wake-up
    nights = [(day, d["sleep"][day]) for day in days if day in d["sleep"] and 0 < float(d["sleep"][day]["hours"]) < 24]
    hm = lambda h: f"{int(h)} h {round((h % 1) * 60):02d} min"
    pts = [[idx(day), round(float(r["hours"]), 2), f"Night to {_dm(day)}: bed {_hm(r['bed_at'])}, up {_hm(r['wake_at'])}, {hm(float(r['hours']))}"]
           for day, r in nights]
    slstat = f'avg <b>{hm(_avg([float(r["hours"]) for _, r in nights]))}</b> · {len(nights)} nights' if nights else "no nights with bed and wake-up"
    sleep = _chart("c-sleep", d, [{"mark": "bar", "cls": "a", "pts": pts}], label="Sleep in hours", min=0, unit=" h",
                   lines=[[7, "w", "7 h"], [6, "r", "6 h"]])
    card = lambda title, stat, chart, legend="": (f'<div class="card"><h2>{title}</h2><p class="stat">{stat}</p>{chart}'
                                                 + (f'<p class="legend">{legend}</p>' if legend else "") + "</div>")
    return ('<div class="grid2">'
            + card("Weight", wstat, weight, '<i class="key a"></i>daily <i class="key line b"></i>7-day average')
            + card('Blood pressure <span class="small">mmHg</span>', bpstat, pressure,
                   '<i class="key a"></i>systolic <i class="key b"></i>diastolic · filled = morning, ring = evening · '
                   '<i class="key ref"></i>amber from 130/80 <i class="key ref r"></i>red from 140/90')
            + card('Blood sugar <span class="small">fasted, mg/dL</span>', sstat, sugar,
                   '<i class="key ref"></i>amber from 100 <i class="key ref r"></i>red from 126')
            + card("Sleep", slstat, sleep, 'Hours from bed to wake-up; point at or tap a bar for the times · '
                   '<i class="key ref"></i>amber under 7 h <i class="key ref r"></i>red under 6 h')
            + "</div>")


def _calendar(d: dict) -> str:
    if not d["habits"]:
        return ""
    days = [d["cal_start"] + timedelta(days=i) for i in range(CAL_DAYS)]
    rows = []
    for h in d["habits"]:
        target, cells, full = h["daily_target"] or 1, [], 0
        for day in days:
            n = d["done"].get((h["name"], day), 0)
            full += n >= target
            cls = "d" if n >= target else "h" if n else ""
            cells.append(f'<i class="{cls}" title="{_dm(day)}: {n} of {target}"></i>')
        rows.append(f'<span class="lbl">{esc(h["label"])} <span class="small">{full}/{CAL_DAYS}</span></span><span class="days">{"".join(cells)}</span>')
    return (f'<div class="card"><h2>Habits · last {CAL_DAYS} days</h2><div class="cal">{"".join(rows)}'
            f'<span></span><span class="axis"><span>{d["cal_start"]:%d.%m.}</span><span>today</span></span></div>'
            '<p class="legend"><i class="key sq d"></i>done <i class="key sq h"></i>partly <i class="key sq"></i>not logged</p></div>')


def _field(day: date, col: str, kind, cur, fid: str) -> str:
    v = cur["v"] if cur else ([""] * (2 if kind == 2 else 1))
    name = NAMES[col]
    if kind == "t":
        return f'<input type="time" name="{col}" form="{fid}" value="{v[0]}" data-orig="{v[0]}" aria-label="{name} {day:%d.%m.}">'
    if kind == 2:
        return (f'<input type="number" class="sm" name="{col}1" form="{fid}" step="1" inputmode="numeric" value="{v[0]}" data-orig="{v[0]}" placeholder="–" aria-label="{name} systolic {day:%d.%m.}">'
                f'/<input type="number" class="sm" name="{col}2" form="{fid}" step="1" inputmode="numeric" value="{v[1]}" data-orig="{v[1]}" placeholder="–" aria-label="{name} diastolic {day:%d.%m.}">')
    return (f'<input type="number" name="{col}" form="{fid}" step="any" inputmode="decimal" value="{v[0]}" data-orig="{v[0]}" placeholder="–" aria-label="{name} {day:%d.%m.}">')


def _entries(d: dict, csrf: str, show_all: bool) -> str:
    days = [d["today"] - timedelta(days=i) for i in range(d["days"])]
    shown = days if show_all else days[:SHOWN_DAYS]
    rows = []
    for day in shown:
        fid = f"hd-{day.isoformat()}"
        cells = "".join(f'<td class="hv">{_field(day, col, kind, d["vals"].get(day, {}).get(col), fid)}</td>' for col, _h, _s, _t, _p, kind in COLS)
        sl = d["sleep"].get(day)
        hours = float(sl["hours"]) if sl else 0
        slept = f"{int(hours)} h {round((hours % 1) * 60):02d}" if 0 < hours < 24 else "–"
        rows.append(f'<tr><td class="stick">{_dm(day)}</td>{cells}<td class="small">{slept}</td>'
                    f'<td><form class="hday" id="{fid}" method="post" action="/web/healthday">{hidden("csrf", csrf)}'
                    f'{hidden("day", day.isoformat())}{hidden("r", str(d["days"]))}{hidden("key", key())}<button class="primary sm" type="submit">Save</button></form></td></tr>')
    heads = "".join(f'<th>{h}{f" <span class=small>{s}</span>" if s else ""}</th>' for _c, h, s, _t, _p, _k in COLS)
    more = ""
    if len(days) > SHOWN_DAYS:
        more = (f'<p><a class="ghost sm" href="/web/health?r={d["days"]}">Show the last {SHOWN_DAYS} days</a></p>' if show_all
                else f'<p><a class="ghost sm" href="/web/health?r={d["days"]}&amp;all=1">Show all {len(days)} days</a></p>')
    return (f'<div class="card"><h2>Entries <span class="small">· one row per day, newest first</span></h2>'
            f'<div class="tablewrap"><table class="ft ht"><thead><tr><th class="stick">Day</th>{heads}<th>Sleep</th><th></th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>{more}'
            '<p class="small hint">Change a value and press Save or Enter; Save is your yes. An empty cell adds a value for that day; '
            'clearing a value deletes it (asked once). Odd values still ask. Bed is the evening before; sleep comes from bed and wake-up.</p></div>')


def render(csrf: str, days: int, show_all: bool = False) -> str:
    d = load(days)
    seg = "".join(f'<a href="/web/health?r={r}"{" aria-current=\"page\"" if r == days else ""}>{r} days</a>' for r in RANGES)
    return (f'<div class="head"><h1>Health</h1><nav class="seg" aria-label="Range">{seg}</nav></div>'
            f'{_charts(d)}{_calendar(d)}{_entries(d, csrf, show_all)}')
