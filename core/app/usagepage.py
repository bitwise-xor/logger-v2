"""The web app's Usage page: what the AI calls cost (in dollars, decided 26.10.2026) over 7 days, 30 days or since the
records began, per day split by model, by kind of call, and the most expensive messages with their first words
(private ones never). Read from audit_log, where every call's model, cost and (for messages) tokens are kept."""
import json
from datetime import timedelta

from . import rules
from .db import connect
from .webentry import esc

RANGES = (("7", "7 days"), ("30", "30 days"), ("all", "Everything"))
TOP = 10
KINDS = {"log": "message", "food_lookup": "food lookup", "lab_report": "lab report"}
FIRST_WORDS = 60


def _family(model: str) -> str:
    m = (model or "").lower()
    return "Haiku" if "haiku" in m else "Sonnet" if "sonnet" in m else "Other"


def _usd(v: float) -> str:
    return f"${v:,.2f}" if v >= 0.1 or v == 0 else f"${v:.3f}"


def load(r: str) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        cur.execute("""SELECT min(log_day(at)) AS d FROM audit_log WHERE detail ? 'ai' AND jsonb_array_length(detail->'ai') > 0""")
        first = cur.fetchone()["d"] or today
        start = first if r == "all" else today - timedelta(days=int(r) - 1)
        cur.execute("""SELECT a.id, a.at, log_day(a.at) AS day, a.tool, a.client, a.detail, ri.content
                       FROM audit_log a LEFT JOIN raw_inbox ri ON ri.id = (a.detail->>'raw_id')::bigint
                       WHERE a.detail ? 'ai' AND jsonb_array_length(a.detail->'ai') > 0 AND log_day(a.at) BETWEEN %s AND %s
                       ORDER BY a.at""", (start, today))
        rows = cur.fetchall()
    return {"today": today, "first": first, "start": start, "rows": rows}


def _first_words(row) -> str:
    if row["detail"].get("private_id"):  # private entries keep no text in the inbox, and none is shown
        return '<span class="pill">private</span>'
    if row["content"]:
        words = " ".join(row["content"].split())
        return esc(words[:FIRST_WORDS] + ("…" if len(words) > FIRST_WORDS else ""))
    return '<span class="small">–</span>'


def render(r: str) -> str:
    r = r if r in dict(RANGES) else "7"
    d = load(r)
    days = (d["today"] - d["start"]).days + 1
    calls, cost, tin, tout, tok_known = 0, 0.0, 0, 0, True
    per_day = {}
    kinds: dict = {}
    msgs, msg_cost = 0, 0.0
    tops = []
    for row in d["rows"]:
        ai = row["detail"].get("ai") or []
        row_cost = 0.0
        for c in ai:
            usd = float(c.get("usd") or 0)
            calls += 1
            cost += usd
            row_cost += usd
            if "in" in c:
                tin += int(c["in"] or 0)
                tout += int(c.get("out") or 0)
            else:
                tok_known = False
            fam = _family(c.get("model"))
            per_day.setdefault(row["day"], {}).setdefault(fam, 0.0)
            per_day[row["day"]][fam] += usd
        k = KINDS.get(row["tool"], row["tool"])
        kc = kinds.setdefault(k, [0, 0.0])
        kc[0] += len(ai)
        kc[1] += row_cost
        if row["tool"] == "log":
            msgs += 1
            msg_cost += row_cost
        tops.append((row_cost, row, sorted({_family(c.get("model")) for c in ai})))
    since = f'Recorded since {d["first"]:%d.%m.%Y} (the v2 trial); v1 kept no costs.'
    tiles = (f'<div class="totals"><div class="total"><div class="lbl">AI calls</div><span class="n num">{calls:,}</span></div>'
             f'<div class="total"><div class="lbl">Cost</div><span class="n num">{_usd(cost)}</span></div>'
             f'<div class="total"><div class="lbl">Per message</div><span class="n num">{_usd(msg_cost / msgs) if msgs else "–"}</span>'
             f' <span class="of">{msgs} messages</span></div>'
             f'<div class="total"><div class="lbl">Tokens in / out</div><span class="n num">{round(tin / 1000):,}k / {round(tout / 1000):,}k</span>'
             + ('' if tok_known else ' <span class="of">messages only</span>') + '</div></div>')
    # cost per day, Haiku at the bottom, Sonnet on top
    series = []
    for fam, cls in (("Haiku", "a"), ("Sonnet", "b"), ("Other", "c")):
        pts = []
        for i in range(days):
            day = d["start"] + timedelta(days=i)
            v = per_day.get(day, {}).get(fam, 0.0)
            if v:
                below = sum(per_day[day].get(f, 0.0) for f in ("Haiku", "Sonnet", "Other")[:("Haiku", "Sonnet", "Other").index(fam)])
                pts.append([i, round(v, 5), f"{day:%a %d.%m.}: {fam} {_usd(v)}", round(below, 5)])
        if pts:
            series.append({"mark": "bar", "cls": cls, "pts": pts})
    data = {"start": d["start"].isoformat(), "days": days, "series": series, "label": "Cost per day in dollars", "min": 0,
            "prefix": "$", "decimals": 2 if max((sum(v.values()) for v in per_day.values()), default=0) >= 0.1 else 3}
    chart = f'<div class="chart" id="c-usage" data-chart="{esc(json.dumps(data, separators=(",", ":")))}"></div>'
    by_kind = "".join(f'<tr><td>{esc(k)}</td><td>{n:,}</td><td>{_usd(v)}</td></tr>' for k, (n, v) in sorted(kinds.items(), key=lambda x: -x[1][1]))
    top = sorted(tops, key=lambda x: -x[0])[:TOP]
    top_rows = "".join(
        f'<tr><td>{row["at"].astimezone(rules.BERLIN):%a %d.%m. %H:%M}</td><td class="l first">{_first_words(row)}'
        + (' <span class="small">· read again by the larger model</span>' if row["detail"].get("escalated") else "")
        + f'</td><td class="l">{esc(KINDS.get(row["tool"], row["tool"]))}</td><td class="l">{esc(" + ".join(fams))}</td><td>{_usd(c)}</td></tr>'
        for c, row, fams in top)
    seg = "".join(f'<a href="/web/usage?r={k}"{' aria-current="page"' if k == r else ""}>{lbl}</a>' for k, lbl in RANGES)
    empty = '<p class="empty">No AI calls in this range.</p>'
    return (f'<div class="head"><h1>Usage</h1><nav class="seg" aria-label="Range">{seg}</nav></div>'
            f'<div class="card">{tiles}<p class="small hint">{since}</p></div>'
            '<div class="grid2 gap"><div class="card"><h2>Cost per day <span class="small">dollars</span></h2>'
            f'{chart if series else empty}<p class="legend"><i class="key sq a"></i>Haiku (every message) '
            '<i class="key sq b"></i>Sonnet (open questions, lab reports)</p></div>'
            f'<div class="card"><h2>By kind</h2><div class="tablewrap"><table class="ft"><thead><tr><th>Kind</th><th>Calls</th><th>Cost</th></tr></thead>'
            f'<tbody>{by_kind}<tr class="sum"><td>All</td><td>{calls:,}</td><td>{_usd(cost)}</td></tr></tbody></table></div></div></div>'
            f'<div class="card"><h2>Most expensive messages</h2>'
            + (f'<div class="tablewrap"><table class="ft"><thead><tr><th>When</th><th class="l">Message</th><th class="l">Kind</th>'
               f'<th class="l">Model</th><th>Cost</th></tr></thead><tbody>{top_rows}</tbody></table></div>' if top else empty)
            + '<p class="small hint">Private entries never show their words. Food lookups and lab reports show no words.</p></div>')
