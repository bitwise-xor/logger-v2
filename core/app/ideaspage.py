"""The web app's Ideas and tasks page: the thoughts (filter, change, move between stages, delete) and the open Todoist
tasks of Lists and the Inbox (tick off, change the text or section, due dates; shopping stays in the chat and Todoist).
The forms post to webideas.py; this module only reads (Todoist through todos' 10-minute cache)."""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from . import todos
from .db import connect
from .todaypage import AREAS, THOUGHT_TYPES
from .todoist import TodoistError
from .webentry import esc, hidden

STAGES = ("inbox", "active", "someday", "done", "dropped")
NEXT_STEPS = ("research", "plan", "do", "read")
STAGE_BUTTONS = (("active", "Active"), ("done", "Done"), ("dropped", "Drop"), ("someday", "Someday"))
LIST_LIMIT = 200
BACK = re.compile(r"/web/ideas(/todoist)?(\?[A-Za-z0-9=&._%-]*)?")


def url(path: str, **q) -> str:
    q = {k: v for k, v in q.items() if v not in (None, "", False)}
    return path + ("?" + urlencode(q) if q else "")


def safe_back(b) -> str:
    b = str(b or "")
    return b if BACK.fullmatch(b) else "/web/ideas"


def _opts(values, selected, blank: str = "") -> str:
    return (f'<option value="">{esc(blank)}</option>' if blank else "") + "".join(
        f'<option value="{esc(v)}"{" selected" if v == selected else ""}>{esc(v)}</option>' for v in values)


def _tabs(on: str) -> str:
    return ('<div class="head"><h1>Ideas and tasks</h1><nav class="seg" aria-label="Ideas and tasks">'
            f'<a href="/web/ideas"{' aria-current="page"' if on == "thoughts" else ""}>Thoughts</a>'
            f'<a href="/web/ideas/todoist"{' aria-current="page"' if on == "todoist" else ""}>Todoist</a></nav></div>')


# ---- thoughts --------------------------------------------------------------------------------------

def _filters(q) -> dict:
    f = {k: str(q.get(k, ""))[:60] for k in ("type", "area", "stage", "tag", "q", "open")}
    f["type"] = f["type"] if f["type"] in THOUGHT_TYPES else ""
    f["area"] = f["area"] if f["area"] in AREAS else ""
    f["stage"] = f["stage"] if f["stage"] in STAGES + ("all",) else "open"
    return f


def _thought_detail(cur, csrf: str, t: dict, back: str) -> str:
    cur.execute("SELECT update_text, event_at FROM thought_updates WHERE thought_id = %s ORDER BY event_at, id", (t["id"],))
    ups = "".join(f'<li><span class="small">{u["event_at"]:%d.%m.}</span> {esc(u["update_text"])}</li>' for u in cur.fetchall())
    base = hidden("csrf", csrf) + hidden("id", str(t["id"])) + hidden("back", back)
    stages = "".join(f'<button class="ghost sm" type="submit" name="stage" value="{s}">{lbl}</button>'
                     for s, lbl in STAGE_BUTTONS if s != t["stage"])
    return (f'<div class="fbox stack"><p class="quote">&ldquo;{esc(t["original_text"] or t["summary"])}&rdquo; '
            f'<span class="small">your words, {t["logged_at"]:%d.%m.%Y}</span></p>'
            + (f'<ul class="updates">{ups}</ul>' if ups else "")
            + f'<form class="row" method="post" action="/web/ideas/thought">{base}{hidden("act", "stage")}<span class="small">Move to</span>{stages}</form>'
            f'<form method="post" action="/web/ideas/thought">{base}{hidden("act", "save")}<div class="fields">'
            f'<label>Type<select name="thought_type">{_opts(THOUGHT_TYPES, t["thought_type"])}</select></label>'
            f'<label>Area<select name="area">{_opts(AREAS, t["area"], "none")}</select></label>'
            f'<label>Stage<select name="stage">{_opts(STAGES, t["stage"])}</select></label>'
            f'<label>Next step<select name="next_step">{_opts(NEXT_STEPS, t["next_step"], "none")}</select></label></div>'
            f'<div class="fields wide"><label>Summary<input name="summary" value="{esc(t["summary"])}" maxlength="300" required></label>'
            f'<label>Tags <span class="small">(comma between them)</span><input name="tags" value="{esc(", ".join(t["tags"] or []))}" maxlength="200"></label></div>'
            '<p class="row"><button class="primary sm" type="submit">Save</button><span class="small">Save is your yes.</span></p></form>'
            f'<form class="row" method="post" action="/web/ideas/thought">{base}{hidden("act", "delete")}'
            '<button class="ghost sm bad" type="submit">Delete</button><span class="small">asks once</span></form></div>')


def render_thoughts(csrf: str, q) -> str:
    f = _filters(q)
    where, args = [], []
    if f["type"]:
        where.append("thought_type = %s"); args.append(f["type"])
    if f["area"]:
        where.append("area = %s"); args.append(f["area"])
    if f["stage"] == "open":
        where.append("stage IN ('inbox','active')")
    elif f["stage"] != "all":
        where.append("stage = %s"); args.append(f["stage"])
    if f["tag"]:
        where.append("%s = ANY(tags)"); args.append(f["tag"])
    if f["q"]:
        where.append("(summary ILIKE %s OR original_text ILIKE %s)"); args += [f"%{f['q']}%"] * 2
    here = {k: f[k] for k in ("type", "area", "tag", "q")} | {"stage": "" if f["stage"] == "open" else f["stage"]}
    back = url("/web/ideas", **here, open=f["open"])
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT unnest(tags) AS t FROM thoughts ORDER BY 1")
        tags = [r["t"] for r in cur.fetchall()]
        cur.execute(f"""SELECT id, summary, original_text, thought_type, area, stage, tags, next_step, logged_at, needs_review
                        FROM thoughts {"WHERE " + " AND ".join(where) if where else ""}
                        ORDER BY logged_at DESC, id DESC LIMIT %s""", args + [LIST_LIMIT])  # where: fixed texts only
        rows = cur.fetchall()
        items = []
        for t in rows:
            open_ = f["open"] == str(t["id"])
            link = url("/web/ideas", **here, open="" if open_ else str(t["id"]))
            meta = " · ".join(x for x in (t["area"], f'{t["logged_at"]:%d.%m.}', f'next: {t["next_step"]}' if t["next_step"] else "",
                                          " ".join("#" + x for x in (t["tags"] or []))) if x)
            items.append(f'<div class="th{" sel" if open_ else ""}" id="t{t["id"]}"><a class="top" href="{esc(link)}#t{t["id"]}">'
                         f'<span class="pill">{esc(t["thought_type"] or "thought")}</span><span class="sum">{esc(t["summary"])}'
                         f'<span class="meta">{esc(meta)}</span></span><span class="pill{" ok" if t["stage"] == "active" else ""}">{t["stage"]}</span></a>'
                         + (_thought_detail(cur, csrf, t, back) if open_ else "") + "</div>")
    stage_opts = '<option value="">open (inbox + active)</option>' + _opts(STAGES + ("all",), f["stage"] if f["stage"] != "open" else None)
    filters = (f'<form class="row" method="get" action="/web/ideas"><input type="search" name="q" value="{esc(f["q"])}" class="grow" '
               'placeholder="Search your thoughts" aria-label="Search">'
               f'<select name="type" aria-label="Type" class="autosubmit">{_opts(THOUGHT_TYPES, f["type"], "all types")}</select>'
               f'<select name="area" aria-label="Area" class="autosubmit">{_opts(AREAS, f["area"], "all areas")}</select>'
               f'<select name="stage" aria-label="Stage" class="autosubmit">{stage_opts}</select>'
               f'<select name="tag" aria-label="Tag" class="autosubmit">{_opts(tags, f["tag"], "all tags")}</select>'
               '<button class="ghost sm" type="submit">Show</button></form>')
    more = f'<p class="small">Showing the newest {LIST_LIMIT}; narrow the filters to find older ones.</p>' if len(rows) == LIST_LIMIT else ""
    return (_tabs("thoughts") + f'<div class="card">{filters}<h2>{len(rows)} {"thought" if len(rows) == 1 else "thoughts"}</h2>'
            f'{"".join(items) or "<p class=empty>Nothing matches these filters.</p>"}{more}'
            '<p class="small hint">Click a thought to see your words and change it. The stage buttons work straight away.</p></div>')


# ---- Todoist ---------------------------------------------------------------------------------------

def places() -> list[tuple[str, str, str]]:
    """Where a task can be, as (key, name): ("s:<section id>", "Lists › <section>") for each section of Lists, then
    ("p:<project id>", "Lists") for Lists without a section and ("p:<project id>", "Inbox")."""
    secs = todos._sections(todos.LISTS)
    return ([(f"s:{sid}", f"Lists › {name}") for sid, name in sorted(secs.items(), key=lambda x: x[1].lower())]
            + [(f"p:{todos.LISTS}", "Lists"), (f"p:{todos.INBOX}", "Inbox")])


def place_of(t: dict) -> str:
    if t.get("section_id"):
        return f"s:{t['section_id']}"
    return f"p:{t['project_id']}"


def due_of(t: dict):
    d = (t.get("due") or {}).get("date") if isinstance(t.get("due"), dict) else None
    try:
        return date.fromisoformat(str(d)[:10]) if d else None
    except ValueError:
        return None


def _due_chip(d, today: date) -> str:
    if not d:
        return ""
    n = (d - today).days
    if n < 0:
        return f'<span class="due late">overdue · {d:%d.%m.}</span>'
    if n == 0:
        return '<span class="due today">today</span>'
    if n == 1:
        return '<span class="due">tomorrow</span>'
    return f'<span class="due">{d:%a %d.%m.}</span>' if n < 7 else f'<span class="due">{d:%d.%m.}</span>'


def open_tasks() -> list[dict]:
    return [t for proj in (todos.LISTS, todos.INBOX) for t in todos._open(proj)]


def _task_row(csrf: str, t: dict, names: dict, today: date, ours: bool, edit: str, back: str, show_place: bool) -> str:
    base = hidden("csrf", csrf) + hidden("task", t["id"]) + hidden("back", back)
    where = names.get(place_of(t), "Lists")
    tick = (f'<form method="post" action="/web/ideas/task">{base}{hidden("act", "close")}'
            f'<button class="tick" type="submit" aria-label="Tick off {esc(todos._en(t["content"]))}" title="Tick off"></button></form>')
    extra = ""
    if edit == t["id"]:
        opts = "".join(f'<option value="{esc(k)}"{" selected" if k == place_of(t) else ""}>{esc(n)}</option>' for k, n in names.items())
        extra = (f'<form class="fbox" method="post" action="/web/ideas/task">{base}{hidden("act", "edit")}<div class="fields wide2">'
                 f'<label>Text<input name="content" value="{esc(t["content"])}" maxlength="500" required></label>'
                 f'<label>Section<select name="place">{opts}</select></label></div>'
                 f'<p class="row"><button class="primary sm" type="submit">Save</button><a class="ghost sm dim" href="{esc(back)}">Cancel</a>'
                 '<span class="small">You see the change first, then Yes. Due dates are set in Todoist.</span></p></form>')
    link = url("/web/ideas/todoist", sort=_sort_of(back), edit="" if edit == t["id"] else t["id"])
    delete = (f'<form method="post" action="/web/ideas/task">{base}{hidden("act", "delete")}'
              '<button class="icon" type="submit" title="Delete (only tasks the logger added)" aria-label="Delete">&times;</button></form>' if ours else "")
    return (f'<div class="task" id="k{esc(t["id"])}">{tick}<div class="txt">{esc(todos._en(t["content"]))} {_due_chip(due_of(t), today)}'
            + (f' <span class="small">· {esc(where)}</span>' if show_place else "")
            + (' <span class="small">· added by the logger</span>' if ours else "") + f'{extra}</div>'
            f'<span class="acts"><a class="icon" href="{esc(link)}#k{esc(t["id"])}" title="Change text or section" aria-label="Change">&#9998;</a>{delete}</span></div>')


def _sort_of(back: str) -> str:
    return "due" if "sort=due" in back else ""


def render_todoist(csrf: str, q) -> str:
    sort = "due" if q.get("sort") == "due" else ""
    edit = str(q.get("edit", ""))[:40]
    back = url("/web/ideas/todoist", sort=sort)
    try:
        tasks = open_tasks()
        names = dict(places())
    except TodoistError as e:
        return _tabs("todoist") + f'<div class="card"><p class="err">Todoist did not answer ({esc(e)}). Your thoughts still work; try again in a moment.</p></div>'
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        ours = {t["id"] for t in tasks if todos._ours(cur, t["id"])}
        cur.execute("""SELECT DISTINCT ON (task_id) task_id, content, place FROM todo_actions
                       WHERE op = 'closed' AND undone_at IS NULL AND batch LIKE 'web-%%' AND at > now() - interval '18 hours'
                       ORDER BY task_id, at DESC""")
        closed = [r for r in cur.fetchall() if r["task_id"] not in {t["id"] for t in tasks}]
    far = date(9999, 1, 1)
    if sort == "due":
        body = "".join(_task_row(csrf, t, names, today, t["id"] in ours, edit, back, True)
                       for t in sorted(tasks, key=lambda t: (due_of(t) or far, todos._en(t["content"]).lower())))
    else:
        groups: dict = {}
        for t in tasks:
            groups.setdefault(place_of(t), []).append(t)
        body = ""
        for key, name in names.items():
            ts = sorted(groups.get(key, []), key=lambda t: (due_of(t) or far, todos._en(t["content"]).lower()))
            if ts:
                body += f'<div class="sect"><h3>{esc(name)} <span class="small">· {len(ts)}</span></h3>' + "".join(
                    _task_row(csrf, t, names, today, t["id"] in ours, edit, back, False) for t in ts) + "</div>"
    done = "".join(f'<div class="task closed"><form method="post" action="/web/ideas/task">{hidden("csrf", csrf)}{hidden("task", r["task_id"])}'
                   f'{hidden("back", back)}{hidden("act", "reopen")}<button class="tick on" type="submit" title="Put it back on the list" '
                   f'aria-label="Put {esc(todos._en(r["content"] or ""))} back on the list"></button></form>'
                   f'<div class="txt">{esc(todos._en(r["content"] or ""))} <span class="small">· ticked off today</span></div></div>' for r in closed)
    seg = (f'<nav class="seg" aria-label="Sort"><a href="/web/ideas/todoist"{' aria-current="page"' if not sort else ""}>By section</a>'
           f'<a href="/web/ideas/todoist?sort=due"{' aria-current="page"' if sort else ""}>By due date</a></nav>')
    return (_tabs("todoist") + f'<div class="card"><div class="head sub"><h2>Open tasks <span class="small">· {len(tasks)} · Lists and Inbox</span></h2>{seg}</div>'
            f'{body or "<p class=empty>No open tasks.</p>"}'
            + (f'<div class="sect"><h3>Ticked off today</h3>{done}</div>' if done else "")
            + '<p class="small hint">Tick off works straight away; a task ticked off here stays below until tomorrow, so one click puts it back. '
            'To add a task, type it in the box above. Shopping stays in Todoist and the chat. Todoist is read again after every change, '
            'otherwise every 10 minutes.</p></div>')
