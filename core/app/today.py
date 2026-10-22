"""Read-only 'Today' page. Served only on the private host name (Caddy, home network and tailnet); never through the public tunnel."""
import html
from datetime import date, timedelta

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from . import rules
from .config import TODAY_HOST
from .db import connect

DAYS = 7
_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"}


def is_private_request(request: Request) -> bool:
    """Only the private host name, and never a request that came through Cloudflare."""
    host = request.headers.get("host", "").split(":")[0].lower()
    via_cloudflare = any(h in request.headers for h in ("cf-ray", "cf-connecting-ip", "cf-visitor"))
    return host == TODAY_HOST and not via_cloudflare


def _num(x) -> str:
    return ("%.2f" % float(x)).rstrip("0").rstrip(".")


def load() -> tuple[date, dict[date, list[tuple[str, str]]]]:
    """(today's health day, {day: [(time text, entry text)]}) for the last DAYS days, newest first inside a day."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        since = today - timedelta(days=DAYS - 1)
        cur.execute("""SELECT m.day, m.event_at, m.time_known, t.name, m.value1, m.value2, m.period FROM measurements m
                       JOIN log_types t ON t.id = m.type_id WHERE t.name IN ('weight','bp') AND m.day >= %s""", (since,))
        items = []
        for r in cur.fetchall():
            if r["name"] == "weight":
                text = f"Weight {_num(r['value1'])} kg"
            else:
                text = f"Blood pressure {_num(r['value1'])}/{_num(r['value2'])}" + (f" ({r['period']})" if r["period"] else "")
            items.append((r["day"], r["event_at"], r["time_known"], text))
        cur.execute("""SELECT e.day, e.event_at, e.time_known FROM events e JOIN log_types t ON t.id = e.type_id
                       WHERE t.name='wake' AND e.day >= %s""", (since,))
        items += [(r["day"], r["event_at"], r["time_known"], "Wake-up") for r in cur.fetchall()]
    days: dict[date, list[tuple[str, str]]] = {today - timedelta(days=i): [] for i in range(DAYS)}
    for day, at, known, text in sorted(items, key=lambda x: x[1], reverse=True):
        when = at.astimezone(rules.BERLIN).strftime("%H:%M") if known else "no time"
        days.setdefault(day, []).append((when, text))
    return today, days


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Logger today</title><style>
body{{font-family:system-ui,sans-serif;max-width:32rem;margin:1.5rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}}
@media(prefers-color-scheme:dark){{body{{color:#e8e8e8;background:#161616}}}}
h1{{font-size:1.4rem}}h2{{font-size:1.05rem;margin:1.4rem 0 .4rem}}ul{{list-style:none;padding:0;margin:0}}
li{{padding:.35rem 0;border-bottom:1px solid #8884}}.t{{display:inline-block;width:4.5rem;opacity:.7}}.none{{opacity:.6}}</style></head>
<body><h1>Today, {today}</h1>{sections}</body></html>"""


def render() -> str:
    today, days = load()
    parts = []
    for day, entries in days.items():
        label = "Today" if day == today else (day.strftime("%a ") + day.strftime("%d.%m."))
        body = "".join(f'<li><span class="t">{html.escape(w)}</span>{html.escape(t)}</li>' for w, t in entries) or '<li class="none">nothing logged</li>'
        parts.append(f"<h2>{html.escape(label)}</h2><ul>{body}</ul>")
    return _PAGE.format(today=today.strftime("%d.%m.%Y"), sections="".join(parts))


def add_route(mcp) -> None:
    @mcp.custom_route("/today", methods=["GET"])
    async def today_page(request: Request) -> Response:
        if not is_private_request(request):
            return Response("Not found", status_code=404)
        return HTMLResponse(render(), headers=_HEADERS)
