"""What the Settings page's forms do. Each takes (form, key, session) and returns the reply shown under the entry box.
Save is the owner's yes here (as on the Health card); every change leaves an audit row with what kind of change it was, never values."""
import re
from datetime import date
from typing import Optional

from . import logbook, nudges, settingspage, uiprefs
from .db import connect

SID = re.compile(r"[0-9a-f-]{36}")


def _rejected(reason: str) -> dict:
    return {"outcome": "rejected", "reason": reason}


def _number(s) -> tuple[bool, Optional[float]]:
    """(ok, value): an empty field is fine and means None; a number must be 0 to 100000."""
    s = str(s or "").strip().replace(",", ".")
    if not s:
        return True, None
    try:
        v = float(s)
    except ValueError:
        return False, None
    return (0 <= v <= 100000 and v == v), v


def _int(s, lo: int, hi: int, default: int) -> int:
    try:
        return min(hi, max(lo, int(str(s).strip())))
    except ValueError:
        return default


# ---- devices ---------------------------------------------------------------------------------------

def device(form, key: Optional[str], session) -> dict:
    """Sign one other device out."""
    sid = str(form.get("sid", ""))
    if not SID.fullmatch(sid):
        return _rejected("Unknown device.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT token_hash FROM web_sessions WHERE sid = %s", (sid,))
        row = cur.fetchone()
        if not row:
            return {"outcome": "unchanged", "reason": "That device is already signed out."}
        if row["token_hash"] == session["token_hash"]:
            return _rejected("This is the device you are using; use Sign out at the top.")
        cur.execute("DELETE FROM web_sessions WHERE sid = %s", (sid,))
        logbook._audit(cur, "web_change", "changed", {"what": "device signed out"}, logbook.WEB)
    return {"outcome": "changed", "head": "Signed that device out."}


def others(form, key: Optional[str], session) -> dict:
    """Sign every other device out."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM web_sessions WHERE token_hash <> %s", (session["token_hash"],))
        n = cur.rowcount
        logbook._audit(cur, "web_change", "changed", {"what": "other devices signed out", "n": n}, logbook.WEB)
    return {"outcome": "changed", "head": f"Signed out {n} other device{'' if n == 1 else 's'}." if n else "No other device was signed in."}


# ---- food targets ----------------------------------------------------------------------------------

def targets(form, key: Optional[str], session) -> dict:
    """New targets from a date (a row of food_targets; an old row of the same date is replaced, earlier days keep theirs)."""
    try:
        valid_from = date.fromisoformat(str(form.get("valid_from", "")).strip())
    except ValueError:
        return _rejected("Give the date the targets start on.")
    vals = {}
    for k, label, _u in settingspage.TARGET_FIELDS:
        ok, v = _number(form.get(k))
        if not ok:
            return _rejected(f"{label}: give a number from 0 to 100000, or leave it empty.")
        vals[k] = v
    if all(v is None for v in vals.values()):
        return _rejected("Give at least one target.")
    note = " ".join(str(form.get("note", "")).split())[:200] or None
    cols = list(vals)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"""INSERT INTO food_targets (valid_from, {", ".join(cols)}, note) VALUES (%s, {", ".join(["%s"] * len(cols))}, %s)
                        ON CONFLICT (valid_from) DO UPDATE SET {", ".join(f"{c} = EXCLUDED.{c}" for c in cols)}, note = EXCLUDED.note""",
                    [valid_from] + [vals[c] for c in cols] + [note])  # column names are the fixed list above
        logbook._audit(cur, "web_change", "changed", {"what": "food targets"}, logbook.WEB)
    shown = ", ".join(f"{label} {vals[k]:g}{' ' + u if u == 'mg' else ''}" for k, label, u in settingspage.TARGET_FIELDS[:5] if vals[k] is not None)
    return {"outcome": "changed", "head": f"Targets from {valid_from:%d.%m.%Y} saved: {shown}."}


# ---- habits ----------------------------------------------------------------------------------------

def habit(form, key: Optional[str], session) -> dict:
    name = str(form.get("name", ""))[:40]
    label = " ".join(str(form.get("label", "")).split())[:40]
    if not label:
        return _rejected("A habit needs a name to show.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE log_types SET label = %s, daily_target = %s, sort_order = %s, active = %s
                       WHERE name = %s AND kind = 'event' AND on_checklist""",
                    (label, _int(form.get("daily_target"), 1, 10, 1), _int(form.get("sort_order"), 0, 999, 100), form.get("active") == "1", name))
        if not cur.rowcount:
            return _rejected("Unknown habit.")
        logbook._audit(cur, "web_change", "changed", {"what": "habit settings"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Saved the habit {label}." + ("" if form.get("active") == "1" else " It is retired: it no longer shows anywhere.")}


def habit_new(form, key: Optional[str], session) -> dict:
    label = " ".join(str(form.get("label", "")).split())[:40]
    name = re.sub(r"[^a-z0-9]+", "_", label.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("_")[:40]
    if not label or not name:
        return _rejected("Give the habit a name with letters or digits.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT active, on_checklist FROM log_types WHERE name = %s OR lower(label) = lower(%s)", (name, label))
        old = cur.fetchone()
        if old:
            return _rejected(f"There is already a log type called {label}" + (" (a retired habit: switch Active on above)." if old["on_checklist"] and not old["active"] else "."))
        cur.execute("SELECT coalesce(max(sort_order), 0) + 10 AS n FROM log_types WHERE on_checklist")
        order = min(cur.fetchone()["n"], 999)
        cur.execute("""INSERT INTO log_types (name, label, kind, category, on_checklist, daily_target, active, sort_order)
                       VALUES (%s, %s, 'event', 'daily', true, %s, true, %s)""", (name, label, _int(form.get("daily_target"), 1, 10, 1), order))
        logbook._audit(cur, "web_change", "changed", {"what": "habit added"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Added the habit {label}. Log it by its name or tick it on Today."}


# ---- the Today page --------------------------------------------------------------------------------

def today(form, key: Optional[str], session) -> dict:
    with connect() as c, c.cursor() as cur:
        groups = settingspage.today_items(cur)
    hidden_items, order = [], {}
    for kind, _heading, items in groups:
        pos = {}
        for n, (i, _lbl) in enumerate(items):
            if form.get(f"show_{kind}_{i}") != "1":
                hidden_items.append(f"{kind}:{i}")
            pos[i] = (_int(form.get(f"pos_{kind}_{i}"), 1, 99, n + 1), n)
        order[kind] = [i for i, _p in sorted(pos.items(), key=lambda kv: kv[1])]
    uiprefs.save(hidden_items, order)
    with connect() as conn, conn.cursor() as cur:
        logbook._audit(cur, "web_change", "changed", {"what": "today page"}, logbook.WEB)
    return {"outcome": "changed", "head": "Saved. The Today page shows what you chose." if hidden_items else "Saved. The Today page shows everything."}


def today_reset(form, key: Optional[str], session) -> dict:
    uiprefs.reset()
    return {"outcome": "changed", "head": "The Today page shows everything again, in the default order."}


# ---- notifications ---------------------------------------------------------------------------------

def _hours(s, lo: float, hi: float, default: float) -> float:
    ok, v = _number(s)
    return default if not ok or v is None else round(min(hi, max(lo, v)) * 2) / 2  # half hours


def nudge_settings(form, key: Optional[str], session) -> dict:
    d = nudges.DEFAULTS
    values = {"on": form.get("on") == "1", "weight": form.get("weight") == "1", "habits": form.get("habits") == "1",
              "idle": form.get("idle") == "1", "weight_after": _hours(form.get("weight_after"), 0.5, 12, d["weight_after"]),
              "habits_after": _hours(form.get("habits_after"), 1, 20, d["habits_after"]), "idle_hours": _hours(form.get("idle_hours"), 1, 12, d["idle_hours"])}
    nudges.save(values)
    with connect() as conn, conn.cursor() as cur:
        logbook._audit(cur, "web_change", "changed", {"what": "nudges"}, logbook.WEB)
    return {"outcome": "changed", "head": "Saved. Nudges are on." if values["on"] else "Saved. Nudges are off."}


def nudge_test(form, key: Optional[str], session) -> dict:
    n = nudges.deliver({"title": "Logger", "body": "A test notification. Nudges will look like this.", "url": "/web/settings?tab=notify", "tag": "test"})
    if not n:
        return _rejected("No device took the test notification. Turn notifications on again on the phone, then try once more.")
    return {"outcome": "changed", "head": f"Test notification sent to {n} device{'' if n == 1 else 's'}."}


def push_off(form, key: Optional[str], session) -> dict:
    pid = str(form.get("id", ""))
    if not pid.isdigit():
        return _rejected("Unknown device.")
    with connect() as c:
        gone = c.execute("DELETE FROM push_subscriptions WHERE id = %s RETURNING id", (int(pid),)).fetchone()
    return {"outcome": "changed", "head": "Notifications are off for that device."} if gone else _rejected("That device is not there any more.")


ACTIONS = {"device": device, "others": others, "targets": targets, "habit": habit, "habit_new": habit_new, "today": today, "today_reset": today_reset,
           "nudges": nudge_settings, "nudge_test": nudge_test, "push_off": push_off}
