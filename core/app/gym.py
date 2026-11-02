"""The Gym section's data and actions (02.11.2026, step (a) of the plan in the doc "Logger v2: status and web app plan",
section "Gym page plan"): the owner's exercises, a tick that logs an exercise into today's workout (one workout per logging
day, started by the first tick), removing it again, and Undo for both. A tick writes a raw_inbox row ("gym: Bench press
3 × 8 at 60 kg") so Recent shows it, and ticks the habit "gym" once a logging day. Each action takes (form, key, session)
like the Habits page's forms and returns the reply shown under the entry box; the click is the owner's yes.
Step (b), 02.11.2026: the split (workout_days and their exercises, edited on the Plan tab) and today's day: the first active day
not done yet this week (Monday starts at Day 1 again; once every day is done, Day 1 again), unless the owner picked another day
or Free workout for today."""
import hashlib
import json
from datetime import timedelta

from psycopg.types.json import Jsonb

from . import logbook
from .db import connect

KINDS = {"weights": "Weights", "bodyweight": "Bodyweight", "cardio": "Machine cardio"}
EXTRACTOR = "v2-gym"
NUMBERS = "Numbers only: sets 1 to 50, reps 1 to 500, kg 0 to 1000, minutes up to 600, km up to 200."
UNDO_TABLES = ("workouts", "workout_sets", "events", "workout_day_items")  # the order rows are put back in
PICK = "gym_pick"  # ui_settings: the day picked by hand for one logging day, {"day": "2026-11-02", "split": 3} (null = Free workout)


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


def days(cur, retired: bool = False) -> list[dict]:
    """The split's days in order: the active ones (Day 1, Day 2, ...), or the removed ones."""
    cur.execute("SELECT * FROM workout_days WHERE active <> %s ORDER BY sort_order, id", (retired,))
    return cur.fetchall()


def label(ds: list[dict], d: dict) -> str:
    """ "Day 2 · Pull": the number is the day's place among the active days."""
    i = next((i for i, x in enumerate(ds) if x["id"] == d["id"]), None)
    return d["name"] if i is None else f"Day {i + 1} · {d['name']}"


def split(cur, today) -> dict:
    """Today's day of the split. Today's workout keeps the day it was logged under; before the first tick a day picked by hand
    for today wins; else the first active day not done yet this week. "day" is None for Free workout or when there is no split.
    "done" maps a day's id to the first date this week (before today) it was done."""
    ds = days(cur)
    monday = today - timedelta(days=today.weekday())
    cur.execute("SELECT day, split_day_id FROM workouts WHERE day BETWEEN %s AND %s ORDER BY day, id", (monday, today))
    week = cur.fetchall()
    done: dict = {}
    for w in week:
        if w["day"] < today and w["split_day_id"]:
            done.setdefault(w["split_day_id"], w["day"])
    auto = next((d for d in ds if d["id"] not in done), ds[0] if ds else None)
    now = next((w for w in week if w["day"] == today), None)
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (PICK,))
    p = cur.fetchone()
    p = p["value"] if p and p["value"].get("day") == today.isoformat() else None
    byid = {d["id"]: d for d in ds}
    if now is not None:
        day = byid.get(now["split_day_id"])
    elif p is not None:
        day = byid.get(p.get("split"), auto if p.get("split") else None)
    else:
        day = auto
    n = len({w["day"] for w in week if w["day"] < today}) + 1
    return {"days": ds, "day": day, "free": bool(ds) and day is None, "auto": auto, "hand": bool(ds) and day is not auto,
            "done": done, "nth": n, "started": now is not None}


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
            sd = split(cur, day)["day"]
            cur.execute("INSERT INTO workouts (raw_id, day, started_at, split_day_id) VALUES (%s, %s, now(), %s) RETURNING id",
                        (raw, day, sd and sd["id"]))
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
            head = g.get("back") or "Undone: it is back in today's workout."
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


def rename(form, key=None, session=None) -> dict:
    """Rename an exercise (owner's ask, 02.11.2026): its past workouts, plan days and progress stay with it, since they point
    at its id; the old name is kept as one of its other words."""
    name = _name(form.get("name"))
    if not name:
        return _rejected("Type the exercise's name.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM exercises WHERE id = %s", (_id(form.get("exercise")),))
        x = cur.fetchone()
        if not x:
            return _rejected("Unknown exercise.")
        if name == x["name"]:
            return _rejected(f"It is called {name} already.")
        cur.execute("SELECT name, active FROM exercises WHERE lower(name) = lower(%s) AND id <> %s", (name, x["id"]))
        o = cur.fetchone()
        if o:
            return _rejected(f'{o["name"]} is already in your list.' if o["active"] else f'A retired exercise is called {o["name"]}.')
        words = [w for w in x["words"] if w.lower() != name.lower()]
        if x["name"].lower() != name.lower() and x["name"].lower() not in (w.lower() for w in words):
            words = words[:9] + [x["name"]]
        cur.execute("UPDATE exercises SET name = %s, words = %s WHERE id = %s", (name, words, x["id"]))
        logbook._audit(cur, "gym", "changed", {"what": "exercise renamed"}, logbook.WEB)
    return {"outcome": "changed", "head": f'Renamed {x["name"]} to {name}. Its history moved with it.'}


def _today(cur):
    cur.execute("SELECT log_day(now()) AS d")
    return cur.fetchone()["d"]


def pick(form, key=None, session=None) -> dict:
    """Switch today's day (split=id) or pick Free workout (split=free). Today's workout, once started, moves with it."""
    raw = str(form.get("split", ""))
    with connect() as conn, conn.cursor() as cur:
        today, ds = _today(cur), days(cur)
        d = None if raw == "free" else next((x for x in ds if x["id"] == _id(raw)), False)
        if d is False:
            return _rejected("Unknown day.")
        cur.execute("UPDATE workouts SET split_day_id = %s WHERE day = %s", (d and d["id"], today))
        cur.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                    (PICK, Jsonb({"day": today.isoformat(), "split": d and d["id"]})))
        logbook._audit(cur, "gym", "changed", {"what": "day picked"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Today: {label(ds, d) if d else 'Free workout'}."}


def _plan_numbers(form) -> tuple:
    """The plan's sets × reps of an exercise on a day: both or neither. ValueError with the reason."""
    try:
        sets, reps = _num(form.get("sets"), 1, 50, True), _num(form.get("reps"), 1, 500, True)
    except ValueError as e:
        raise ValueError("Numbers only: sets 1 to 50, reps 1 to 500.") from e
    if (sets is None) != (reps is None):
        raise ValueError("Give both sets and reps, or neither.")
    return sets, reps


def day_new(form, key=None, session=None) -> dict:
    """A new day at the end of the split."""
    name = _name(form.get("name"))[:40]
    if not name:
        return _rejected("Type the day's name, e.g. Push.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM workout_days WHERE active AND lower(name) = lower(%s)", (name,))
        if cur.fetchone():
            return _rejected(f"{name} is already a day of your split.")
        cur.execute("INSERT INTO workout_days (name, sort_order) SELECT %s, coalesce(max(sort_order), 0) + 10 FROM workout_days RETURNING id", (name,))
        logbook._audit(cur, "gym", "changed", {"what": "new day"}, logbook.WEB)
        ds = days(cur)
    return {"outcome": "changed", "head": f"Added {label(ds, ds[-1])}. Add its exercises below."}


def day_edit(form, key=None, session=None) -> dict:
    """Rename a day."""
    name = _name(form.get("name"))[:40]
    if not name:
        return _rejected("Type the day's name.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE workout_days SET name = %s WHERE id = %s RETURNING id", (name, _id(form.get("day"))))
        if not cur.fetchone():
            return _rejected("Unknown day.")
        logbook._audit(cur, "gym", "changed", {"what": "day renamed"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Renamed to {name}."}


def day_active(form, key=None, session=None) -> dict:
    """Remove a day from the split (it is hidden; past workouts keep its name) or bring it back at the end."""
    on = form.get("on") == "1"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE workout_days SET active = %s, sort_order = CASE WHEN %s THEN (SELECT coalesce(max(sort_order), 0) + 10
                       FROM workout_days) ELSE sort_order END WHERE id = %s RETURNING name""", (on, on, _id(form.get("day"))))
        r = cur.fetchone()
        if not r:
            return _rejected("Unknown day.")
        logbook._audit(cur, "gym", "changed", {"what": "day back" if on else "day removed"}, logbook.WEB)
    return {"outcome": "changed", "head": f'{r["name"]} is back in your split.' if on else
            f'Removed {r["name"]} from your split. Past workouts keep its name; "Removed days" below brings it back.'}


def item_add(form, key=None, session=None) -> dict:
    """Put an exercise on a day, at the end, with the plan's sets × reps (optional; used the first time it is done)."""
    try:
        sets, reps = _plan_numbers(form)
    except ValueError as e:
        return _rejected(str(e))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM workout_days WHERE id = %s AND active", (_id(form.get("day")),))
        d = cur.fetchone()
        cur.execute("SELECT * FROM exercises WHERE id = %s AND active", (_id(form.get("exercise")),))
        x = cur.fetchone()
        if not d or not x:
            return _rejected("Pick a day and one of your exercises.")
        cur.execute("SELECT 1 FROM workout_day_items WHERE day_id = %s AND exercise_id = %s", (d["id"], x["id"]))
        if cur.fetchone():
            return _rejected(f'{x["name"]} is already on {d["name"]}.')
        cur.execute("""INSERT INTO workout_day_items (day_id, exercise_id, sets, reps, sort_order)
                       SELECT %s, %s, %s, %s, coalesce(max(sort_order), 0) + 10 FROM workout_day_items WHERE day_id = %s""",
                    (d["id"], x["id"], sets, reps, d["id"]))
        logbook._audit(cur, "gym", "changed", {"what": "exercise on a day"}, logbook.WEB)
    return {"outcome": "changed", "head": f'Added {x["name"]} to {d["name"]}.'}


def item_set(form, key=None, session=None) -> dict:
    """Change the plan's sets × reps of an exercise on a day (empty = none)."""
    try:
        sets, reps = _plan_numbers(form)
    except ValueError as e:
        return _rejected(str(e))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE workout_day_items i SET sets = %s, reps = %s FROM exercises x
                       WHERE i.id = %s AND x.id = i.exercise_id RETURNING x.name""", (sets, reps, _id(form.get("item"))))
        r = cur.fetchone()
        if not r:
            return _rejected("That is not on a day any more.")
        logbook._audit(cur, "gym", "changed", {"what": "plan numbers"}, logbook.WEB)
    return {"outcome": "changed", "head": f'{r["name"]}: plan {fmt("bodyweight", sets, reps) or "without numbers"}.'}


def item_move(form, key=None, session=None) -> dict:
    """Move an exercise one place up or down on its day."""
    iid, up = _id(form.get("item")), form.get("dir") == "up"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT i.id, x.name FROM workout_day_items i JOIN exercises x ON x.id = i.exercise_id
                       WHERE i.day_id = (SELECT day_id FROM workout_day_items WHERE id = %s) ORDER BY i.sort_order, i.id""", (iid,))
        rows = cur.fetchall()
        at = next((i for i, r in enumerate(rows) if r["id"] == iid), None)
        if at is None:
            return _rejected("That is not on a day any more.")
        to = at - 1 if up else at + 1
        if 0 <= to < len(rows):
            rows[at], rows[to] = rows[to], rows[at]
        for i, r in enumerate(rows):
            cur.execute("UPDATE workout_day_items SET sort_order = %s WHERE id = %s", ((i + 1) * 10, r["id"]))
        logbook._audit(cur, "gym", "changed", {"what": "exercise moved"}, logbook.WEB)
    name = next(r["name"] for r in rows if r["id"] == iid)
    return {"outcome": "changed", "head": f'Moved {name} {"up" if up else "down"}.'}


def item_remove(form, key=None, session=None) -> dict:
    """Take an exercise off a day (it stays in your list); Undo puts it back."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT to_jsonb(i) AS j, x.name, d.name AS day FROM workout_day_items i JOIN exercises x ON x.id = i.exercise_id
                       JOIN workout_days d ON d.id = i.day_id WHERE i.id = %s""", (_id(form.get("item")),))
        r = cur.fetchone()
        if not r:
            return _rejected("That is not on a day any more.")
        cur.execute("DELETE FROM workout_day_items WHERE id = %s", (r["j"]["id"],))
        logbook._audit(cur, "gym", "changed", {"what": "exercise off a day"}, logbook.WEB)
    return {"outcome": "changed", "head": f'Took {r["name"]} off {r["day"]}.',
            "gym_undo": {"restore": [{"t": "workout_day_items", "row": r["j"]}], "back": f'Undone: {r["name"]} is back on {r["day"]}.'}}


# The starter split the owner asked for on 02.11.2026 ("a typical workout for a mid 40s person"): machines and dumbbells, a
# cardio finisher each day; the owner changes it on the Plan tab. Made once by `python -m app.gymseed` when there is no split.
STARTER = (("Push", (("Chest press", "weights", 3, 10), ("Shoulder press", "weights", 3, 10), ("Incline dumbbell press", "weights", 3, 10),
                     ("Triceps pushdown", "weights", 3, 12), ("Treadmill", "cardio", None, None))),
           ("Pull", (("Lat pulldown", "weights", 3, 10), ("Seated row", "weights", 3, 10), ("Face pull", "weights", 3, 15),
                     ("Biceps curl", "weights", 3, 12), ("Exercise bike", "cardio", None, None))),
           ("Legs", (("Leg press", "weights", 3, 10), ("Romanian deadlift", "weights", 3, 10), ("Leg curl", "weights", 3, 12),
                     ("Calf raise", "weights", 3, 15), ("Cross trainer", "cardio", None, None))))


def seed() -> dict:
    """Make the starter split when there is no day yet (removed ones count). An exercise of the same name is reused."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM workout_days")
        if cur.fetchone()["n"]:
            return {"made": False, "why": "there is a split already"}
        made = 0
        for di, (day, items) in enumerate(STARTER):
            cur.execute("INSERT INTO workout_days (name, sort_order) VALUES (%s, %s) RETURNING id", (day, (di + 1) * 10))
            did = cur.fetchone()["id"]
            for ii, (name, kind, sets, reps) in enumerate(items):
                cur.execute("SELECT id FROM exercises WHERE lower(name) = lower(%s)", (name,))
                x = cur.fetchone()
                if x:
                    cur.execute("UPDATE exercises SET active = true WHERE id = %s", (x["id"],))
                else:
                    cur.execute("INSERT INTO exercises (name, kind, sort_order) SELECT %s, %s, coalesce(max(sort_order), 0) + 10 FROM exercises RETURNING id",
                                (name, kind))
                    x = cur.fetchone()
                    made += 1
                cur.execute("INSERT INTO workout_day_items (day_id, exercise_id, sets, reps, sort_order) VALUES (%s, %s, %s, %s, %s)",
                            (did, x["id"], sets, reps, (ii + 1) * 10))
        logbook._audit(cur, "gym", "changed", {"what": "starter split", "exercises": made}, logbook.WEB)
    return {"made": True, "days": len(STARTER), "exercises": made}


ACTIONS = {"tick": tick, "remove": remove, "new": new_exercise, "active": active, "rename": rename, "pick": pick, "daynew": day_new, "dayedit": day_edit,
           "dayactive": day_active, "itemadd": item_add, "itemset": item_set, "itemmove": item_move, "itemremove": item_remove}
