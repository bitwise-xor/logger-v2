"""Commands: look-ups and changes asked for in words ("what did I eat yesterday", "the apple was 150 g", "undo that").
The command reader (ai/command.py) turns the words into v1's find and change arguments, given the ids of recent
entries; v1's own code (v1_commands.py) does the work. A change first runs as a preview inside a savepoint that is
rolled back, so the user sees v1's own receipt; their yes runs the same steps for real (apply)."""
import json
import secrets
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from .ai import command as reader
from . import todos
from .ai.provider import Provider, Usage
from .todoist import TodoistError
from .v1_commands import Ask, _bound, _change, _delete_thought, _find, _find_food, _find_health, _find_private
from .v1_food import _num
from .v1_log import Reject, _day_line, _dm, fmt
from .v1_thoughts import _short, _thought_row
from .when import BERLIN

FIND_ARGS = {"thought_id", "query", "tag", "area", "thought_type", "stage", "project", "needs_review", "unreviewed_links",
             "since", "until", "private", "limit", "health", "food", "todoist"}
CHANGE_ARGS = {"id", "fields", "delete", "past"}
QUESTION_HOURS = 72


def context(cur, now: datetime) -> str:
    """Recent entries with their ids, for the reader: food and health of today and yesterday, the newest thoughts."""
    since = now.astimezone(BERLIN).date() - timedelta(days=1)
    lines = []
    cur.execute("""SELECT id, day, time, meal, food, amount, unit, is_drink FROM v_food_log WHERE day >= %s
                   ORDER BY day, time NULLS FIRST, id""", (since,))
    for r in cur.fetchall():
        t = r["time"].strftime("%H:%M") if r["time"] else "no time"
        lines.append(f"f{r['id']} {_dm(r['day'])} {t} {'drink' if r['is_drink'] else 'meal ' + str(r['meal'])}: "
                     f"{r['food']} {_num(r['amount'])} {r['unit']}")
    cur.execute("""SELECT id, day, type, value1, value2, unit, event_at, time_known FROM v_measurements WHERE day >= %s
                   ORDER BY event_at, id""", (since,))
    for r in cur.fetchall():
        v = _num(r["value1"]) + (f"/{_num(r['value2'])}" if r["value2"] is not None else "")
        lines.append(f"m{r['id']} {_dm(r['day'])} {_hm(r)}: {r['type']} {v} {r['unit'] or ''}".rstrip())
    cur.execute("SELECT id, day, type, event_at, time_known FROM v_events WHERE day >= %s ORDER BY event_at, id", (since,))
    lines += [f"e{r['id']} {_dm(r['day'])} {_hm(r)}: {r['type']}" for r in cur.fetchall()]
    cur.execute("SELECT id, thought_type, stage, summary FROM thoughts ORDER BY id DESC LIMIT 8")
    lines += [f"#{r['id']} {r['thought_type'] or 'unsorted'} ({r['stage']}): {_short(r['summary'], 80)}" for r in cur.fetchall()]
    return "\n".join(lines)


def _hm(r: dict) -> str:
    return r["event_at"].astimezone(BERLIN).strftime("%H:%M") if r["time_known"] else "no time"


def _steps(raw: list[dict]) -> list[dict]:
    """The reader's steps with their args checked: only v1's own argument names, as an object."""
    out = []
    for s in raw:
        try:
            args = json.loads(s["args"] or "{}")
        except json.JSONDecodeError as e:
            raise Reject(f"could not read the request ({e.msg})") from e
        if not isinstance(args, dict):
            raise Reject("could not read the request")
        allowed = {"find": FIND_ARGS, "change": CHANGE_ARGS, "undo": set(), "todo": {"done"}}[s["action"]]
        bad = sorted(set(args) - allowed)
        if bad:
            raise Reject(f"unknown argument(s) {', '.join(bad)} for {s['action']}")
        if s["action"] == "change" and not args.get("id"):
            raise Reject("a change needs the id of an entry")
        out.append({"action": s["action"], "args": args})
    return out


def _run_find(cur, a: dict) -> str:
    limit = a.get("limit") or 20
    if a.get("todoist") is not None:
        return todos.find(str(a["todoist"]))
    if a.get("private") and (a.get("since") or a.get("until")):
        return _private_between(cur, a, limit)
    if a.get("private"):
        return _find_private(cur, a.get("thought_id"), a.get("query"), a.get("tag"), a.get("area"), a.get("thought_type"), limit)
    if a.get("food") is not None:
        return _find_food(cur, str(a["food"]), a.get("since"), a.get("until"), limit)
    if a.get("health"):
        return _find_health(cur, str(a["health"]), a.get("since"), a.get("until"), limit)
    return _find(cur, a.get("thought_id"), a.get("query"), a.get("tag"), a.get("area"), a.get("thought_type"), a.get("stage"),
                 a.get("project"), a.get("needs_review"), bool(a.get("unreviewed_links")), a.get("since"), a.get("until"), limit)


def _private_between(cur, a: dict, limit: int) -> str:
    """Private and gratitude entries of a time span ("my gratitudes for today"): v1's private search has no dates."""
    lo, hi = _bound(a.get("since"), False), _bound(a.get("until"), True)
    where, params = ["TRUE"], []
    if lo:
        where.append("received_at >= %s"); params.append(lo)
    if hi:
        where.append("received_at <= %s"); params.append(hi)
    q = (a.get("query") or "").strip().lower()
    if q in ("gratitude", "gratitudes", "grateful"):
        where.append("label = 'gratitude'")
    elif q in ("private", "private entries"):
        where.append("label = 'private'")
    elif q:
        where.append("(content ILIKE %s OR summary ILIKE %s)"); params += [f"%{q}%", f"%{q}%"]
    cur.execute(f"""SELECT id, received_at, label, summary, content FROM private.entries WHERE {' AND '.join(where)}
                    ORDER BY id DESC LIMIT %s""", params + [max(1, min(int(limit), 50))])
    rows = cur.fetchall()
    if not rows:
        return "No matching private entries in that time."
    return (f"{len(rows)} private entr{'y' if len(rows) == 1 else 'ies'}:\n"
            + "\n".join(f"P{r['id']} [{r['label']}] {fmt(r['received_at'])}: {r['summary'] or _short(r['content'], 160)}" for r in rows))


def _undo(cur, now: datetime, dry: bool = False) -> str:
    """v2's undo: everything saved from the newest message of the last 24 hours (food, health rows, thoughts, its
    Todoist actions), or the newest tick-off, whichever came later. v1's 'last' undid only food, found by v1's own
    extractor version, which in v2 would hit the imported history. dry: say what would happen, touch no Todoist."""
    cur.execute("""SELECT r.id, r.content, r.received_at FROM raw_inbox r WHERE r.channel <> 'catalogue' AND r.received_at > %s - interval '24 hours'
                   AND (EXISTS (SELECT 1 FROM food_entries x WHERE x.raw_id = r.id) OR EXISTS (SELECT 1 FROM measurements x WHERE x.raw_id = r.id)
                        OR EXISTS (SELECT 1 FROM events x WHERE x.raw_id = r.id) OR EXISTS (SELECT 1 FROM thoughts x WHERE x.raw_id = r.id)
                        OR EXISTS (SELECT 1 FROM todo_actions x WHERE x.raw_id = r.id AND x.undone_at IS NULL AND x.op <> 'exists'))
                   ORDER BY r.received_at DESC, r.id DESC LIMIT 1""", (now,))
    raw = cur.fetchone()
    cur.execute("""SELECT batch, max(at) AS at FROM todo_actions WHERE raw_id IS NULL AND undone_at IS NULL
                   AND at > %s - interval '24 hours' GROUP BY batch ORDER BY max(at) DESC LIMIT 1""", (now,))
    tick = cur.fetchone()
    if tick and (not raw or tick["at"] > raw["received_at"]):
        try:
            return "OK undid the tick-off: " + "; ".join(todos.undo(cur, tick["batch"], dry)) + "."
        except TodoistError as e:
            raise Reject(f"{e}; nothing changed") from e
    if not raw:
        raise Reject("there is no message of the last 24 hours to undo")
    gone = []
    cur.execute("DELETE FROM food_entries WHERE raw_id = %s RETURNING id, day", (raw["id"],))
    food = cur.fetchall()
    gone += [f"f{x['id']}" for x in food]
    cur.execute("DELETE FROM measurements WHERE raw_id = %s RETURNING id", (raw["id"],))
    gone += [f"m{x['id']}" for x in cur.fetchall()]
    cur.execute("DELETE FROM events WHERE raw_id = %s RETURNING id", (raw["id"],))
    gone += [f"e{x['id']}" for x in cur.fetchall()]
    cur.execute("SELECT id FROM thoughts WHERE raw_id = %s ORDER BY id", (raw["id"],))
    for t in cur.fetchall():
        _delete_thought(cur, _thought_row(cur, t["id"]))
        gone.append(f"#{t['id']}")
    try:
        gone += todos.undo(cur, f"r{raw['id']}", dry)
    except TodoistError as e:
        raise Reject(f"{e}; nothing changed") from e
    days = sorted({x["day"] for x in food})
    for d in days:
        cur.execute("SELECT assign_meals(%s)", (d,))
    return (f"OK undid raw #{raw['id']} ('{_short(raw['content'], 60)}'): removed {', '.join(gone)}. The message stays in the inbox."
            + "".join("\n" + _day_line(cur, d) for d in days))


def _run_changes(cur, steps: list[dict], now: datetime, past=None, dry: bool = False) -> list[str]:
    out = []
    for s in steps:
        a = s["args"]
        if s["action"] == "undo":
            out.append(_undo(cur, now, dry))
        else:
            out.append(_change(cur, str(a["id"]), a.get("fields") or {}, [], [], [], bool(a.get("delete")), past or a.get("past")))
    return out


def _preview(cur, steps: list[dict], now: datetime) -> list[str]:
    cur.execute("SAVEPOINT command_preview")
    try:
        return _run_changes(cur, steps, now, dry=True)  # dry: the preview never touches Todoist
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT command_preview")


def _pending(cur, tool: str, payload: dict, reason: str, now: datetime) -> str:
    pid = "p" + secrets.token_hex(4)
    cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, %s, %s, %s, %s)",
                (pid, tool, Jsonb({**payload, "now": now.isoformat()}), reason[:500], now + timedelta(hours=QUESTION_HOURS)))
    return pid


def handle(cur, provider: Provider, text: str, now: datetime, previous=()) -> tuple[dict, list[Usage]]:
    """One command. Look-ups answer at once; changes are read back and wait for the user's yes."""
    cmd, u = reader.read(provider, text, now.astimezone(BERLIN).replace(tzinfo=None), previous, context(cur, now))
    try:
        steps = _steps(cmd["steps"])
    except Reject as e:
        return {"outcome": "rejected", "reason": str(e), "questions": []}, [u]
    audit = [{"action": s["action"], "id": s["args"].get("id")} for s in steps]
    if not steps:
        if not cmd.get("unclear"):
            return {"outcome": "not_logged", "intent": "command", "questions": []}, [u]
        pid = _pending(cur, "command", {"text": text}, cmd["unclear"], now)
        return {"outcome": "needs_choice", "questions": [{"pending_id": pid, "question": cmd["unclear"]}], "steps": audit}, [u]
    found, changes = [], [s for s in steps if s["action"] in ("change", "undo")]
    try:
        for s in steps:
            if s["action"] == "find":
                found.append(_run_find(cur, s["args"]))
            elif s["action"] == "todo":  # ticking off happens straight away (decided 25.10.); undo puts it back
                found.append("\n".join(todos.tick(cur, [str(x) for x in s["args"].get("done") or []], now)))
        if not changes:
            done = any(s["action"] == "todo" for s in steps)
            return {"outcome": "changed" if done else "answered", "result": "\n\n".join(found), "questions": [], "steps": audit}, [u]
        preview = _preview(cur, changes, now)
    except Ask as e:  # a food correction that past entries use: v1 asks whether those change too
        pid = _pending(cur, "command_confirm", {"steps": changes, "ask_past": True}, str(e), now)
        return {"outcome": "needs_confirmation", "result": "\n\n".join(found) or None, "read_back": str(e),
                "questions": [{"pending_id": pid, "question": "Change the past entries too (yes), only new ones (no), or cancel?"}],
                "steps": audit}, [u]
    except Reject as e:
        return {"outcome": "rejected", "reason": str(e), "questions": [], "steps": audit}, [u]
    except TodoistError as e:
        return {"outcome": "rejected", "reason": f"{e}; nothing changed in Todoist", "questions": [], "steps": audit}, [u]
    pid = _pending(cur, "command_confirm", {"steps": changes}, "\n".join(preview), now)
    return {"outcome": "needs_confirmation", "result": "\n\n".join(found) or None,
            "read_back": "Preview, nothing changed yet:\n" + "\n".join(preview),
            "questions": [{"pending_id": pid, "question": "Make this change (yes) or not (no)?"}], "steps": audit}, [u]


def apply(cur, payload: dict, now: datetime, past=None) -> dict:
    """The user's yes to a previewed change: the same steps, for real."""
    try:
        done = _run_changes(cur, payload["steps"], now, past)
    except (Reject, TodoistError) as e:
        return {"outcome": "rejected", "reason": str(e)}
    return {"outcome": "changed", "receipt": done,
            "steps": [{"action": s["action"], "id": s["args"].get("id")} for s in payload["steps"]]}