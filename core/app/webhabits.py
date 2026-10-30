"""What the Habits page's manager forms do (30.10.2026): track or stop tracking a habit, retire it and bring it back, rename it, delete a
habit that was never ticked, add a new one. Each takes (form, key, session) and returns the reply shown under the entry box. The click
is the owner's yes here; every change leaves an audit row with what kind of change it was, never values. Wake-up and Bedtime are
times, not habits (category sleep): these forms refuse them."""
import re

from . import logbook
from .db import connect
from .habitspage import GROUPS
from .websettings import _int, _rejected

CATEGORIES = {k for k, _v in GROUPS}


def _habit(cur, name: str):
    cur.execute("SELECT name, label, category, on_checklist, active FROM log_types WHERE name = %s AND kind = 'event' AND category <> 'sleep'",
                (str(name)[:64],))
    return cur.fetchone()


def _label(form) -> str:
    return " ".join(str(form.get("label", "")).split())[:40]


def track(form, key, session) -> dict:
    on = form.get("on") == "1"
    with connect() as conn, conn.cursor() as cur:
        t = _habit(cur, form.get("name", ""))
        if not t:
            return _rejected("Unknown habit.")
        if not t["active"]:
            return _rejected(f"{t['label']} is retired: bring it back first.")
        if on:  # a newly tracked habit goes last on Today
            cur.execute("SELECT coalesce(max(sort_order), 0) + 10 AS n FROM log_types WHERE on_checklist")
            cur.execute("UPDATE log_types SET on_checklist = true, sort_order = %s WHERE name = %s", (min(cur.fetchone()["n"], 999), t["name"]))
        else:
            cur.execute("UPDATE log_types SET on_checklist = false WHERE name = %s", (t["name"],))
        logbook._audit(cur, "web_change", "changed", {"what": "habit tracking"}, logbook.WEB)
    if on:
        return {"outcome": "changed", "head": f"Now tracking {t['label']}. It shows on Today and in the calendar."}
    return {"outcome": "changed", "head": f"Stopped tracking {t['label']}. Its history stays and you can still log it in the chat."}


def retire(form, key, session) -> dict:
    with connect() as conn, conn.cursor() as cur:
        t = _habit(cur, form.get("name", ""))
        if not t:
            return _rejected("Unknown habit.")
        cur.execute("UPDATE log_types SET active = false WHERE name = %s", (t["name"],))
        logbook._audit(cur, "web_change", "changed", {"what": "habit retired"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Retired {t['label']}. Its history stays; it is hidden everywhere. Find it under Retired to bring it back."}


def restore(form, key, session) -> dict:
    with connect() as conn, conn.cursor() as cur:
        t = _habit(cur, form.get("name", ""))
        if not t:
            return _rejected("Unknown habit.")
        cur.execute("UPDATE log_types SET active = true WHERE name = %s", (t["name"],))
        logbook._audit(cur, "web_change", "changed", {"what": "habit brought back"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Brought back {t['label']}" + (" (tracked again)." if t["on_checklist"] else " (not tracked: switch Track on to start).")}


def edit(form, key, session) -> dict:
    label = _label(form)
    if not label:
        return _rejected("A habit needs a name to show.")
    with connect() as conn, conn.cursor() as cur:
        t = _habit(cur, form.get("name", ""))
        if not t:
            return _rejected("Unknown habit.")
        cur.execute("SELECT 1 FROM log_types WHERE lower(label) = lower(%s) AND name <> %s", (label, t["name"]))
        if cur.fetchone():
            return _rejected(f"There is already a log type called {label}.")
        cur.execute("UPDATE log_types SET label = %s, daily_target = %s WHERE name = %s", (label, _int(form.get("daily_target"), 1, 10, 1), t["name"]))
        logbook._audit(cur, "web_change", "changed", {"what": "habit settings"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Saved the habit {label}."}


def delete(form, key, session) -> dict:
    if form.get("confirmed") != "1":
        return _rejected("Press Yes, delete to delete a habit.")
    with connect() as conn, conn.cursor() as cur:
        t = _habit(cur, form.get("name", ""))
        if not t:
            return _rejected("Unknown habit.")
        cur.execute("""SELECT (SELECT count(*) FROM events e WHERE e.type_id = lt.id) + (SELECT count(*) FROM measurements m WHERE m.type_id = lt.id) AS n
                       FROM log_types lt WHERE lt.name = %s""", (t["name"],))
        if cur.fetchone()["n"]:
            return _rejected(f"{t['label']} has history, so it cannot be deleted. Retire it instead.")
        cur.execute("DELETE FROM log_types WHERE name = %s", (t["name"],))
        logbook._audit(cur, "web_change", "changed", {"what": "habit deleted"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Deleted the habit {t['label']}."}


def new(form, key, session) -> dict:
    label = _label(form)
    name = re.sub(r"[^a-z0-9]+", "_", label.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("_")[:40]
    if not label or not name:
        return _rejected("Give the habit a name with letters or digits.")
    group = str(form.get("group", "daily"))
    if group not in CATEGORIES:
        return _rejected("Choose a group for the habit.")
    track_it = form.get("track") == "1"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT active FROM log_types WHERE name = %s OR lower(label) = lower(%s)", (name, label))
        old = cur.fetchone()
        if old:
            return _rejected(f"There is already a log type called {label}" + ("" if old["active"] else " (retired: bring it back from the Retired list)") + ".")
        cur.execute("SELECT coalesce(max(sort_order), 0) + 10 AS n FROM log_types WHERE kind = 'event'")
        order = min(cur.fetchone()["n"], 999)
        cur.execute("""INSERT INTO log_types (name, label, kind, category, aliases, on_checklist, daily_target, active, sort_order)
                       VALUES (%s, %s, 'event', %s, %s, %s, %s, true, %s)""",
                    (name, label, group, [label.lower()], track_it, _int(form.get("daily_target"), 1, 10, 1), order))
        logbook._audit(cur, "web_change", "changed", {"what": "habit added"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Added the habit {label}. " + ("It is tracked: tick it above or log it by its name." if track_it
                                                                         else "It is in the list, not tracked yet: switch Track on to start.")}


ACTIONS = {"track": track, "retire": retire, "restore": restore, "edit": edit, "delete": delete, "new": new}
