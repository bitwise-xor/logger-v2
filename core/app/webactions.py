"""What the web app's buttons and fields do. New entries are the parser's answer built from the button (no AI call to
read it) and go through logbook.log like a typed message. Corrections, unticks and review actions go through the
logger's own change function (commands._run_changes, v1's checks and read-backs); pressing Save is the user's yes
(decided 26.10.2026), so nothing is previewed first. Each returns the reply shown under the entry box."""
import re
from datetime import date, datetime, timedelta
from typing import Optional

from . import commands, habitspage, healthpage, logbook
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


# ---- undo ------------------------------------------------------------------------------------------

def undo(raw_id: str) -> dict:
    """The Undo button under a reply: the chat's own undo, but only while that message is still the newest one that
    can be undone (the chat's undo always takes the newest). A look at what it would do comes first, in a savepoint."""
    def refused(why: str) -> dict:
        return {**_rejected(why), "head": "Nothing undone."}

    if not raw_id.isdigit():
        return refused("Unknown message.")
    step = {"action": "undo", "args": {}}
    now = datetime.now(BERLIN)
    with connect() as conn, conn.cursor() as cur:
        try:
            seen = " ".join(commands._preview(cur, [step], now))
        except Reject as e:
            return refused(str(e))
        found = re.search(r"undid raw #(\d+)", seen)
        if not found or found[1] != raw_id:
            return refused("Something newer was logged since, so nothing was undone. Open the Food log to change an entry, "
                           "or type \"undo\" to undo the newest one.")
        try:
            out = commands._run_changes(cur, [step], now)
        except Reject as e:
            conn.rollback()
            return refused(str(e))
        logbook._audit(cur, "web_change", "changed", {"what": "undo", "ids": [int(raw_id)]}, logbook.WEB)
    return {"outcome": "changed", "head": "Undone.", "result": "\n".join(out)}


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


def habit_day(name: str, day: str, key: Optional[str]) -> dict:
    """The Habits page's squares: a click on a day with fewer ticks than the habit's daily count adds one tick for that day
    (date only, no time); a click on a day that is done takes that day's ticks off. Not for the Wake-up habit, which is a time."""
    with connect() as c:
        t = c.execute("SELECT name, label, daily_target FROM log_types WHERE name = %s AND kind = 'event' AND on_checklist AND active", (name,)).fetchone()
        today = c.execute("SELECT log_day(now()) AS d").fetchone()["d"]
    if not t or name in habitspage.LOCKED:
        return _rejected("That habit cannot be changed for a past day here.")
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return _rejected("Unknown day.")
    if d > today or d < today - timedelta(days=habitspage.DAYS + 5):
        return _rejected("Only the last 30 days can be changed here.")
    with connect() as c:
        ids = [r["id"] for r in c.execute("""SELECT e.id FROM events e JOIN log_types lt ON lt.id = e.type_id WHERE lt.name = %s AND e.day = %s
                                             ORDER BY e.event_at, e.id""", (name, d)).fetchall()]
    if len(ids) < (t["daily_target"] or 1):
        pred = _pred(health=[{"type": name, "value": None, "value2": None, "period": None, "session": None, "when": d.isoformat()}])
        # The time of the click makes each message different: the same words twice within 30 minutes (tick, take off, tick again)
        # would otherwise stop at the "log it again?" question.
        return logbook.log(f"tick {name} on {d:%d.%m.%Y} (clicked {datetime.now(BERLIN):%H:%M:%S.%f})", key, pred, channel=logbook.WEB)
    steps = [{"action": "change", "args": {"id": f"e{i}", "delete": True}} for i in ids]
    return _change(steps, "untick day", f"Removed {len(ids)} tick{'' if len(ids) == 1 else 's'} of {t['label']} on {d:%d.%m.%Y}.")


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


# ---- health page: one day's row --------------------------------------------------------------------

_FIELDS = ("w", "bm1", "bm2", "be1", "be2", "s", "bed", "wake")


def _when(day: date, col: str, t: str) -> str:
    """The date and time for the change function or the parser answer; a bed time from noon on is the evening before."""
    on = day - timedelta(days=1) if col == "bed" and int(t[:2]) >= 12 else day
    return f"{on:%d.%m.%Y} {t}"


def health_day(day_s: str, r: str, form, key: Optional[str]) -> dict:
    """Save one row of the Health page: changed values are corrected, cleared ones deleted (after one yes), empty cells
    that got a value are logged for that day. Pressing Save is the yes for corrections; odd values still ask."""
    try:
        day = date.fromisoformat(day_s)
    except ValueError:
        return _rejected("Unknown day.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        if not today - timedelta(days=365) <= day <= today:
            return _rejected("Pick a day of the last 12 months.")
        now_vals = healthpage.day_values(cur, day, day).get(day, {})
    dm = f"{day:%a %d.%m.}"
    steps, items, words, said, deletes = [], [], [], [], []
    for col, _h, _s, ltype, period, kind in healthpage.COLS:
        name, cur_v = healthpage.NAMES[col], now_vals.get(col)
        sent = [form.get(f"{col}1"), form.get(f"{col}2")] if kind == 2 else [form.get(col)]
        if any(x is None for x in sent):
            continue  # not part of this form
        sent = [str(x).strip() for x in sent]
        if kind == "t":
            m = TIME.match(sent[0]) if sent[0] else None
            if sent[0] and not m:
                return _rejected(f"{name}: give the time as hh:mm.")
            new, nums = [f"{int(m[1]):02d}:{m[2]}" if m else ""], None
        else:
            nums = [_num(x) if x else None for x in sent]
            if any(x and v is None for x, v in zip(sent, nums)):
                return _rejected(f"{name}: give a number.")
            new = [healthpage.plain(v) if v is not None else "" for v in nums]
        old = cur_v["v"] if cur_v else [""] * len(new)
        if new == old:
            continue
        if all(x == "" for x in new):
            if cur_v:
                steps.append({"action": "change", "args": {"id": cur_v["id"], "delete": True}})
                deletes.append(f"{name} {'/'.join(old)}")
                said.append(f"{name} {'/'.join(old)} deleted")
            continue
        if any(x == "" for x in new):
            return _rejected(f"{name}: give both numbers.")
        shown = "/".join(new)
        if cur_v:
            fields = {"when": _when(day, col, new[0])} if kind == "t" else {"value": nums[0], **({"value2": nums[1]} if kind == 2 else {})}
            if form.get("confirmed") == "1":
                fields["confirmed"] = True
            steps.append({"action": "change", "args": {"id": cur_v["id"], "fields": fields}})
            said.append(f"{name} {'/'.join(old)} → {shown}")
        else:
            when = _when(day, col, new[0]) if kind == "t" else (None if day == today else f"{day:%d.%m.%Y}")
            items.append({"type": ltype, "value": nums[0] if nums else None, "value2": nums[1] if kind == 2 else None,
                          "period": period, "session": None, "when": when})
            words.append({"weight": f"weight {shown}", "bp": f"bp {shown} {period}", "blood_sugar": f"sugar {shown} fasted",
                          "wake": f"woke up {new[0]}", "bed": f"went to bed {new[0]}"}[ltype]
                         + ("" if kind == "t" or day == today else f" on {day:%d.%m.%Y}"))
            said.append(f"{name} {shown} added")
    if not steps and not items:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    again = {k: str(form.get(k)) for k in _FIELDS if form.get(k) is not None} | {"day": day.isoformat(), "r": r}
    back = f"/web/health?r={r}"
    if deletes and form.get("confirm_del") != "1":
        return {"outcome": "needs_confirmation", "read_back": f"Delete on {dm}: {', '.join(deletes)}?",
                "confirm": {"action": "/web/healthday", "label": "Yes, delete", "no": back,
                            "fields": again | {"confirm_del": "1", **({"confirmed": "1"} if form.get("confirmed") == "1" else {})}}}
    head = f"Saved for {dm}: {', '.join(said)}."
    if steps:
        out = _change(steps, "health day", head)
        if out["outcome"] == "needs_confirmation":
            out["confirm"] = {"action": "/web/healthday", "label": "Yes, save it", "no": back,
                              "fields": again | {"confirmed": "1", **({"confirm_del": "1"} if deletes else {})}}
        if out["outcome"] != "changed" or not items:
            return out
    out = logbook.log(", ".join(words), key, _pred(health=items), channel=logbook.WEB)
    if out["outcome"] == "saved" and not out.get("questions"):
        out["head"] = head
    return out


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
