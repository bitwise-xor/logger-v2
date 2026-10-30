"""The web app's Recent page (31.10.2026): the last messages sent to the logger from the web app and from Claude, newest
first, each with what it saved (foods, values, ticks, thoughts), so the last meal or tick can be checked at a glance.
Private entries are listed with their words (decided by the user). Imports (sheets, exports) are left out."""
from datetime import date, timedelta

from . import rules
from .db import connect
from .todaypage import _plain
from .webentry import esc

LIMIT = 50
CHANNELS = {"web": "Web app", "claude-chat": "Claude", "mcp": "Claude"}


def load() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        cur.execute("""SELECT id, received_at, channel, content FROM raw_inbox WHERE channel = ANY(%s)
                       ORDER BY received_at DESC, id DESC LIMIT %s""", (list(CHANNELS), LIMIT))
        msgs = [dict(m, kind="raw") for m in cur.fetchall()]
        ids = [m["id"] for m in msgs]
        saved: dict = {i: [] for i in ids}
        day_of: dict = {}
        cur.execute("""SELECT e.raw_id, f.name, e.amount, f.basis, e.meal, e.day FROM food_entries e JOIN foods f ON f.id = e.food_id
                       WHERE e.raw_id = ANY(%s) ORDER BY e.id""", (ids,))
        for r in cur.fetchall():
            meal = "drinks" if r["meal"] == 0 else f"meal {r['meal']}"
            saved[r["raw_id"]].append(("Food", f"{r['name']} {_plain(r['amount'])} {r['basis']} · {meal}"))
            day_of.setdefault(r["raw_id"], r["day"])
        cur.execute("""SELECT m.raw_id, t.label, m.value1, m.value2, m.period, t.unit, m.day FROM measurements m
                       JOIN log_types t ON t.id = m.type_id WHERE m.raw_id = ANY(%s) ORDER BY m.id""", (ids,))
        for r in cur.fetchall():
            v = "/".join(_plain(x) for x in (r["value1"], r["value2"]) if x is not None)
            saved[r["raw_id"]].append(("Health", " ".join(x for x in (r["label"], v, r["unit"] or "", r["period"] or "") if x)))
            day_of.setdefault(r["raw_id"], r["day"])
        cur.execute("""SELECT e.raw_id, t.label, t.category, e.event_at, e.time_known, e.day FROM events e
                       JOIN log_types t ON t.id = e.type_id WHERE e.raw_id = ANY(%s) ORDER BY e.id""", (ids,))
        for r in cur.fetchall():
            at = f" {r['event_at'].astimezone(rules.BERLIN):%H:%M}" if r["time_known"] and r["category"] == "sleep" else ""
            saved[r["raw_id"]].append(("Habit" if r["category"] != "sleep" else "Sleep", f"{r['label']}{at}"))
            day_of.setdefault(r["raw_id"], r["day"])
        cur.execute("SELECT raw_id, summary, thought_type FROM thoughts WHERE raw_id = ANY(%s) ORDER BY id", (ids,))
        for r in cur.fetchall():
            saved[r["raw_id"]].append(((r["thought_type"] or "note").capitalize(), r["summary"]))
        cur.execute("SELECT raw_id, count(*) AS n FROM lab_reports WHERE raw_id = ANY(%s) GROUP BY raw_id", (ids,))
        for r in cur.fetchall():
            saved[r["raw_id"]].append(("Labs", f"{r['n']} lab report{'' if r['n'] == 1 else 's'}"))
        cur.execute("SELECT raw_id, count(*) AS n FROM todo_actions WHERE raw_id = ANY(%s) GROUP BY raw_id", (ids,))
        for r in cur.fetchall():
            saved[r["raw_id"]].append(("Task", f"{r['n']} to-do change{'' if r['n'] == 1 else 's'}"))
        try:  # private entries have their own inbox
            cur.execute("""SELECT id, received_at, channel, content, label FROM private.entries
                           ORDER BY received_at DESC, id DESC LIMIT %s""", (LIMIT,))
            msgs += [dict(p, kind="private") for p in cur.fetchall()]
        except Exception:  # noqa: BLE001 - no access in this view: the page shows the rest
            conn.rollback()
    msgs.sort(key=lambda m: m["received_at"], reverse=True)
    return {"today": today, "msgs": msgs[:LIMIT], "saved": saved, "day_of": day_of}


def _day_head(d: date, today: date) -> str:
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return f"{d:%a %d.%m.}"


def render(csrf: str) -> str:
    d = load()
    out, last = [], None
    for m in d["msgs"]:
        at = m["received_at"].astimezone(rules.BERLIN)
        if at.date() != last:
            last = at.date()
            out.append(f'<h3 class="rc-day">{_day_head(last, d["today"])}</h3>')
        if m["kind"] == "private":
            chan, items = "Private", [("Private", m.get("label") or "private entry")]
        else:
            chan, items = CHANNELS.get(m["channel"], m["channel"]), d["saved"].get(m["id"], [])
        lines = ("<ul class=\"rc-saved\">" + "".join(f'<li><span class="pill">{esc(k)}</span> {esc(v)}</li>' for k, v in items) + "</ul>"
                 if items else '<p class="small rc-none">Nothing saved from this message.</p>')
        day = d["day_of"].get(m["id"]) if m["kind"] == "raw" else None
        acts = (f'<button class="ghost sm" type="button" data-fill="{esc(m["content"])}">Use again</button>'
                + (f'<a class="ghost sm" href="/web/?day={day.isoformat()}">Open day</a>' if day else ""))
        out.append(f'<article class="rc"><div class="rc-top"><span class="num">{at:%H:%M}</span><span class="pill">{chan}</span></div>'
                   f'<p class="rc-text">{esc(m["content"])}</p>{lines}<div class="row">{acts}</div></article>')
    body = "".join(out) or '<p class="empty">No messages yet.</p>'
    return (f'<div class="head"><h1>Recent</h1><span class="small">The last {LIMIT} messages from the web app and Claude</span></div>'
            f'<div class="card recent">{body}</div>')
