"""What the Ideas and tasks page's forms do. Thoughts change through the logger's own change function (v1's checks):
Save is the yes for the user's own fields and the stage buttons work at once with an Undo button (decided 26.10.2026);
a deletion is shown first. Todoist: tick off at once with Undo (recorded in todo_actions like the chat's); a changed
text or section is shown first, then sent after the Yes; only tasks the logger added can be deleted."""
import re
import secrets
from datetime import date
from typing import Optional

from . import ideaspage, logbook, todos
from .db import connect
from .todaypage import AREAS, THOUGHT_TYPES
from .todoist import TodoistError
from .webactions import _change, _rejected
from .webfood import _again

TODOIST_DOWN = "Todoist did not answer, so nothing changed. Try again in a moment."


def _undo(action: str, fields: dict, back: str) -> dict:
    return {"action": action, "label": "Undo", "no": back, "no_label": "Close", "fields": fields}


# ---- thoughts --------------------------------------------------------------------------------------

def thought(form, key: Optional[str]) -> dict:
    tid, act = str(form.get("id", "")), form.get("act")
    back = ideaspage.safe_back(form.get("back"))
    if not tid.isdigit():
        return _rejected("Unknown thought.")
    with connect() as c:
        t = c.execute("SELECT id, summary, stage, thought_type, area, next_step, tags FROM thoughts WHERE id = %s", (int(tid),)).fetchone()
    if not t:
        return _rejected("That thought is not there any more.")
    if act == "stage":
        stage = form.get("stage")
        if stage not in ideaspage.STAGES:
            return _rejected("Unknown stage.")
        out = _change([{"action": "change", "args": {"id": tid, "fields": {"stage": stage}}}], "thought stage",
                      f'Moved "{t["summary"]}" from {t["stage"]} to {stage}.')
        if out["outcome"] == "changed" and stage != t["stage"]:
            out["confirm"] = _undo("/web/ideas/thought", {"id": tid, "act": "stage", "stage": t["stage"], "back": back}, back)
        return out
    if act == "delete":
        if form.get("confirmed") != "1":
            return {"outcome": "needs_confirmation", "read_back": f'Delete thought {tid}: "{t["summary"]}"?',
                    "confirm": {"action": "/web/ideas/thought", "label": "Yes, delete", "no": back, "fields": _again(form) | {"confirmed": "1"}}}
        return _change([{"action": "change", "args": {"id": tid, "delete": True}}], "thought delete", f'Deleted "{t["summary"]}".')
    if act != "save":
        return _rejected("Unknown action.")
    summary = " ".join(str(form.get("summary", "")).split())[:300]
    if not summary:
        return _rejected("The summary cannot be empty.")
    tt, area, stage, nxt = form.get("thought_type", ""), form.get("area", ""), form.get("stage", ""), form.get("next_step", "")
    if tt not in THOUGHT_TYPES or stage not in ideaspage.STAGES or (area and area not in AREAS) or (nxt and nxt not in ideaspage.NEXT_STEPS):
        return _rejected("Pick a type, stage, area and next step from the lists.")
    tags = []
    for x in str(form.get("tags", "")).split(","):
        x = " ".join(x.split()).lower().lstrip("#")[:40]
        if x and x not in tags:
            tags.append(x)
    new = {"summary": summary, "thought_type": tt, "area": area or None, "stage": stage, "next_step": nxt or None, "tags": tags[:12]}
    fields = {k: v for k, v in new.items() if v != (t[k] if k != "tags" else list(t["tags"] or []))}
    if not fields:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    return _change([{"action": "change", "args": {"id": tid, "fields": fields}}], "thought save",
                   f"Saved: {', '.join(k.replace('_', ' ') for k in fields)} of thought {tid}.")


# ---- Todoist ---------------------------------------------------------------------------------------

def _find(task_id: str):
    """The open task of Lists or the Inbox with this id (shopping is not changed from here)."""
    return next((t for t in ideaspage.open_tasks() if t["id"] == task_id), None)


def task(form, key: Optional[str]) -> dict:
    tid, act = str(form.get("task", ""))[:40], form.get("act")
    back = ideaspage.safe_back(form.get("back"))
    if not re.fullmatch(r"[\w-]{1,40}", tid):
        return _rejected("Unknown task.")
    try:
        if act == "reopen":
            return _reopen(tid, back)
        t = _find(tid)
        if not t:
            return _rejected("That task is not open in Lists or the Inbox any more.")
        name = todos._en(t["content"])
        names = dict(ideaspage.places())
        if act == "close":
            todos.client().close(tid)
            with connect() as conn, conn.cursor() as cur:
                todos._record(cur, f"web-{secrets.token_hex(4)}", None, "closed", name, t, names.get(ideaspage.place_of(t)))
                logbook._audit(cur, "web_todo", "closed", {"task": tid}, logbook.WEB)
            todos.forget()
            return {"outcome": "changed", "head": f'Ticked off "{name}" in Todoist.',
                    "confirm": _undo("/web/ideas/task", {"task": tid, "act": "reopen", "back": back}, back)}
        if act == "delete":
            with connect() as c:
                ours = todos._ours(c.cursor(), tid)
            if not ours:
                return _rejected("Only tasks the logger added in the last 24 hours can be deleted here; tick it off instead.")
            if form.get("confirmed") != "1":
                return {"outcome": "needs_confirmation", "read_back": f'Delete "{name}" from Todoist?',
                        "confirm": {"action": "/web/ideas/task", "label": "Yes, delete", "no": back, "fields": _again(form) | {"confirmed": "1"}}}
            todos.client().delete(tid)
            with connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE todo_actions SET undone_at = now() WHERE task_id = %s AND op = 'created' AND undone_at IS NULL", (tid,))
                logbook._audit(cur, "web_todo", "deleted", {"task": tid}, logbook.WEB)
            todos.forget()
            return {"outcome": "changed", "head": f'Deleted "{name}" from Todoist.'}
        if act != "edit":
            return _rejected("Unknown action.")
        content = " ".join(str(form.get("content", "")).split())[:500]
        place = str(form.get("place", ""))
        if not content:
            return _rejected("The task's text cannot be empty.")
        if place not in names:
            return _rejected("Pick a section from the list.")
        old_due, new_due = ideaspage.due_of(t), ideaspage.due_of(t)
        if "due" in form and not ideaspage.due_lock(t):  # a form without the field (older page, other client) leaves the date alone
            if form.get("no_due") == "1":
                new_due = None
            elif str(form.get("due", "")).strip():
                try:
                    new_due = date.fromisoformat(str(form.get("due")).strip())
                except ValueError:
                    return _rejected("Give the due date as a date.")
        lines = []
        if content != t["content"]:
            lines.append(f'Text: "{t["content"]}" → "{content}"')
        if place != ideaspage.place_of(t):
            lines.append(f"Section: {names.get(ideaspage.place_of(t), 'Lists')} → {names[place]}")
        if new_due != old_due:
            lines.append(f"Due date: {old_due:%d.%m.%Y} → " if old_due else "Due date: none → ")
            lines[-1] += f"{new_due:%d.%m.%Y}" if new_due else "none"
        if not lines:
            return {"outcome": "unchanged", "reason": "Nothing changed."}
        if form.get("confirmed") != "1":
            return {"outcome": "needs_confirmation", "read_back": "Change this task in Todoist?\n" + "\n".join(lines),
                    "confirm": {"action": "/web/ideas/task", "label": "Yes, change", "no": back, "fields": _again(form) | {"confirmed": "1"}}}
        if content != t["content"]:
            todos.client().update(tid, content)
        if place != ideaspage.place_of(t):
            kind, pid = place.split(":", 1)
            todos.client().move(tid, section_id=pid if kind == "s" else None, project_id=pid if kind == "p" else None)
        if new_due != old_due:
            todos.client().set_due(tid, new_due.isoformat() if new_due else None)
        with connect() as conn, conn.cursor() as cur:
            logbook._audit(cur, "web_todo", "changed", {"task": tid, "text": content != t["content"], "moved": place != ideaspage.place_of(t),
                                                        "due": new_due != old_due}, logbook.WEB)
        todos.forget()
        return {"outcome": "changed", "head": "Changed in Todoist: " + "; ".join(lines) + "."}
    except TodoistError:
        todos.forget()
        return _rejected(TODOIST_DOWN)


def _reopen(tid: str, back: str) -> dict:
    """Undo of a tick-off made here: only a task this page ticked off and nothing has put back since."""
    with connect() as c:
        a = c.execute("""SELECT id, content FROM todo_actions WHERE task_id = %s AND op = 'closed' AND undone_at IS NULL
                         AND batch LIKE 'web-%%' ORDER BY at DESC LIMIT 1""", (tid,)).fetchone()
    if not a:
        return _rejected("Only a task ticked off here can be put back from here.")
    todos.client().reopen(tid)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE todo_actions SET undone_at = now() WHERE id = %s", (a["id"],))
        logbook._audit(cur, "web_todo", "reopened", {"task": tid}, logbook.WEB)
    todos.forget()
    return {"outcome": "changed", "head": f'"{todos._en(a["content"] or "")}" is back on the list.'}


# Every form of the page: path under /web/ideas/, the function, where the browser goes (None = the page it came from)
ACTIONS = {"thought": (thought, None), "task": (task, None)}
