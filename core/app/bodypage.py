"""The Body measurements card on the Health page and the "Body measurements due" line in Today's To review card (03.11.2026,
body measurements T1; spec docs/specs/body-measurements-spec.md sections 6 and 7, mockups in docs/specs/mockups/body-measurements/).
The card: when it was last measured and when it is due, a Measure form (opened by ?measure=1, a session to change by
&mday=), the table Now / Last / Start, one chip per measure with its chart (?bx=), waist ÷ height and the weight rate.
Everything works without the script; the chart is drawn by app.js like the other Health charts. The form posts to
webactions.body_save; this module only reads."""
import json
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode

from . import body
from .db import connect
from .webentry import esc, hidden, key


def _url(r: int, **q) -> str:
    q = {k: v for k, v in q.items() if v not in (None, "", False)}
    return "/web/health?" + urlencode({"r": r, **q}) + "#body"


def _due_text(last: date, today: date, interval: int) -> str:
    left = interval - (today - last).days
    if left <= 0:
        return "due now"
    if left < 7:
        return f"next due in {left} day{'' if left == 1 else 's'}"
    weeks = round(left / 7)
    return f"next due in about {weeks} week{'' if weeks == 1 else 's'}"


def _form(csrf: str, r: int, day: date, today: date, shown: list, now: dict, before: dict) -> str:
    """One row per shown measure: the session's value (to change), the last value before it as the placeholder."""
    rows = "".join(
        f'<label class="brow"><span class="bl"><b>{lbl}</b><span class="small">{hint}</span></span>'
        f'<span class="bin"><input type="number" name="{k}" step="any" min="0" inputmode="decimal" value="{body.cm(now[k]["v"]) if k in now else ""}"'
        f' placeholder="{body.cm(before[k]) if k in before else ""}" aria-label="{lbl} in cm"> cm</span></label>'
        for k, lbl, hint in shown)
    which = "" if day == today else f'<p class="small"><b>Changing the session of {day:%d.%m.%Y}.</b> Clearing a value deletes it (asked once).</p>'
    return (f'<form class="fbox bform" method="post" action="/web/body">{hidden("csrf", csrf)}{hidden("day", day.isoformat())}'
            f'{hidden("r", str(r))}{hidden("key", key())}<p class="small">{body.PROTOCOL}</p>{which}{rows}'
            f'<p class="row"><button class="primary" type="submit">Save</button><a class="ghost" href="{esc(_url(r))}">Cancel</a></p></form>')


def _table(r: int, sessions: list, shown: list) -> str:
    rows = []
    for k, lbl, _h in shown:
        vals = [(s["day"], s["vals"][k]["v"]) for s in sessions if k in s["vals"]]
        if not vals:
            continue
        (d1, v1), now_d, now_v = vals[0], vals[-1][0], vals[-1][1]
        last = body.signed(now_v - vals[-2][1]) if len(vals) > 1 else "–"
        start = body.signed(now_v - v1) if len(vals) > 1 else "–"
        rows.append(f'<tr><td>{lbl}</td><td class="num"><a href="{esc(_url(r, measure=1, mday=now_d.isoformat()))}" '
                    f'aria-label="{lbl} {body.cm(now_v)} cm on {now_d:%d.%m.%Y}, change it">{body.cm(now_v)}</a></td>'
                    f'<td class="num ch">{last}</td><td class="num ch">{start}</td></tr>')
    if not rows:
        return ""
    return ('<div class="tablewrap"><table class="ft bt"><thead><tr><th>cm</th><th>Now</th><th>Last</th><th>Start</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="small">Last = change since the time before. Start = change since {sessions[0]["day"]:%d.%m.%Y}. Tap a value to change it.</p>')


def _chart(r: int, sessions: list, shown: list, pick: Optional[str], today: date, days: int) -> str:
    have = [(k, lbl) for k, lbl, _h in shown if any(k in s["vals"] for s in sessions)]
    if not have:
        return ""
    k = pick if pick in dict(have) else have[0][0]
    pts = [(s["day"], s["vals"][k]["v"]) for s in sessions if k in s["vals"]]
    inside = [p for p in pts if p[0] > today - timedelta(days=days)]
    pts = inside if len(inside) >= 2 else pts  # fewer than 2 in the range: every session
    first = pts[0][0]
    idx = lambda d: (d - first).days
    data = {"start": first.isoformat(), "days": max(1, idx(pts[-1][0]) + 1), "label": f"{dict(have)[k]} in cm",
            "series": [{"mark": "line", "cls": "a", "pts": [[idx(d), v] for d, v in pts]},
                       {"mark": "dot", "cls": "a", "pts": [[idx(d), v, f"{d:%d.%m.%Y}: {body.cm(v)} cm"] for d, v in pts]}],
            "pointLabels": 1, "xAtPoints": 1}
    chips = "".join(f'<a class="chip{" on" if m == k else ""}" href="{esc(_url(r, bx=m))}"{" aria-current=\"true\"" if m == k else ""}>{lbl}</a>'
                    for m, lbl in have)
    return (f'<p class="chips bchips" role="group" aria-label="Measure">{chips}</p>'
            f'<div class="chart" id="c-body" data-chart="{esc(json.dumps(data, separators=(",", ":")))}"></div>')


def _derived(cur, sessions: list, today: date) -> str:
    out = []
    waist = [(s["day"], s["vals"]["waist"]["v"]) for s in sessions if "waist" in s["vals"]]
    if waist:
        day, w = waist[-1]
        h = body.height_on(cur, day)
        out.append(f'Waist ÷ height: <b>{w / h:.2f}</b> (height {h:g} cm, from <a href="/web/settings?tab=body">Settings</a>). '
                   'Under 0.5 is the common target.' if h else
                   'Waist ÷ height: add your height in <a href="/web/settings?tab=body">Settings</a>.')
    rate = body.weight_rate(cur, today)
    if rate:
        kg, pct = rate
        sign = "±" if abs(kg) < 0.005 else "+" if kg > 0 else "−"
        out.append(f'Weight: <b>{sign}{abs(kg):.2f} kg</b> a week over the last 4 weeks ({sign}{abs(pct):.2f} % of body weight a week), '
                   'from the 7-day averages.')
    return "".join(f'<p class="bline">{x}</p>' for x in out)


def card(csrf: str, q, today: date, days: int) -> str:
    """The Body measurements card: q = the Health page's query (measure, mday, bx)."""
    with connect() as conn, conn.cursor() as cur:
        prefs = body.settings(cur)
        shown = body.shown(prefs)
        sessions = body.sessions(cur)
        derived = _derived(cur, sessions, today)
        open_ = q.get("measure") == "1"
        try:
            day = min(date.fromisoformat(str(q.get("mday", ""))), today)
        except ValueError:
            day = today
        now = next((s["vals"] for s in sessions if s["day"] == day), {})
        before = {}  # the form's placeholders: each measure's last value before the session
        for k, _l, _h in shown if open_ else ():
            p = body.last_before(cur, k, day)
            if p:
                before[k] = p[1]
    last = sessions[-1]["day"] if sessions else None
    sub = (f'Last measured {last:%d.%m.%Y} · {_due_text(last, today, prefs["interval"])}' if last
           else "No measurements yet. About 3 minutes with a tape measure, every 4 weeks.")
    btn = (f'<a class="ghost" href="{esc(_url(days))}">Close</a>' if open_ else
           f'<a class="primary bmeasure" href="{esc(_url(days, measure=1))}">Measure</a>')
    form = _form(csrf, days, day, today, shown, now, before) if open_ else ""
    hint = ('<p class="small hint">Or type it in the Log bar or in Claude: <b>waist 92.5 hips 101 chest 98 arm 33.5 thigh 57</b>. '
            'Any of them alone works too ("waist 92.5"); no AI is needed for these.</p>')
    return (f'<div class="card bodycard" id="body"><div class="bhead"><div><h2>Body measurements</h2><p class="small">{sub}</p></div>{btn}</div>'
            f'{form}{_table(days, sessions, shown)}{_chart(days, sessions, shown, q.get("bx"), today, days)}{derived}{hint}</div>')


def today_line(csrf: str, due: dict) -> str:
    """The line in Today's To review card (spec section 7): Measure opens the Health card's form, Not today hides it until the
    next health day."""
    if due["last"]:
        head = "Body measurements due"
        sub = f'Last {due["last"]:%d.%m.} · {due["days"]} days ago · takes about 3 minutes'
    else:
        head, sub = "Body measurements", "not measured yet · takes about 3 minutes"
    return (f'<div class="rev bdue"><div class="bdue-t"><b>{head}</b><span class="small">{sub}</span></div>'
            f'<div class="row"><a class="primary" href="/web/health?measure=1#body">Measure</a>'
            f'<form method="post" action="/web/body/skip">{hidden("csrf", csrf)}<button class="ghost dim" type="submit">Not today</button></form></div></div>')
