"""The web app's Labs and medication page: the reports (each opening with its values, ranges and flags; H and L in amber,
A in red, decided 26.10.2026), the values flagged in the newest lab report, one test over time with its reference range
as a band, and the medication courses. All reports are shown, 2014 included (decided 26.10.). Values are written the
way the logger prints them (v1's own helpers). Corrections post to weblabs.py; this module only reads."""
import json
import re
from urllib.parse import urlencode

from .db import connect
from .v1_commands import _lnum, _ref_text, _val_text
from .webentry import esc, hidden

BACK = re.compile(r"/web/labs(/tests|/meds)?(\?[A-Za-z0-9=&._%-]*)?")
FLAGS = {"H": "high", "L": "low", "A": "abnormal"}


def url(path: str = "/web/labs", **q) -> str:
    q = {k: v for k, v in q.items() if v not in (None, "", False)}
    return path + ("?" + urlencode(q) if q else "")


def safe_back(b) -> str:
    b = str(b or "")
    return b if BACK.fullmatch(b) else "/web/labs"


def printed(r: dict) -> tuple[str, str, str]:
    """A value, its unit and its range as they were printed (what a correction starts from)."""
    value = ((r["qualifier"] or "") + r["value_raw"]) if r["value_raw"] is not None else (r["value_text"] or _lnum(r["value"]))
    unit = r["unit_raw"] if r["unit_raw"] is not None else (r["unit"] or "")
    return value, unit or "", r["ref_range_raw"] or ""


def _flag(f) -> str:
    return f'<span class="flag {f}" title="{FLAGS[f]}">{f}</span>' if f in FLAGS else '<span class="small">–</span>'


def _tabs(on: str) -> str:
    tabs = (("reports", "/web/labs", "Reports"), ("tests", "/web/labs/tests", "Tests over time"), ("meds", "/web/labs/meds", "Medication"))
    return ('<div class="head"><h1>Labs and medication</h1><nav class="seg" aria-label="Labs">'
            + "".join(f'<a href="{h}"{' aria-current="page"' if k == on else ""}>{lbl}</a>' for k, h, lbl in tabs) + "</nav></div>")


RESULT_COLS = """v.id, v.report_id, v.test_id, t.name AS test, v.value, v.unit, v.value_text, v.value_raw, v.unit_raw, v.qualifier,
                 v.ref_low, v.ref_high, v.ref_range_raw, v.flag, v.note, v.body_site, v.side"""


def _site(r: dict) -> str:
    return " ".join(x for x in (r["side"], r["body_site"]) if x)


# ---- reports ---------------------------------------------------------------------------------------

def _value_panel(csrf: str, r: dict, back: str) -> str:
    value, unit, ref = printed(r)
    return (f'<tr class="panel"><td colspan="5"><form class="fbox" method="post" action="/web/labs/value">{hidden("csrf", csrf)}'
            f'{hidden("id", "V" + str(r["id"]))}{hidden("back", back)}<div class="fields">'
            f'<label>Value as printed<input name="value" value="{esc(value)}" maxlength="60" required></label>'
            f'<label>Unit<input name="unit" value="{esc(unit)}" maxlength="30"></label>'
            f'<label>Range as printed<input name="ref" value="{esc(ref)}" maxlength="60" placeholder="e.g. 30-100 or <116"></label></div>'
            f'<p class="row"><button class="primary sm" type="submit">Save</button><a class="ghost sm dim" href="{esc(back)}">Cancel</a>'
            '<span class="small">You see the change first, then Yes. The logger converts the unit and sets the flag again.</span></p></form></td></tr>')


def _report_panel(cur, csrf: str, p: dict, edit: str, edit_report: bool, back: str) -> str:
    cur.execute(f"SELECT {RESULT_COLS} FROM lab_results v JOIN lab_tests t ON t.id = v.test_id WHERE v.report_id = %s ORDER BY v.id", (p["id"],))
    rows = []
    for r in cur.fetchall():
        vid = f"V{r['id']}"
        trend = url("/web/labs/tests", t=str(r["test_id"]))
        rows.append(f'<tr class="{"out " if r["flag"] in FLAGS else ""}{"sel" if edit == vid else ""}" id="{vid}">'
                    f'<td><a href="{esc(trend)}" title="Over time">{esc(r["test"])}</a>'
                    + (f'<span class="notel">{esc(r["note"])}</span>' if r["note"] else "") + "</td>"
                    f'<td class="v">{esc(_val_text(r))}</td><td>{esc(_ref_text(r["ref_low"], r["ref_high"]))}</td><td>{_flag(r["flag"])}</td>'
                    f'<td><a class="icon" href="{esc(url("/web/labs", open=f"R{p["id"]}", edit="" if edit == vid else vid))}#{vid}" '
                    'title="Correct this value" aria-label="Correct">&#9998;</a></td></tr>')
        if edit == vid:
            rows.append(_value_panel(csrf, r, back))
    table = ('<div class="tablewrap"><table class="ft"><thead><tr><th>Test</th><th>Value</th><th>Range</th><th>Flag</th><th></th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>') if rows else '<p class="empty">No values in this report.</p>'
    notes = f'<p class="find">{esc(p["notes"])}</p>' if p["notes"] else ""
    rlink = url("/web/labs", open=f"R{p['id']}", report="" if edit_report else "1")
    form = ""
    if edit_report:
        form = (f'<form class="fbox" method="post" action="/web/labs/report">{hidden("csrf", csrf)}{hidden("id", "R" + str(p["id"]))}{hidden("back", back)}'
                f'<div class="fields"><label>Date<input type="date" name="collected" value="{p["collected_date"].isoformat()}"></label>'
                f'<label>Title<input name="title" value="{esc(p["title"] or "")}" maxlength="200"></label>'
                f'<label>From<input name="provider" value="{esc(p["provider"] or "")}" maxlength="200"></label></div>'
                f'<div class="fields wide"><label>Notes<textarea name="notes" rows="2" maxlength="2000">{esc(p["notes"] or "")}</textarea></label></div>'
                f'<p class="row"><button class="primary sm" type="submit">Save</button><a class="ghost sm dim" href="{esc(url("/web/labs", open=f"R{p['id']}"))}">Cancel</a>'
                '<span class="small">You see the change first, then Yes.</span></p></form>')
    return (f'<tr class="panel"><td colspan="6">{table}{notes}<p class="row"><a class="ghost sm" href="{esc(rlink)}#R{p["id"]}">Change date, title or notes</a></p>'
            f'{form}</td></tr>')


def render_reports(csrf: str, q) -> str:
    open_, edit = str(q.get("open", ""))[:12], str(q.get("edit", ""))[:12]
    back = url("/web/labs", open=open_)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT p.id, p.collected_date, p.title, p.provider, p.report_type, p.notes, count(v.id) AS n,
                              count(v.id) FILTER (WHERE v.flag IN ('H','L','A')) AS flagged
                       FROM lab_reports p LEFT JOIN lab_results v ON v.report_id = p.id
                       GROUP BY p.id ORDER BY p.collected_date DESC, p.id DESC""")
        reports = cur.fetchall()
        newest = next((p for p in reports if p["report_type"] == "lab"), None)
        flagged = []
        if newest:
            cur.execute(f"""SELECT {RESULT_COLS} FROM lab_results v JOIN lab_tests t ON t.id = v.test_id
                            WHERE v.report_id = %s AND v.flag IN ('H','L','A') ORDER BY t.name""", (newest["id"],))
            flagged = cur.fetchall()
        rows = []
        for p in reports:
            rid = f"R{p['id']}"
            is_open = open_ == rid
            link = url("/web/labs", open="" if is_open else rid)
            rows.append(f'<tr class="click{" sel" if is_open else ""}" id="{rid}"><td><a href="{esc(link)}#{rid}">{p["collected_date"]:%d.%m.%Y}</a></td>'
                        f'<td class="l"><a href="{esc(link)}#{rid}">{esc(p["title"] or "Report")}</a></td><td class="l"><span class="pill">{esc(p["report_type"])}</span></td>'
                        f'<td class="l hide-sm">{esc(p["provider"] or "")}</td><td>{p["n"] or "–"}</td>'
                        f'<td>{f"<span class=\"flag H\">{p['flagged']}</span>" if p["flagged"] else "–"}</td></tr>')
            if is_open:
                rows.append(_report_panel(cur, csrf, p, edit, q.get("report") == "1", back))
    chips = "".join(f'<a class="fl" href="{esc(url("/web/labs/tests", t=str(r["test_id"])))}">{_flag(r["flag"])} {esc(r["test"])} '
                    f'<b>{esc(_val_text(r))}</b> <span class="small">range {esc(_ref_text(r["ref_low"], r["ref_high"]))}</span></a>' for r in flagged)
    top = (f'<div class="card"><h2>Flagged in your newest report <span class="small">· {newest["collected_date"]:%d.%m.%Y}</span></h2>'
           f'<div class="flagged">{chips or "<span class=empty>Nothing flagged.</span>"}</div>'
           '<p class="small hint">Click one to see it over time. H high and L low in amber, A abnormal in red.</p></div>') if newest else ""
    table = ('<div class="card"><div class="tablewrap"><table class="ft"><thead><tr><th>Date</th><th class="l">Report</th><th class="l">Kind</th>'
             '<th class="l hide-sm">From</th><th>Values</th><th>Flagged</th></tr></thead>'
             f'<tbody>{"".join(rows) or "<tr><td colspan=6 class=empty>No reports yet.</td></tr>"}</tbody></table></div>'
             '<p class="small hint">New reports come in through the chat (photo or PDF). Click a report to see its values; the pencil corrects one '
             '(shown first, then Yes).</p></div>')
    return _tabs("reports") + top + table


# ---- one test over time ----------------------------------------------------------------------------

def render_tests(csrf: str, q) -> str:
    text, tid = str(q.get("q", ""))[:60].strip(), str(q.get("t", ""))
    with connect() as conn, conn.cursor() as cur:
        test = None
        if tid.isdigit():
            cur.execute("SELECT id, name, unit FROM lab_tests WHERE id = %s", (int(tid),))
            test = cur.fetchone()
        matches = []
        if text and not test:
            cur.execute("""SELECT t.id, t.name, t.unit, count(v.id) AS n FROM lab_tests t JOIN lab_results v ON v.test_id = t.id
                           WHERE t.name ILIKE %s OR EXISTS (SELECT 1 FROM unnest(t.aliases) a WHERE a ILIKE %s)
                           GROUP BY t.id ORDER BY count(v.id) DESC, t.name LIMIT 30""", (f"%{text}%", f"%{text}%"))
            matches = cur.fetchall()
            if len(matches) == 1:
                test = matches[0]
        if not test and not text:  # the tests worth a chart: numbers on three or more days
            cur.execute("""SELECT t.id, t.name, t.unit, count(v.id) AS n FROM lab_tests t JOIN lab_results v ON v.test_id = t.id
                           JOIN lab_reports p ON p.id = v.report_id WHERE v.value IS NOT NULL
                           GROUP BY t.id HAVING count(DISTINCT p.collected_date) >= 3 ORDER BY count(v.id) DESC, t.name LIMIT 30""")
            matches = cur.fetchall()
        rows = []
        if test:
            cur.execute(f"""SELECT {RESULT_COLS}, p.collected_date FROM lab_results v JOIN lab_tests t ON t.id = v.test_id
                            JOIN lab_reports p ON p.id = v.report_id WHERE v.test_id = %s ORDER BY p.collected_date, v.id""", (test["id"],))
            rows = cur.fetchall()
    search = (f'<form class="row" method="get" action="/web/labs/tests"><input type="search" name="q" value="{esc(text)}" class="grow" '
              'placeholder="Search a test: ldl, vitamin d, hba1c" aria-label="Search a test"><button class="ghost sm" type="submit">Search</button></form>')
    pick = ""
    if matches and not test:
        pick = ('<p class="chips">' + "".join(f'<a class="chip" href="{esc(url("/web/labs/tests", t=str(m["id"])))}">{esc(m["name"])} '
                                             f'<span class="small">{m["n"]}</span></a>' for m in matches) + "</p>")
        if not text:
            pick = '<p class="small">Tests with values on three or more days:</p>' + pick
    elif text and not test:
        pick = '<p class="empty">No test with that name. Try another word.</p>'
    body = ""
    if test:
        nums = [r for r in rows if r["value"] is not None]
        last = rows[-1] if rows else None
        band = [float(last["ref_low"]) if last and last["ref_low"] is not None else None,
                float(last["ref_high"]) if last and last["ref_high"] is not None else None] if last else None
        chart = ""
        dates = sorted({r["collected_date"] for r in nums})
        if len(dates) >= 2:
            first = dates[0]
            one_per_day = len(dates) == len(nums)
            site = lambda r: f" ({_site(r)})" if _site(r) else ""
            pt = lambda r: [(r["collected_date"] - first).days, float(r["value"]), f'{r["collected_date"]:%d.%m.%Y}: {_val_text(r)}{site(r)}']
            dots = [{"mark": "dot", "cls": "a", "pts": [pt(r) for r in nums if r["flag"] not in FLAGS]},
                    {"mark": "dot", "cls": "w", "pts": [pt(r) for r in nums if r["flag"] in ("H", "L")]},
                    {"mark": "dot", "cls": "r", "pts": [pt(r) for r in nums if r["flag"] == "A"]}]
            data = {"start": first.isoformat(), "days": (dates[-1] - first).days + 1, "label": f'{test["name"]} over time', "years": True,
                    "band": band if band and any(b is not None for b in band) else None,
                    "series": ([{"mark": "line", "cls": "a", "pts": [pt(r) for r in nums]}] if one_per_day else []) + dots}
            chart = (f'<div class="chart" id="c-test" data-chart="{esc(json.dumps(data, separators=(",", ":")))}"></div>'
                     '<p class="legend"><i class="key sq band"></i>reference range (from the newest value) <i class="key w"></i>outside the range'
                     + ("" if one_per_day else " · several values on one day (different sites), so dots without a line") + "</p>")
        elif len(nums) > 1:
            chart = '<p class="small">All values are from one day (different sites); see them below. The chart starts with a second day.</p>'
        elif nums:
            chart = '<p class="small">Only one value so far; the chart starts with the second.</p>'
        table = "".join(f'<tr class="{"out" if r["flag"] in FLAGS else ""}"><td>{r["collected_date"]:%d.%m.%Y}'
                        + (f'<span class="notel">{esc(_site(r))}</span>' if _site(r) else "") + f'</td><td class="v">{esc(_val_text(r))}</td>'
                        f'<td>{esc(_ref_text(r["ref_low"], r["ref_high"]))}</td><td>{_flag(r["flag"])}</td>'
                        f'<td class="l"><a href="{esc(url("/web/labs", open=f"R{r["report_id"]}"))}#R{r["report_id"]}">R{r["report_id"]}</a></td></tr>'
                        for r in reversed(rows))
        body = (f'<div class="card"><h2>{esc(test["name"])} <span class="small">· {len(rows)} values</span></h2>{chart}</div>'
                '<div class="card"><div class="tablewrap"><table class="ft"><thead><tr><th>Date</th><th>Value</th><th>Range</th><th>Flag</th>'
                f'<th class="l">Report</th></tr></thead><tbody>{table}</tbody></table></div></div>')
    return _tabs("tests") + f'<div class="card">{search}{pick}</div>' + body


# ---- medication ------------------------------------------------------------------------------------

def render_meds(csrf: str, q) -> str:
    edit = str(q.get("edit", ""))[:12]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        cur.execute("""SELECT * FROM medications ORDER BY (stop_date IS NOT NULL AND stop_date < log_day(now())), start_date DESC NULLS LAST, id DESC""")
        meds = cur.fetchall()
    rows = []
    for m in meds:
        did = f"D{m['id']}"
        running = m["stop_date"] is None or m["stop_date"] >= today
        base = hidden("csrf", csrf) + hidden("id", did) + hidden("back", "/web/labs/meds")
        end = ("" if not running or m["stop_date"] else
               f'<form method="post" action="/web/labs/med">{base}{hidden("act", "end")}<button class="ghost sm" type="submit">End</button></form>')
        rows.append(f'<tr class="{"sel" if edit == did else ""}" id="{did}"><td class="l"><b>{esc(m["name"])}</b>'
                    + (f'<span class="notel">{esc(m["ingredient"])}</span>' if m["ingredient"] else "") + "</td>"
                    f'<td class="l">{esc(m["dose"] or "–")}</td><td class="l">{esc(m["frequency"] or "–")}</td><td class="l hide-sm">{esc(m["indication"] or "–")}</td>'
                    f'<td>{m["start_date"].strftime("%d.%m.%Y") if m["start_date"] else "–"}</td>'
                    f'<td>{m["stop_date"].strftime("%d.%m.%Y") if m["stop_date"] else "<span class=med-run>running</span>"}</td>'
                    f'<td><span class="row end"><a class="icon" href="{esc(url("/web/labs/meds", edit="" if edit == did else did))}#{did}" '
                    f'title="Change" aria-label="Change">&#9998;</a>{end}</span></td></tr>')
        if edit == did:
            rows.append(f'<tr class="panel"><td colspan="7"><form class="fbox" method="post" action="/web/labs/med">{base}{hidden("act", "save")}<div class="fields">'
                        f'<label>Dose<input name="dose" value="{esc(m["dose"] or "")}" maxlength="60"></label>'
                        f'<label>How often<input name="frequency" value="{esc(m["frequency"] or "")}" maxlength="60"></label>'
                        f'<label>Why<input name="indication" value="{esc(m["indication"] or "")}" maxlength="120"></label>'
                        f'<label>Start<input type="date" name="start" value="{m["start_date"].isoformat() if m["start_date"] else ""}"></label>'
                        f'<label>Stop <span class="small">(empty = running)</span><input type="date" name="stop" value="{m["stop_date"].isoformat() if m["stop_date"] else ""}"></label></div>'
                        '<p class="row"><button class="primary sm" type="submit">Save</button><a class="ghost sm dim" href="/web/labs/meds">Cancel</a>'
                        '<span class="small">You see the change first, then Yes.</span></p></form></td></tr>')
    n_run = sum(1 for m in meds if m["stop_date"] is None or m["stop_date"] >= today)
    return (_tabs("meds") + f'<div class="card"><h2>Medication <span class="small">· {n_run} running</span></h2>'
            '<div class="tablewrap"><table class="ft"><thead><tr><th class="l">Name</th><th class="l">Dose</th><th class="l">How often</th>'
            '<th class="l hide-sm">Why</th><th>Start</th><th>Stop</th><th></th></tr></thead>'
            f'<tbody>{"".join(rows) or "<tr><td colspan=7 class=empty>No medication recorded.</td></tr>"}</tbody></table></div>'
            '<p class="small hint">A new course is a sentence in the box above ("started vitamin D 1000 IU daily"), read back before it is saved, '
            'as in the chat.</p></div>')
