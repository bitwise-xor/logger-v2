"""The Gym section's data and actions (02.11.2026, step (a) of the plan in the doc "Logger v2: status and web app plan",
section "Gym page plan"): the owner's exercises, a tick that logs an exercise into today's workout (one workout per logging
day, started by the first tick), removing it again, and Undo for both. A tick writes a raw_inbox row ("gym: Bench press
3 × 8 at 60 kg") so Recent shows it, and ticks the habit "gym" once a logging day. Each action takes (form, key, session)
like the Habits page's forms and returns the reply shown under the entry box; the click is the owner's yes."""
import hashlib
import json

from psycopg.types.json import Jsonb

from . import logbook
from .db import connect

KINDS = {"weights": "Weights", "bodyweight": "Bodyweight", "cardio": "Machine cardio"}
EXTRACTOR = "v2-gym"
NUMBERS = "Numbers only: sets 1 to 50, reps 1 to 500, kg 0 to 1000, minutes up to 600, km up to 200."
UNDO_TABLES = ("workouts", "workout_sets", "events")  # the order rows are put back in


def _n(x) -> str:
    return format(float(x), "g")


def fmt(kind: str, sets=None, reps=None, kg=None, minutes=None, km=None, **_) -> str:
    """One row as the owner reads it: "3 × 8 at 60 kg", "3 × 12", "25 min · 3.2 km"."""
    if kind == "cardio":
        return " · ".join(x for x in (f"{_n(minutes)} min" if minutes is not None else "", f"{_n(km)} km" if km is not None else "") if x)
    out = f"{sets} × {reps}" if sets and reps else ""
    return out + (f" at {_n(kg)} kg" if kind == "weights" and kg is not None else "")


def row_text(r: dict) -> str:
    """fmt() of a workout_sets row joined with its exercise's kind."""
    return fmt(r["kind"], r["sets"], r["reps"], r["kg"], r["minutes"], r["km"])


def _num(v, lo: float, hi: float, whole: bool = False):
    s = str(v or "").strip().replace(",", ".")
    if not s:
        return None
    x = float(s)  # ValueError for words
    if not lo <= x <= hi or (whole and x != int(x)):
        raise ValueError(NUMBERS)
    return int(x) if whole else x


def values(kind: str, form) -> dict:
    """The numbers of one row from a form, checked for the exercise's kind. ValueError with the reason."""
    try:
        if kind == "cardio":
            minutes, km = _num(form.get("minutes"), 0.1, 600), _num(form.get("km"), 0, 200)
            if minutes is None:
                raise ValueError("Give the minutes.")
            return {"sets": None, "reps": None, "kg": None, "minutes": minutes, "km": km}
        sets, reps = _num(form.get("sets"), 1, 50, True), _num(form.get("reps"), 1, 500, True)
        kg = _num(form.get("kg"), 0, 1000) if kind == "weights" else None
    except ValueError as e:
        raise ValueError(str(e) if str(e) in (NUMBERS, "Give the minutes.") else NUMBERS) from e
    if sets is None or reps is None:
        raise ValueError("Give sets and reps.")
    if kind == "weights" and kg is None:
        raise ValueError("Give the weight in kg.")
    return {"sets": sets, "reps": reps, "kg": kg, "minutes": None, "km": None}


def _rejected(reason: str) -> dict:
    return {"outcome": "rejected", "reason": reason}


def _id(v) -> int:
    s = str(v or "")[:12]
    return int(s) if s.isdigit() else 0


def tick(form, key=None, session=None) -> dict:
    """Log one exercise into today's workout with the numbers sent; the first tick of the day starts the workout and ticks
    the habit "gym". The form's key stops a double tap from logging it twice."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM exercises WHERE id = %s", (_id(form.get("exercise")),))
        ex = cur.fetchone()
        if not ex:
            return _rejected("Unknown exercise.")
        try:
            v = values(ex["kind"], form)
        except ValueError as e:
            return _rejected(str(e))
        said = f'{ex["name"]} {fmt(ex["kind"], **v)}'
        req = hashlib.sha256(json.dumps([ex["id"], v], sort_keys=True).encode()).hexdigest()
        if key:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
            cur.execute("SELECT request_hash, response FROM idempotency_keys WHERE key = %s", (key,))
            seen = cur.fetchone()
            if seen:
                return {**seen["response"], "replayed": True} if seen["request_hash"] == req else _rejected("This form was already used.")
        cur.execute("SELECT log_day(now()) AS d")
        day = cur.fetchone()["d"]
        cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('web', %s, %s) RETURNING id", (f"gym: {said}", Jsonb({"gym": True})))
        raw = cur.fetchone()["id"]
        cur.execute("SELECT set_config('app.raw_id', %s, true)", (str(raw),))  # change_log ties the habit tick to this message
        made: dict = {"workout_sets": [], "workouts": [], "events": []}
        cur.execute("SELECT id FROM workouts WHERE day = %s ORDER BY id LIMIT 1", (day,))
        w = cur.fetchone()
        if not w:
            cur.execute("INSERT INTO workouts (raw_id, day, started_at) VALUES (%s, %s, now()) RETURNING id", (raw, day))
            w = cur.fetchone()
            made["workouts"].append(w["id"])
        cur.execute("""INSERT INTO workout_sets (workout_id, exercise_id, raw_id, sets, reps, kg, minutes, km, sort_order)
                       SELECT %s, %s, %s, %s, %s, %s, %s, %s, coalesce(max(sort_order), 0) + 1 FROM workout_sets WHERE workout_id = %s
                       RETURNING id""", (w["id"], ex["id"], raw, v["sets"], v["reps"], v["kg"], v["minutes"], v["km"], w["id"]))
        made["workout_sets"].append(cur.fetchone()["id"])
        cur.execute("""SELECT t.id, EXISTS (SELECT 1 FROM events e WHERE e.type_id = t.id AND e.day = %s) AS done
                       FROM log_types t WHERE t.name = 'gym' AND t.kind = 'event' AND t.active""", (day,))
        habit = cur.fetchone()
        if habit and not habit["done"]:
            cur.execute("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                           VALUES (%s, %s, %s, now(), true, %s) RETURNING id""", (raw, habit["id"], day, EXTRACTOR))
            made["events"].append(cur.fetchone()["id"])
        out = {"outcome": "saved", "head": f"Saved: {said}", "gym_undo": {"delete": made}}
        if key:
            cur.execute("INSERT INTO idempotency_keys (key, tool, request_hash, response) VALUES (%s, 'gym', %s, %s)", (key, req, Jsonb(out)))
        logbook._audit(cur, "gym", "saved", {"raw_id": raw, "kind": ex["kind"], "started": bool(made["workouts"])}, logbook.WEB)
    return out


def remove(form, key=None, session=None) -> dict:
    """Take a row off today's workout (set=id), or all of an exercise's rows today (exercise=id). The last row of the day
    also takes the workout and the day's gym tick with it. Undo puts everything back."""
    sid, eid = _id(form.get("set")), _id(form.get("exercise"))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT s.id, s.workout_id, w.day, x.name, x.kind, s.sets, s.reps, s.kg, s.minutes, s.km FROM workout_sets s
                       JOIN workouts w ON w.id = s.workout_id JOIN exercises x ON x.id = s.exercise_id
                       WHERE w.day = log_day(now()) AND (s.id = %s OR s.exercise_id = %s) ORDER BY s.id""", (sid, eid))
        rows = cur.fetchall()
        if not rows:
            return _rejected("That is not in today's workout.")
        snap = []
        for table, ids in (("workout_sets", [r["id"] for r in rows]),):
            cur.execute(f"SELECT to_jsonb(x) AS j FROM {table} x WHERE id = ANY(%s)", (ids,))
            snap += [{"t": table, "row": r["j"]} for r in cur.fetchall()]
            cur.execute(f"DELETE FROM {table} WHERE id = ANY(%s)", (ids,))
        wid, day = rows[0]["workout_id"], rows[0]["day"]
        cur.execute("SELECT 1 FROM workout_sets WHERE workout_id = %s LIMIT 1", (wid,))
        if not cur.fetchone():  # nothing left today: no workout, no gym tick
            cur.execute("SELECT to_jsonb(x) AS j FROM workouts x WHERE id = %s", (wid,))
            snap.append({"t": "workouts", "row": cur.fetchone()["j"]})
            cur.execute("""SELECT to_jsonb(e) AS j FROM events e JOIN log_types t ON t.id = e.type_id
                           WHERE t.name = 'gym' AND e.day = %s AND e.extractor_version = %s""", (day, EXTRACTOR))
            gone = [r["j"] for r in cur.fetchall()]
            snap += [{"t": "events", "row": j} for j in gone]
            cur.execute("DELETE FROM events WHERE id = ANY(%s)", ([j["id"] for j in gone],))
            cur.execute("DELETE FROM workouts WHERE id = %s", (wid,))
        logbook._audit(cur, "gym", "removed", {"rows": len(rows)}, logbook.WEB)
    said = ", ".join(f'{r["name"]} {row_text(r)}' for r in rows)
    return {"outcome": "changed", "head": f"Removed: {said}"[:160], "gym_undo": {"restore": snap}}


def undo(g: dict) -> dict:
    """Undo of a tick (its rows deleted) or of a remove (the rows put back with the same ids)."""
    with connect() as conn, conn.cursor() as cur:
        if g.get("delete"):
            d = g["delete"]
            cur.execute("DELETE FROM events WHERE id = ANY(%s) AND extractor_version = %s", (d.get("events") or [], EXTRACTOR))
            cur.execute("DELETE FROM workout_sets WHERE id = ANY(%s)", (d.get("workout_sets") or [],))
            cur.execute("DELETE FROM workouts w WHERE id = ANY(%s) AND NOT EXISTS (SELECT 1 FROM workout_sets s WHERE s.workout_id = w.id)",
                        (d.get("workouts") or [],))
            head = "Undone: the exercise is off today's workout."
        else:
            rows = sorted(g.get("restore") or [], key=lambda r: UNDO_TABLES.index(r["t"]) if r["t"] in UNDO_TABLES else 9)
            for item in rows:
                if item["t"] not in UNDO_TABLES:
                    continue
                cur.execute(f"INSERT INTO {item['t']} SELECT * FROM jsonb_populate_record(NULL::{item['t']}, %s) ON CONFLICT DO NOTHING",
                            (Jsonb(item["row"]),))
            head = "Undone: it is back in today's workout."
        logbook._audit(cur, "gym", "undone", {}, logbook.WEB)
    return {"outcome": "changed", "head": head}


def _name(v) -> str:
    return " ".join(str(v or "").split())[:60]


def new_exercise(form, key=None, session=None) -> dict:
    """A new exercise (or a retired one of the same name brought back): name, kind, other words (comma-separated)."""
    name, kind = _name(form.get("name")), str(form.get("kind", ""))
    if not name:
        return _rejected("Type the exercise's name.")
    words = [w for w in (" ".join(x.split())[:40] for x in str(form.get("words", "")).split(",")) if w][:10]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM exercises WHERE lower(name) = lower(%s)", (name,))
        old = cur.fetchone()
        if old and old["active"]:
            return _rejected(f'{old["name"]} is already in your list.')
        if old:
            cur.execute("UPDATE exercises SET active = true WHERE id = %s", (old["id"],))
            logbook._audit(cur, "gym", "changed", {"what": "exercise back"}, logbook.WEB)
            return {"outcome": "changed", "head": f'{old["name"]} is back in your list.'}
        if kind not in KINDS:
            return _rejected("Pick weights, bodyweight or machine cardio.")
        cur.execute("INSERT INTO exercises (name, kind, words, sort_order) SELECT %s, %s, %s, coalesce(max(sort_order), 0) + 10 FROM exercises",
                    (name, kind, words))
        logbook._audit(cur, "gym", "changed", {"what": "new exercise", "kind": kind}, logbook.WEB)
    return {"outcome": "changed", "head": f"Added {name} ({KINDS[kind].lower()})."}


def active(form, key=None, session=None) -> dict:
    """Retire an exercise (it leaves today's list; its history stays) or bring it back."""
    on = form.get("on") == "1"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE exercises SET active = %s WHERE id = %s RETURNING name", (on, _id(form.get("exercise"))))
        r = cur.fetchone()
        if not r:
            return _rejected("Unknown exercise.")
        logbook._audit(cur, "gym", "changed", {"what": "exercise back" if on else "exercise retired"}, logbook.WEB)
    return {"outcome": "changed", "head": f'{r["name"]} is back in your list.' if on else f'Retired {r["name"]}. Its history stays.'}


ACTIONS = {"tick": tick, "remove": remove, "new": new_exercise, "active": active}
