"""The web app's Private page: private and gratitude entries with the user's words shown (decided 26.10.2026), a New
entry box that saves without the AI, a filter and a search, change and delete, and the gratitude entries by day and by
week. Only on the app address; the forms post to webprivate.py; this module only reads."""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from . import rules
from .db import connect
from .todaypage import AREAS, THOUGHT_TYPES
from .webentry import esc, hidden, key

KINDS = ("private", "gratitude")
LIST_LIMIT = 300
BACK = re.compile(r"/web/private(\?[A-Za-z0-9=&._%-]*)?")
LABEL = re.compile(r"^\s*(private|gratitude)\s*:\s*", re.I)


def url(**q) -> str:
    q = {k: v for k, v in q.items() if v not in (None, "", False)}
    return "/web/private" + ("?" + urlencode(q) if q else "")


def safe_back(b) -> str:
    b = str(b or "")
    return b if BACK.fullmatch(b) else "/web/private"


def words(content: str) -> str:
    """The entry as written, without the "gratitude:" or "private:" label in front."""
    return LABEL.sub("", content or "", count=1)


def _when(e) -> datetime:
    return (e["event_at"] or e["received_at"]).astimezone(rules.BERLIN)


def _opts(values, selected, blank: str = "") -> str:
    return (f'<option value="">{esc(blank)}</option>' if blank else "") + "".join(
        f'<option{" selected" if v == selected else ""}>{esc(v)}</option>' for v in values)


def _rows(cur, where: str = "", args=()) -> list:
    cur.execute(f"""SELECT id, received_at, event_at, label, content, summary, thought_type, area, tags FROM private.entries
                    {where} ORDER BY coalesce(event_at, received_at) DESC, id DESC LIMIT %s""", (*args, LIST_LIMIT))  # where: fixed texts only
    return cur.fetchall()


def _new_box(csrf: str, back: str) -> str:
    return (f'<form class="card" method="post" action="/web/private/new">{hidden("csrf", csrf)}{hidden("key", key())}{hidden("back", back)}'
            '<div class="head sub"><h2>New entry</h2><span class="row">'
            '<label class="row small"><input type="radio" name="kind" value="gratitude" checked> Gratitude</label>'
            '<label class="row small"><input type="radio" name="kind" value="private"> Private</label></span></div>'
            '<textarea name="text" rows="3" maxlength="4000" required class="wide" placeholder="Grateful for … (one per line)" aria-label="New private or gratitude entry"></textarea>'
            '<p class="row"><button class="primary sm" type="submit">Save</button><span class="small">Saved straight into the private store, '
            'without the AI. Gratitude: each line is its own entry.</span></p></form>')


def _entry(csrf: str, e: dict, edit: str, here: dict) -> str:
    back = url(**here, edit=edit)
    pid, w = f"P{e['id']}", _when(e)
    meta = " · ".join(x for x in (e["summary"], e["area"], " ".join("#" + t for t in (e["tags"] or []))) if x)
    extra = ""
    if edit == pid:
        base = hidden("csrf", csrf) + hidden("id", pid) + hidden("back", back)
        extra = (f'<form class="fbox" method="post" action="/web/private/entry">{base}{hidden("act", "save")}<div class="fields">'
                 f'<label>Type<select name="thought_type">{_opts(THOUGHT_TYPES, e["thought_type"], "none")}</select></label>'
                 f'<label>Area<select name="area">{_opts(AREAS, e["area"], "none")}</select></label>'
                 f'<label>Date<input type="date" name="day" value="{w:%Y-%m-%d}"></label>'
                 f'<label>Time<input type="time" name="time" value="{w:%H:%M}"></label></div>'
                 f'<div class="fields wide"><label>Summary<input name="summary" value="{esc(e["summary"] or "")}" maxlength="300" required></label>'
                 f'<label>Tags <span class="small">(comma between them)</span><input name="tags" value="{esc(", ".join(e["tags"] or []))}" maxlength="200"></label></div>'
                 f'<p class="row"><button class="primary sm" type="submit">Save</button><a class="ghost sm dim" href="{esc(back)}">Cancel</a>'
                 '<span class="small">Save is your yes. Your words stay as you wrote them.</span></p></form>'
                 f'<form class="row" method="post" action="/web/private/entry">{base}{hidden("act", "delete")}'
                 '<button class="ghost sm bad" type="submit">Delete</button><span class="small">asks once</span></form>')
    link = url(**here, edit="" if edit == pid else pid)
    return (f'<div class="pe{" sel" if edit == pid else ""}" id="{pid}"><span class="when">{w:%a %d.%m.%Y %H:%M}</span>'
            f'<span><span class="pill{" grat" if e["label"] == "gratitude" else ""}">{esc(e["label"])}</span> <span class="words">{esc(words(e["content"]))}</span>'
            f'<span class="sum">{esc(meta)}</span></span>'
            f'<a class="icon" href="{esc(link)}#{pid}" title="Change or delete" aria-label="Change or delete">&#9998;</a>{extra}</div>')


def _all(cur, csrf: str, q) -> str:
    kind = q.get("kind", "") if q.get("kind", "") in KINDS else ""
    text = str(q.get("q", ""))[:60]
    edit = str(q.get("edit", ""))[:12]
    where, args = [], []
    if kind:
        where.append("label = %s"); args.append(kind)
    if text:
        where.append("(content ILIKE %s OR summary ILIKE %s)"); args += [f"%{text}%"] * 2
    rows = _rows(cur, ("WHERE " + " AND ".join(where)) if where else "", args)
    here = {"kind": kind, "q": text}
    back = url(**here, edit=edit)
    chips = "".join(f'<a class="chip{" on" if k == kind else ""}" href="{esc(url(kind=k, q=text))}">{lbl}</a>'
                    for k, lbl in (("", "All"), ("private", "Private"), ("gratitude", "Gratitude")))
    search = (f'<form class="row" method="get" action="/web/private">{hidden("kind", kind) if kind else ""}'
              f'<input type="search" name="q" value="{esc(text)}" class="grow" placeholder="Search your private entries" aria-label="Search">'
              '<button class="ghost sm" type="submit">Search</button></form>')
    body = "".join(_entry(csrf, e, edit, here) for e in rows) or '<p class="empty">Nothing matches.</p>'
    return (_new_box(csrf, back) + f'<div class="card">{search}<p class="chips">{chips}</p><h2>{len(rows)} entries</h2>{body}'
            '<p class="small hint">Your words as you wrote them. Only on the app address, never through the tunnel, never kept by the browser.</p></div>')


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _gratitude(cur, csrf: str, q) -> str:
    cur.execute("SELECT log_day(now()) AS d")
    today = cur.fetchone()["d"]
    mode = "week" if q.get("mode") == "week" else "day"
    try:
        d = date.fromisoformat(str(q.get("day", "")))
    except ValueError:
        d = today
    d = min(d, today)
    if mode == "day":
        first = last = d
        step, title = 1, d.strftime("%a %d.%m.%Y")
    else:
        first = _monday(d)
        last = first + timedelta(days=6)
        step, title = 7, f"Week {first.isocalendar()[1]} · {first:%d.%m.} to {last:%d.%m.%Y}"
    lo = datetime.combine(first, datetime.min.time(), rules.BERLIN)
    hi = datetime.combine(last + timedelta(days=1), datetime.min.time(), rules.BERLIN)
    cur.execute("""SELECT id, received_at, event_at, content FROM private.entries WHERE label = 'gratitude'
                   AND coalesce(event_at, received_at) >= %s AND coalesce(event_at, received_at) < %s
                   ORDER BY coalesce(event_at, received_at), id""", (lo, hi))
    rows = cur.fetchall()
    by: dict = {}
    for e in rows:
        by.setdefault(_when(e).date(), []).append(e)
    def li(e, with_time: bool) -> str:
        return f'<li>{esc(words(e["content"]))}' + (f' <span class="small">{_when(e):%H:%M}</span>' if with_time else "") + "</li>"

    if mode == "day":
        body = f'<ul class="gl">{"".join(li(e, True) for e in rows)}</ul>' if rows else '<p class="empty">No gratitude entries on this day.</p>'
    else:
        body = "".join(f'<div class="day"><h3><span>{day:%a %d.%m.}</span><span class="small">{len(es)}</span></h3>'
                       f'<ul class="gl">{"".join(li(e, False) for e in es)}</ul></div>'
                       for day, es in sorted(by.items(), reverse=True)) or '<p class="empty">No gratitude entries in this week.</p>'
    prev, nxt = first - timedelta(days=step), first + timedelta(days=step)
    seg = (f'<nav class="seg" aria-label="Day or week"><a href="{esc(url(view="grat", day=d.isoformat()))}"{' aria-current="page"' if mode == "day" else ""}>Day</a>'
           f'<a href="{esc(url(view="grat", mode="week", day=d.isoformat()))}"{' aria-current="page"' if mode == "week" else ""}>Week</a></nav>')
    arrows = (f'<span class="row"><a class="ghost" href="{esc(url(view="grat", mode=mode if mode == "week" else "", day=prev.isoformat()))}" aria-label="Earlier">&larr;</a>'
              f'<b>{title}</b>'
              + (f'<a class="ghost" href="{esc(url(view="grat", mode=mode if mode == "week" else "", day=nxt.isoformat()))}" aria-label="Later">&rarr;</a>' if last < today else "")
              + "</span>")
    count = f'<h2>{len(rows)} gratitude {"entry" if len(rows) == 1 else "entries"}{" this week" if mode == "week" else ""}</h2>'
    return (_new_box(csrf, url(view="grat", mode=mode if mode == "week" else "", day=d.isoformat()))
            + f'<div class="card"><div class="head sub">{seg}{arrows}</div>{count}{body}</div>')


def render(csrf: str, q) -> str:
    view = "grat" if q.get("view") == "grat" else "all"
    tabs = ('<div class="head"><h1>Private</h1><nav class="seg" aria-label="Private">'
            f'<a href="/web/private"{' aria-current="page"' if view == "all" else ""}>All entries</a>'
            f'<a href="/web/private?view=grat"{' aria-current="page"' if view == "grat" else ""}>Gratitude</a></nav></div>')
    with connect() as conn, conn.cursor() as cur:
        return tabs + (_gratitude(cur, csrf, q) if view == "grat" else _all(cur, csrf, q))
