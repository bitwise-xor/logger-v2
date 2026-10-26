"""What the web app's buttons and fields do. New entries are the parser's answer built from the button (no AI call to
read it) and go through logbook.log like a typed message. Corrections, unticks and review actions go through the
logger's own change function (commands._run_changes, v1's checks and read-backs); pressing Save is the user's yes
(decided 26.10.2026), so nothing is previewed first. Each returns the reply shown under the entry box."""
import re
from datetime import datetime
from typing import Optional

from . import commands, logbook
from .db import connect
from .todaypage import AREAS, HEALTH_ROWS, THOUGHT_TYPES
from .v1_log import Reject, _words_in
from .when import BERLIN

TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _pred(**kw) -> dict:
    return {"intent": "log", "command_text": None, "private": None, "private_unsure": False, "food": [], "usual_meals": [],
            "health": [], "thoughts": [], "todos": [], **kw}


def _num(s: str) -> Optional[float]:
    try:
        v = float(str(s).strip().replace(",", "."))
    except ValueError:
        return None
    return v if v == v and abs(v) < 1e6 else None  # no NaN, no absurd numbers


def _rejected(reason: str) -> dict:
    return {"outcome": "rejected", "reason": reason}


def _change(steps: list[dict], what: str, ok: str) -> dict:
    """Run changes through the logger's own change function, in one transaction, with an audit row (ids only)."""
    with connect() as conn, conn.cursor() as cur:
        try:
            commands._run_changes(cur, steps, datetime.now(BERLIN))
        except Reject as e:
            conn.rollback()
            with conn.cursor() as cur2:
                logbook._audit(cur2, "web_change", "rejected", {"what": what, "ids": [s["args"].get("id") for s in steps]}, logbook.WEB)
            msg = str(e)
            if msg.startswith("READ BACK"):
                return {"outcome": "needs_confirmation", "read_back": msg.split(". Ask the user")[0].replace("READ BACK, nothing changed: ", "")}
            return _rejected(msg)
        logbook._audit(cur, "web_change", "changed", {"what": what, "ids": [s["args"].get("id") for s in steps]}, logbook.WEB)
    return {"outcome": "changed", "head": ok}


# ---- habits ----------------------------------------------------------------------------------------

def habit(name: str, action: str, eid: str, key: Optional[str]) -> dict:
    with connect() as c:
        t = c.execute("SELECT name, label FROM log_types WHERE name = %s AND kind = 'event' AND on_checklist AND active", (name,)).fetchone()
    if not t:
        return _rejected("That habit is not on the checklist.")
    if action == "tick":
        pred = _pred(health=[{"type": t["name"], "value": None, "value2": None, "period": None, "session": None, "when": None}])
        return logbook.log(f"tick {t['name']}", key, pred, channel=logbook.WEB)
    if action != "untick" or not eid.isdigit():
        return _rejected("Unknown action.")
    with connect() as c:  # the tick to remove: of this habit, from today
        row = c.execute("""SELECT e.id FROM events e JOIN log_types lt ON lt.id = e.type_id
                           WHERE e.id = %s AND lt.name = %s AND e.day = log_day(now())""", (int(eid), t["name"])).fetchone()
    if not row:
        return {"outcome": "unchanged", "reason": f"{t['label']} has no tick from today to remove."}
    return _change([{"action": "change", "args": {"id": f"e{row['id']}", "delete": True}}], "untick", f"Removed the {t['label']} tick.")


# ---- usual meals -----------------------------------------------------------------------------------

def usual(meal_id: str, form, key: Optional[str]) -> dict:
    if not meal_id.isdigit():
        return _rejected("Unknown usual meal.")
    with connect() as c:
        meal = c.execute("SELECT id, name FROM usual_meals WHERE id = %s", (int(meal_id),)).fetchone()
        items = c.execute("""SELECT i.id, i.grams, f.name, f.basis FROM usual_meal_items i JOIN foods f ON f.id = i.food_id
                             WHERE i.meal_id = %s ORDER BY i.sort_order, i.id""", (int(meal_id),)).fetchall() if meal else []
    if not meal or not items:
        return _rejected("Unknown usual meal.")
    skip, changes, said = [], [], []
    for i in items:
        if not form.get(f"on_{i['id']}"):
            skip.append(i["name"])
            said.append(f"no {i['name']}")
            continue
        g = _num(form.get(f"g_{i['id']}", ""))
        if g is None or not 0 < g <= 5000:
            return _rejected(f"{i['name']}: give an amount above 0 and at most 5000 {i['basis']}.")
        if abs(g - float(i["grams"])) > 1e-9:
            changes.append({"food": i["name"], "grams": g})
            said.append(f"{i['name']} {format(g, 'g')} {i['basis']}")
    if len(skip) == len(items):
        return _rejected("Tick at least one item.")
    for term in skip + [ch["food"] for ch in changes]:  # the logger matches items by their words: refuse anything ambiguous
        if sum(1 for i in items if _words_in(term, i["name"])) != 1:
            return _rejected(f"'{term}' matches more than one item of {meal['name']}; change it by typing in the box.")
    t = str(form.get("time", "")).strip()
    when = t if TIME.match(t) else None
    text = f"my usual {meal['name']}" + (f" ({', '.join(said)})" if said else "") + (f" at {when}" if when else "")
    pred = _pred(usual_meals=[{"name": meal["name"], "skip": skip, "changes": changes, "when": when}])
    return logbook.log(text, key, pred, channel=logbook.WEB)


# ---- health ----------------------------------------------------------------------------------------

_ROWS = {k: (label, sub, t, period) for k, label, sub, t, period, _u in HEALTH_ROWS}


def health(row: str, ref: str, form, key: Optional[str]) -> dict:
    if row not in _ROWS:
        return _rejected("Unknown row.")
    label, sub, ltype, period = _ROWS[row]
    name = f"{label} {sub}".strip()
    if row == "wake":
        t = str(form.get("t", "")).strip()
        if not TIME.match(t):
            return _rejected("Give the time as hh:mm.")
        vals, fields, said = None, {"when": t}, t
    else:
        v1 = _num(form.get("v1", ""))
        v2 = _num(form.get("v2", "")) if ltype == "bp" else None
        if v1 is None or (ltype == "bp" and v2 is None):
            return _rejected(f"{name}: give " + ("both numbers." if ltype == "bp" else "a number."))
        vals = (v1, v2)
        fields = {"value": v1, **({"value2": v2} if ltype == "bp" else {})}
        said = f"{format(v1, 'g')}/{format(v2, 'g')}" if ltype == "bp" else format(v1, "g")
    if ref:  # a correction of today's entry: Save is the yes
        if not re.fullmatch(r"[me]\d+", ref):
            return _rejected("Unknown entry.")
        with connect() as c:
            table = "measurements" if ref[0] == "m" else "events"
            ok = c.execute(f"""SELECT 1 FROM {table} x JOIN log_types lt ON lt.id = x.type_id
                               WHERE x.id = %s AND lt.name = %s""", (int(ref[1:]), ltype)).fetchone()  # table is one of two fixed names
        if not ok:
            return _rejected("That entry is not there any more.")
        if form.get("confirmed") == "1":
            fields["confirmed"] = True
        out = _change([{"action": "change", "args": {"id": ref, "fields": fields}}], f"health {row}", f"{name} changed to {said}.")
        if out["outcome"] == "needs_confirmation":
            out["confirm"] = {"action": "/web/health", "label": "Yes, save it",
                              "fields": {"row": row, "id": ref, "confirmed": "1", **({"t": said} if row == "wake" else {"v1": form.get("v1", ""), "v2": form.get("v2", "")})}}
        return out
    item = {"type": ltype, "value": vals[0] if vals else None, "value2": vals[1] if vals else None,
            "period": period, "session": None, "when": fields.get("when")}
    words = {"weight": f"weight {said}", "bp": f"bp {said} {period}", "blood_sugar": f"sugar {said} fasted", "wake": f"woke up {said}"}[ltype]
    return logbook.log(words, key, _pred(health=[item]), channel=logbook.WEB)


# ---- review ----------------------------------------------------------------------------------------

def review(ref: str, act: str, form) -> dict:
    m = re.fullmatch(r"([tfme])(\d+)", ref or "")
    if not m or act not in ("ok", "change", "delete"):
        return _rejected("Unknown item.")
    kind, n = m[1], int(m[2])
    cid = str(n) if kind == "t" else ref  # the change function takes a thought by its number
    if act == "delete":
        return _change([{"action": "change", "args": {"id": cid, "delete": True}}], "review delete", "Deleted.")
    fields: dict = {"needs_review": False}
    if act == "change":
        if kind == "t":
            tt, area = form.get("thought_type", ""), form.get("area", "")
            if tt not in THOUGHT_TYPES or area not in AREAS:
                return _rejected("Pick a type and an area.")
            fields.update(thought_type=tt, area=area)
        elif kind == "f":
            a = _num(form.get("amount", ""))
            if a is None:
                return _rejected("Give the amount as a number.")
            fields["amount"] = a
        elif kind == "m":
            v1 = _num(form.get("v1", ""))
            if v1 is None:
                return _rejected("Give the value as a number.")
            fields["value"] = v1
            if form.get("v2") is not None:
                v2 = _num(form.get("v2", ""))
                if v2 is None:
                    return _rejected("Give both values as numbers.")
                fields["value2"] = v2
        else:
            t = str(form.get("t", "")).strip()
            if not TIME.match(t):
                return _rejected("Give the time as hh:mm.")
            with connect() as c:  # the new time is on the entry's own date, not today
                row = c.execute("SELECT event_at FROM events WHERE id = %s", (n,)).fetchone()
            if not row:
                return _rejected("That entry is not there any more.")
            fields["when"] = f"{row['event_at'].astimezone(BERLIN):%d.%m.%Y} {t}"
    return _change([{"action": "change", "args": {"id": cid, "fields": fields}}], f"review {act}",
                   "Kept. The flag is cleared." if act == "ok" else "Changed. The flag is cleared.")
