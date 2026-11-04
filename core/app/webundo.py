"""Undo for the web app's buttons (31.10.2026): the last change made with a button can be taken back from a bar that
stays until the device's next action (web.py done keeps it in web_sessions.undo). A save that logged a message (a tick,
a value, a usual meal, an Again chip) is undone by the chat's own undo of that message; a delete or a correction of a
food entry, a tick or a health value is undone by putting the rows back as they were just before it."""
import secrets
import time
from typing import Optional

from psycopg.types.json import Jsonb

from . import held, logbook
from .db import connect

TABLES = {"f": "food_entries", "m": "measurements", "e": "events"}


def undoable(ref: str) -> bool:
    return len(ref) > 1 and ref[0] in TABLES and ref[1:].isdigit()


def snapshot(refs: list[str]) -> list[dict]:
    """The rows as they are now (to_jsonb, so every column comes back exactly), for refs like f12, e5, m3."""
    rows = []
    with connect() as c:
        for ref in refs:
            table = TABLES[ref[0]]  # one of three fixed names
            r = c.execute(f"SELECT to_jsonb(x) AS j FROM {table} x WHERE x.id = %s", (int(ref[1:]),)).fetchone()
            if r:
                rows.append({"t": table, "row": r["j"]})
    return rows


MAX_AGE_S = 15 * 60  # the Undo bar goes after 15 minutes: an old copy put back could undo a later change made elsewhere (04.11.2026)


def fresh(rec) -> bool:
    """Whether an Undo record is still offered (records from before 04.11.2026 have no time and are not)."""
    return bool(rec) and time.time() - float(rec.get("at") or 0) < MAX_AGE_S


def record(resp: dict, rows: Optional[list] = None) -> Optional[dict]:
    """What the Undo bar offers after this reply (with the time it was made), or None (then the bar goes away)."""
    rec = _record(resp, rows)
    return rec and {**rec, "at": time.time()}


def _record(resp: dict, rows: Optional[list] = None) -> Optional[dict]:
    if resp.get("held_undo"):  # an unread message deleted on Today or Recent (held.py)
        return {"token": secrets.token_urlsafe(12), "head": resp.get("head") or "Deleted.", "held": int(resp["held_undo"])}
    if resp.get("gym_undo") and not resp.get("replayed"):  # a gym tick or a removed gym row (gym.py)
        return {"token": secrets.token_urlsafe(12), "head": (resp.get("head") or "Saved.")[:160], "gym": resp["gym_undo"]}
    if resp.get("home_undo") and not resp.get("replayed"):  # a chore tick or a removed history row (home.py)
        return {"token": secrets.token_urlsafe(12), "head": (resp.get("head") or "Saved.")[:160], "home": resp["home_undo"]}
    if resp.get("outcome") == "changed" and rows:
        return {"token": secrets.token_urlsafe(12), "head": resp.get("head") or "Changed.", "rows": rows}
    if resp.get("raw_id") and resp.get("outcome") in ("saved", "partly_saved") and not resp.get("replayed"):
        said = "; ".join(str(x) for x in resp.get("receipt") or [])
        head = resp.get("head") or (f"Saved: {said}" if said else "Saved.")
        return {"token": secrets.token_urlsafe(12), "head": head[:160], "raw_id": resp["raw_id"]}
    return None


def apply(rec: dict) -> dict:
    """Undo one record: the logged message through the chat's undo, or the rows put back (same ids) in one transaction."""
    if rec.get("raw_id"):
        from .webactions import undo  # here, not at the top: webactions imports this module
        return undo(str(rec["raw_id"]))
    if rec.get("gym"):
        from .gym import undo as gym_undo  # here: gym imports logbook, like this module
        return gym_undo(rec["gym"])
    if rec.get("home"):
        from .home import undo as home_undo  # here: home imports logbook, like this module
        return home_undo(rec["home"])
    if rec.get("held"):
        if held.restore(int(rec["held"])):
            return {"outcome": "changed", "head": "The unread message is back."}
        return {"outcome": "rejected", "head": "Nothing undone.", "reason": "That message is gone for good."}
    days, ids = set(), []
    with connect() as conn, conn.cursor() as cur:
        for item in rec.get("rows") or []:
            table, row = item["t"], item["row"]
            if table not in TABLES.values():
                continue
            cur.execute(f"DELETE FROM {table} WHERE id = %s RETURNING day::text AS day", (row["id"],))
            gone = cur.fetchone()
            cur.execute(f"INSERT INTO {table} SELECT * FROM jsonb_populate_record(NULL::{table}, %s)", (Jsonb(row),))
            if table == "food_entries":
                days.update(d for d in (row.get("day"), gone and gone["day"]) if d)
            ids.append(f"{table}:{row['id']}")
        for d in sorted(days):  # meal numbers follow the times again (the 2-hour rule)
            cur.execute("SELECT assign_meals(%s::date)", (d,))
        logbook._audit(cur, "web_change", "changed", {"what": "undo", "ids": ids}, logbook.WEB)
    return {"outcome": "changed", "head": "Undone."}
