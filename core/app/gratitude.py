"""The gratitude list of the day (step B, 14.11.2026): how many lines today, the goal (ui_settings "gratitude", default 10),
today's lines for the signed-in owner who asked to see them, a save through logbook.log (privateline.py: no AI) and a change
of one of today's lines. Every answer here that is not asked for the words holds ids, times and counts only."""
import re
from datetime import datetime
from typing import Optional

from psycopg.types.json import Jsonb

from . import commands, cycle, logbook, privateline
from .db import connect
from .v1_log import Reject
from .when import BERLIN

KEY = "gratitude"
DEFAULT_GOAL = 10
GOAL_RANGE = (1, 50)
MAX_LINE = 1000  # one changed line
_TODAY = "label = 'gratitude' AND log_day(coalesce(event_at, received_at)) = %s"


def goal(cur) -> int:
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (KEY,))
    r = cur.fetchone()
    g = (r["value"] if r and isinstance(r["value"], dict) else {}).get("goal", DEFAULT_GOAL)
    lo, hi = GOAL_RANGE
    return g if isinstance(g, int) and not isinstance(g, bool) and lo <= g <= hi else DEFAULT_GOAL


def save_goal(n: int) -> None:
    lo, hi = GOAL_RANGE
    if not lo <= n <= hi:
        raise ValueError("goal out of range")
    with connect() as c:
        c.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (KEY, Jsonb({"goal": n})))


def count(cur) -> int:
    cur.execute(f"SELECT count(*) AS n FROM private.entries WHERE {_TODAY}", (cycle.today(cur),))
    return cur.fetchone()["n"]


def state() -> dict:
    """{"count", "goal", "day", "times": [(id, "HH:MM")]} of today, no words."""
    with connect() as c, c.cursor() as cur:
        day = cycle.today(cur)
        cur.execute(f"SELECT id, coalesce(event_at, received_at) AS at FROM private.entries WHERE {_TODAY} ORDER BY at, id", (day,))
        times = [(r["id"], r["at"].astimezone(BERLIN).strftime("%H:%M")) for r in cur.fetchall()]
        return {"count": len(times), "goal": goal(cur), "day": day, "times": times}


def lines() -> list[dict]:
    """Today's lines with their words, for the owner who pressed Show: [{"id", "at", "text"}]."""
    with connect() as c, c.cursor() as cur:
        cur.execute(f"""SELECT id, content, coalesce(event_at, received_at) AS at FROM private.entries WHERE {_TODAY}
                        ORDER BY at, id""", (cycle.today(cur),))
        return [{"id": r["id"], "at": r["at"].astimezone(BERLIN).strftime("%H:%M"), "text": privateline.words(r["content"])}
                for r in cur.fetchall()]


def save(text: str, key: Optional[str]) -> dict:
    """One typed or pasted text -> its lines saved (one entry each) through logbook.log, without the AI.
    -> {"ok": True, "ids", "at", "count", "goal", "replayed"} or {"ok": False, "message", "key_used"}; never the words."""
    text = str(text or "").replace("\r\n", "\n").strip()
    if not text:
        return {"ok": False, "message": "Type something first."}
    out = logbook.log(f"gratitude: {text}", key, channel=logbook.WEB)
    ids = out.get("private_ids") or []
    if out.get("outcome") != "saved" or not ids:
        reason = out.get("reason") or out.get("message") or ""
        used = "idempotency key was already used" in reason
        why = reason if reason in ("At most 20 lines at once.", "Type something after the label.") else ""
        return {"ok": False, "message": (why.replace("Type something after the label.", "Type something first.")
                                         or "Not saved, try again."), "key_used": used}
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT max(coalesce(event_at, received_at)) AS at FROM private.entries WHERE id = ANY(%s)", (ids,))
        at = cur.fetchone()["at"]
        return {"ok": True, "ids": ids, "at": at.astimezone(BERLIN).strftime("%H:%M") if at else "", "count": count(cur),
                "goal": goal(cur), "replayed": bool(out.get("replayed"))}


def edit(ref: str, text: str) -> dict:
    """Change the words of one of today's gratitude lines: content "gratitude: <line>" and the summary, through the logger's
    change function in one transaction, audited as the Private page's save (ids only). Empty text is refused (delete stays
    on the Private page). -> {"ok": True, "id", "at"} or {"ok": False, "message"}."""
    ref = str(ref or "").strip()
    m = re.fullmatch(r"P?(\d{1,12})", ref)
    if not m:
        return {"ok": False, "message": "Unknown line."}
    pid = int(m.group(1))
    line = privateline.words(" ".join(str(text or "").split())).strip()[:MAX_LINE]
    if not line:
        return {"ok": False, "message": "A line cannot be empty. Delete it on the Private page."}
    summary = privateline.pred("gratitude", line)["thoughts"][0]["summary"]
    steps = [{"action": "change", "args": {"id": f"P{pid}", "fields": {"summary": summary}}}]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id, coalesce(event_at, received_at) AS at FROM private.entries WHERE id = %s AND {_TODAY} FOR UPDATE",
                    (pid, cycle.today(cur)))
        row = cur.fetchone()
        if not row:
            return {"ok": False, "message": "Only today's gratitude lines can be changed here."}
        try:
            commands._run_changes(cur, steps, datetime.now(BERLIN))
        except Reject:
            conn.rollback()
            with conn.cursor() as cur2:
                logbook._audit(cur2, "web_change", "rejected", {"what": "private save", "ids": [f"P{pid}"]}, logbook.WEB)
            return {"ok": False, "message": "Not saved, try again."}
        cur.execute("UPDATE private.entries SET content = %s WHERE id = %s", (f"gratitude: {line}", pid))
        logbook._audit(cur, "web_change", "changed", {"what": "private save", "ids": [f"P{pid}"]}, logbook.WEB)
    return {"ok": True, "id": pid, "at": row["at"].astimezone(BERLIN).strftime("%H:%M")}
