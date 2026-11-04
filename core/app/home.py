"""The Home page's data and actions (03.11.2026, step 1 of the plan in the doc "Logger v2: status and web app plan", tab Current;
spec docs/specs/home-page-spec.md on the branch docs/home-page-spec): household chores with an interval and a "last done", apart
from the habits. A chore belongs to one zone or to the whole flat (Dust, Vacuum, Mop), and then each zone is tracked on its own,
so a done row always names its zone. The page lists (chore, zone) pairs by how far past its interval each is (time since last done
divided by the interval; never done counts as 1.5 times late since round 2, shorter intervals first). Ages are plain text: no colour, no
counts of overdue things. Each action takes (form, key, session) like the Gym's and returns the reply shown under the entry box;
the tap is the owner's yes. A tick writes a raw_inbox row ("home: Dust · Sleeping") so Recent shows it. Days are log_day().
Step 2 (03.11.2026): the jobs that fit a time budget (candidates), "Not today", the pick timer (kept on the server), the sessions
of the weekly goal and the goal itself. Round 2, step B (04.11.2026): whole-flat chores per zone (on/off, interval, minutes),
"Vacuum the whole flat" as a job, starting points for never-logged jobs, jobs done earlier, and "Done, next job" while the timer runs."""
import hashlib
import json
from datetime import date, timedelta
from typing import Optional

from psycopg.types.json import Jsonb

from . import logbook
from .db import connect

SOURCES = ("web", "chat", "pick")
HOURS = (5, 15, 30, 60)  # the time chips
WHOLE_FROM = 30  # "Vacuum the whole flat" is offered from 30 minutes on
NEXT_FROM = 3  # "Done, next job" needs this many minutes left
EARLIER_DAYS = 365  # a job done earlier: at most this many days back
DEFAULT_MINUTES = 15
STEPS = 5  # a job shows 2 to 5 steps
GOAL_KEY = "home"  # ui_settings: {"goal": 3}
GOAL = 3
UNDO_TABLES = ("home_zones", "home_chores", "home_done")  # the order rows are put back in
WHOLE = "whole flat"
NEVER = 1.5  # how late a job never logged counts (round 2, item 1)
START_AGO = {"week": 3, "month": 14}  # "When did you last do these?": days ago; "longer" = twice the interval ago
NOON = 12  # a job done earlier (or a starting point) gets 12:00 on its day
UNKNOWN_KEY = "home_unknown"  # ui_settings: {"pairs": [[chore id, zone id], ...]} answered "Don't know" on the starting point card
SOURCE_TEXT = {"web": "ticked", "chat": "chat", "pick": "timer", "start": "starting point"}


def interval_text(days: int) -> str:
    """7 -> weekly, 14 -> every 2 weeks, 30 -> monthly, 90 -> every 3 months, 365 -> yearly, 730 -> every 2 years."""
    if days == 1:
        return "daily"
    if days == 7:
        return "weekly"
    if days == 30:
        return "monthly"
    if days == 365:
        return "yearly"
    if days == 730:
        return "every 2 years"
    if days % 30 == 0 and days <= 360:
        return f"every {days // 30} months"
    if days % 7 == 0:
        return f"every {days // 7} weeks"
    return f"every {days} days"


def age_text(days: Optional[int]) -> str:
    """Plain words for how long ago: not logged yet, today, yesterday, 5 days ago, 3 weeks ago, 3 months ago."""
    if days is None:
        return "not logged yet"
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 56:
        return f"{days // 7} weeks ago"
    if days < 365:
        n = days // 30
        return f"{n} month{'' if n == 1 else 's'} ago"
    n = days // 365
    return f"{n} year{'' if n == 1 else 's'} ago"


def age_short(days: Optional[int]) -> str:
    """The same for a zone tile: not yet, today, 5 days, 3 wk, 2 mo, 1 yr."""
    if days is None:
        return "not yet"
    if days <= 0:
        return "today"
    if days < 14:
        return f"{days} days" if days > 1 else "1 day"
    if days < 56:
        return f"{days // 7} wk"
    if days < 365:
        return f"{days // 30} mo"
    return f"{days // 365} yr"


def _rejected(reason: str) -> dict:
    return {"outcome": "rejected", "reason": reason}


def _id(v) -> int:
    s = str(v or "")[:12]
    return int(s) if s.isdigit() else 0


def _name(v, n: int = 60) -> str:
    return " ".join(str(v or "").split())[:n]


def _lines(v, n: int = 8, width: int = 60) -> list[str]:
    return [x for x in (" ".join(l.split())[:width] for l in str(v or "").splitlines()) if x][:n]


def _words(v, n: int = 12) -> list[str]:
    return [x for x in (" ".join(w.split())[:40].lower() for w in str(v or "").split(",")) if x][:n]


def today(cur):
    cur.execute("SELECT log_day(now()) AS d")
    return cur.fetchone()["d"]


# ---- what the pages show ---------------------------------------------------------------------------

def load(cur) -> dict:
    """Zones, chores and every (chore, zone) pair with its age and how far past its interval it is.
    "pairs" are the active ones, "rank" orders them: never done first (shorter interval first), then the most overdue."""
    day = today(cur)
    cur.execute("SELECT * FROM home_zones ORDER BY sort_order, id")
    all_zones = cur.fetchall()
    zones = [z for z in all_zones if z["active"]]
    by_zone = {z["id"]: z for z in all_zones}
    cur.execute("SELECT * FROM home_chores ORDER BY sort_order, id")
    all_chores = cur.fetchall()
    chores = [c for c in all_chores if c["active"]]
    cur.execute("SELECT DISTINCT ON (chore_id, zone_id) chore_id, zone_id, day, done_at FROM home_done ORDER BY chore_id, zone_id, done_at DESC, id DESC")
    last = {(r["chore_id"], r["zone_id"]): r for r in cur.fetchall()}
    pairs = []
    for c in chores:
        for z in (flat_zones(c, zones) if c["zone_id"] is None else [by_zone[c["zone_id"]]] if c["zone_id"] in by_zone and by_zone[c["zone_id"]]["active"] else []):
            interval = int(c["zone_days"].get(str(z["id"]), c["interval_days"]))
            seen = last.get((c["id"], z["id"]))
            age = (day - seen["day"]).days if seen else None
            late = NEVER if age is None else age / interval  # never logged: 1.5 times late (just behind one twice as late)
            rank = (-late, interval, c["sort_order"], z["sort_order"])
            pairs.append({"chore": c, "zone": z, "interval": interval, "age": age, "done_at": seen and seen["done_at"], "rank": rank,
                          "minutes": zone_minutes(c, z["id"])})
    pairs.sort(key=lambda p: p["rank"])
    return {"today": day, "zones": zones, "all_zones": all_zones, "chores": chores, "all_chores": all_chores, "pairs": pairs}


def flat_zones(c: dict, zones: list) -> list:
    """The zones a whole-flat chore is done in: all of them but those switched off for it (zone_off)."""
    off = {int(x) for x in c.get("zone_off") or []}
    return [z for z in zones if (z["id"] if isinstance(z, dict) else z) not in off]


def zone_minutes(c: dict, zone_id: int) -> int:
    """The minutes of a chore in a zone: the zone's own (zone_minutes) for a whole-flat chore, else the chore's."""
    return int((c.get("zone_minutes") or {}).get(str(zone_id)) or c["minutes"])


def visible(pairs: list[dict], hidden: set) -> list[dict]:
    """The pairs without those marked "Not today" (home_skips of this logging day)."""
    return [p for p in pairs if (p["chore"]["id"], p["zone"]["id"]) not in hidden]


def worst(pairs: list[dict]) -> list[dict]:
    """One row per chore, in the order of its most overdue zone (a whole-flat chore shows as "Dust · <oldest zone>")."""
    seen, out = set(), []
    for p in pairs:  # already ordered
        if p["chore"]["id"] not in seen:
            seen.add(p["chore"]["id"])
            out.append(p)
    return out


def label(chore: dict, zone: dict) -> str:
    return f'{chore["name"]} · {zone["name"]}'


def recent(cur, limit: int = 5, starts: bool = False) -> list[dict]:
    """The last done rows, newest first; rows stamped together (All done, "dusted everything") come back as one entry.
    Each: ids, at, day, dated, label, source, zones. Starting points only with starts=True (History)."""
    cur.execute(f"""SELECT d.id, d.done_at, d.day, d.dated, d.source, d.chore_id, d.zone_id, c.name AS chore, c.zone_id AS chore_zone, c.zone_off, z.name AS zone
                    FROM home_done d JOIN home_chores c ON c.id = d.chore_id JOIN home_zones z ON z.id = d.zone_id
                    {"" if starts else "WHERE d.source <> 'start'"} ORDER BY d.done_at DESC, d.id DESC LIMIT %s""", (limit * 9 + 9,))
    rows = cur.fetchall()
    cur.execute("SELECT id FROM home_zones WHERE active")
    active = [r["id"] for r in cur.fetchall()]
    out: list[dict] = []
    for r in rows:
        if out and out[-1]["chore_id"] == r["chore_id"] and out[-1]["at"] == r["done_at"] and r["chore_zone"] is None:
            out[-1]["ids"].append(r["id"])
            out[-1]["zones"].append(r["zone"])
            continue
        out.append({"ids": [r["id"]], "at": r["done_at"], "day": r["day"], "dated": r["dated"], "source": r["source"], "chore_id": r["chore_id"],
                    "chore": r["chore"], "zones": [r["zone"]], "flat": r["chore_zone"] is None,
                    "n_zones": len(flat_zones({"zone_off": r["zone_off"]}, active))})
    for e in out:
        if not e["flat"]:
            e["label"] = f'{e["chore"]} · {e["zones"][0]}'
        elif len(e["zones"]) >= e["n_zones"] > 1:
            e["label"] = f'{e["chore"]} · {WHOLE}'
        else:
            e["label"] = f'{e["chore"]} · {", ".join(sorted(e["zones"]))}'
    return out[:limit]


# ---- ticking ---------------------------------------------------------------------------------------

def stamp(cur, pairs: list[tuple[int, int]], source: str, raw_id: Optional[int], day, dated: bool = False) -> list[int]:
    """Done rows for these (chore id, zone id) pairs, all with the same time: now, or 12:00 (Berlin) on `day` when dated (a job done
    earlier, a starting point). -> their ids."""
    ids = []
    if dated:
        cur.execute("SELECT (%s::date + make_interval(hours => %s)) AT TIME ZONE 'Europe/Berlin' AS t", (day, NOON))
    else:
        cur.execute("SELECT now() AS t")
    at = cur.fetchone()["t"]
    for chore_id, zone_id in pairs:
        cur.execute("INSERT INTO home_done (chore_id, zone_id, done_at, day, source, raw_id, dated) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (chore_id, zone_id, at, day, source, raw_id, dated))
        ids.append(cur.fetchone()["id"])
    return ids


def _once(cur, key: Optional[str], req: str) -> Optional[dict]:
    """The reply of a form already used (a double tap saves once), or None. The key's reply is stored by _remember."""
    if not key:
        return None
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
    cur.execute("SELECT request_hash, response FROM idempotency_keys WHERE key = %s", (key,))
    seen = cur.fetchone()
    if not seen:
        return None
    return {**seen["response"], "replayed": True} if seen["request_hash"] == req else _rejected("This form was already used.")


def _remember(cur, key: Optional[str], req: str, out: dict) -> None:
    if key:
        cur.execute("INSERT INTO idempotency_keys (key, tool, request_hash, response) VALUES (%s, 'home', %s, %s)", (key, req, Jsonb(out)))


def tick(form, key=None, session=None) -> dict:
    """Tick one chore in one zone: done now. A pair already done today is ticked off again (its rows of today go; Undo puts them back)."""
    chore_id, zone_id = _id(form.get("chore")), _id(form.get("zone"))
    req = hashlib.sha256(json.dumps([chore_id, zone_id]).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        seen = _once(cur, key, req)
        if seen:
            return seen
        cur.execute("SELECT * FROM home_chores WHERE id = %s AND active", (chore_id,))
        c = cur.fetchone()
        cur.execute("SELECT * FROM home_zones WHERE id = %s AND active", (zone_id,))
        z = cur.fetchone()
        if not c or not z or c["zone_id"] not in (None, z["id"]) or not flat_zones(c, [z["id"]]):
            return _rejected("That chore is not in the list.")
        day = today(cur)
        cur.execute("SELECT to_jsonb(d) AS j FROM home_done d WHERE chore_id = %s AND zone_id = %s AND day = %s ORDER BY id", (c["id"], z["id"], day))
        gone = cur.fetchall()
        if gone:  # a second tap on a done circle takes the tick back
            cur.execute("DELETE FROM home_done WHERE id = ANY(%s)", ([g["j"]["id"] for g in gone],))
            out = {"outcome": "changed", "head": f"Ticked off: {label(c, z)}", "home_undo": {"restore": [{"t": "home_done", "row": g["j"]} for g in gone],
                                                                                              "back": f"Undone: {label(c, z)} is done again."}}
            logbook._audit(cur, "home", "removed", {"rows": len(gone)}, logbook.WEB)
        else:
            said = label(c, z)
            cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('web', %s, %s) RETURNING id", (f"home: {said}", Jsonb({"home": True})))
            raw = cur.fetchone()["id"]
            ids = stamp(cur, [(c["id"], z["id"])], "web", raw, day)
            out = {"outcome": "saved", "head": f"Saved: {said}", "home_undo": {"delete": ids}}
            logbook._audit(cur, "home", "saved", {"raw_id": raw}, logbook.WEB)
        _remember(cur, key, req, out)
    return out


def all_done(form, key=None, session=None) -> dict:
    """"All done" on a whole-flat chore: every active zone not done today yet is stamped done now, as one message. When every zone
    is done today already, the tap takes today's ticks of that chore back (like a second tap on a tile); Undo puts them back."""
    chore_id = _id(form.get("chore"))
    req = hashlib.sha256(json.dumps(["all", chore_id]).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        seen = _once(cur, key, req)
        if seen:
            return seen
        cur.execute("SELECT * FROM home_chores WHERE id = %s AND active AND zone_id IS NULL", (chore_id,))
        c = cur.fetchone()
        if not c:
            return _rejected("That is not a whole-flat chore.")
        cur.execute("SELECT id FROM home_zones WHERE active ORDER BY sort_order, id")
        every = flat_zones(c, [z["id"] for z in cur.fetchall()])
        day = today(cur)
        said = f'{c["name"]} · {WHOLE}'
        cur.execute("SELECT to_jsonb(d) AS j FROM home_done d WHERE chore_id = %s AND day = %s ORDER BY id", (c["id"], day))
        today_rows = cur.fetchall()
        have = {g["j"]["zone_id"] for g in today_rows}
        zones = [z for z in every if z not in have]
        if every and not zones:  # all done today already: this tap takes it back
            cur.execute("DELETE FROM home_done WHERE id = ANY(%s)", ([g["j"]["id"] for g in today_rows],))
            out = {"outcome": "changed", "head": f"Ticked off: {said}", "home_undo": {"restore": [{"t": "home_done", "row": g["j"]} for g in today_rows],
                                                                                    "back": f"Undone: {said} is done again."}}
            logbook._audit(cur, "home", "removed", {"rows": len(today_rows)}, logbook.WEB)
            _remember(cur, key, req, out)
            return out
        cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('web', %s, %s) RETURNING id", (f"home: {said}", Jsonb({"home": True})))
        raw = cur.fetchone()["id"]
        ids = stamp(cur, [(c["id"], z) for z in zones], "web", raw, day)
        out = {"outcome": "saved", "head": f"Saved: {said}", "home_undo": {"delete": ids}}
        logbook._audit(cur, "home", "saved", {"raw_id": raw, "zones": len(ids)}, logbook.WEB)
        _remember(cur, key, req, out)
    return out


def remove(form, key=None, session=None) -> dict:
    """Take done rows off the history (ids=1,2,3, rows stamped together); Undo puts them back."""
    ids = [i for i in (_id(x) for x in str(form.get("ids", "")).split(",")[:20]) if i]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT to_jsonb(d) AS j, c.name AS chore, z.name AS zone, c.zone_id AS cz FROM home_done d
                       JOIN home_chores c ON c.id = d.chore_id JOIN home_zones z ON z.id = d.zone_id WHERE d.id = ANY(%s) ORDER BY d.id""", (ids,))
        rows = cur.fetchall()
        if not rows:
            return _rejected("That is not in the history any more.")
        cur.execute("DELETE FROM home_done WHERE id = ANY(%s)", ([r["j"]["id"] for r in rows],))
        logbook._audit(cur, "home", "removed", {"rows": len(rows)}, logbook.WEB)
    said = rows[0]["chore"] if len(rows) > 1 and rows[0]["cz"] is None else f'{rows[0]["chore"]} · {rows[0]["zone"]}'
    return {"outcome": "changed", "head": f"Removed: {said}"[:160],
            "home_undo": {"restore": [{"t": "home_done", "row": r["j"]} for r in rows], "back": f"Undone: {said} is back in the history."}}


def undo(h: dict) -> dict:
    """Undo of a tick (its rows deleted) or of a removal (the rows put back with the same ids)."""
    with connect() as conn, conn.cursor() as cur:
        if h.get("unskip"):
            for chore_id, zone_id, day in h["unskip"]:
                cur.execute("DELETE FROM home_skips WHERE chore_id = %s AND zone_id = %s AND day = %s", (chore_id, zone_id, day))
            head = "Undone: the job is back in the list."
        elif h.get("known"):  # a "Don't know" taken back: the job is on the starting point card again
            gone = {tuple(x) for x in h["known"]}
            _set_unknown(cur, [x for x in unknown(cur) if x not in gone])
            head = "Undone: the job is back on the card."
        elif h.get("delete"):
            cur.execute("DELETE FROM home_done WHERE id = ANY(%s)", (h["delete"],))
            head = "Undone: the tick is gone."
        else:
            rows = sorted(h.get("restore") or [], key=lambda r: UNDO_TABLES.index(r["t"]) if r["t"] in UNDO_TABLES else 9)
            for item in rows:
                if item["t"] in UNDO_TABLES:
                    cur.execute(f"INSERT INTO {item['t']} SELECT * FROM jsonb_populate_record(NULL::{item['t']}, %s) ON CONFLICT DO NOTHING",
                                (Jsonb(item["row"]),))
            head = h.get("back") or "Undone."
        logbook._audit(cur, "home", "undone", {}, logbook.WEB)
    return {"outcome": "changed", "head": head}


# ---- Edit zones: zones and chores (retired, never deleted) -----------------------------------------

def _short(name: str, short: str) -> str:
    return _name(short, 12) or _name(name.split()[0] if name.split() else name, 12)


def zone_new(form, key=None, session=None) -> dict:
    name = _name(form.get("name"), 40)
    if not name:
        return _rejected("Type the zone's name.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM home_zones WHERE lower(name) = lower(%s)", (name,))
        old = cur.fetchone()
        if old and old["active"]:
            return _rejected(f'{old["name"]} is already in your list.')
        if old:
            cur.execute("UPDATE home_zones SET active = true WHERE id = %s", (old["id"],))
            logbook._audit(cur, "home", "changed", {"what": "zone back"}, logbook.WEB)
            return {"outcome": "changed", "head": f'{old["name"]} is back in your list.'}
        cur.execute("""INSERT INTO home_zones (name, short, spots, words, sort_order) SELECT %s, %s, %s, %s, coalesce(max(sort_order), 0) + 10 FROM home_zones""",
                    (name, _short(name, form.get("short")), _lines(form.get("spots"), 12, 40), _words(form.get("words"))))
        logbook._audit(cur, "home", "changed", {"what": "new zone"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Added the zone {name}."}


def zone_edit(form, key=None, session=None) -> dict:
    name = _name(form.get("name"), 40)
    if not name:
        return _rejected("Type the zone's name.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM home_zones WHERE lower(name) = lower(%s) AND id <> %s", (name, _id(form.get("zone"))))
        if cur.fetchone():
            return _rejected("Another zone has that name.")
        cur.execute("UPDATE home_zones SET name = %s, short = %s, spots = %s, words = %s WHERE id = %s RETURNING id",
                    (name, _short(name, form.get("short")), _lines(form.get("spots"), 12, 40), _words(form.get("words")), _id(form.get("zone"))))
        if not cur.fetchone():
            return _rejected("That zone is not in the list.")
        logbook._audit(cur, "home", "changed", {"what": "zone changed"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Saved the zone {name}."}


def zone_active(form, key=None, session=None) -> dict:
    on = form.get("on") == "1"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE home_zones SET active = %s WHERE id = %s RETURNING name", (on, _id(form.get("zone"))))
        r = cur.fetchone()
        if not r:
            return _rejected("That zone is not in the list.")
        logbook._audit(cur, "home", "changed", {"what": "zone back" if on else "zone retired"}, logbook.WEB)
    return {"outcome": "changed", "head": f'{r["name"]} is back.' if on else f'{r["name"]} is retired; its history stays.'}


def _chore_fields(form) -> tuple:
    """(name, zone id or None, interval days, minutes, steps, howto, words) from a form, or ValueError with the reason."""
    name = _name(form.get("name"))
    if not name:
        raise ValueError("Type the chore's name.")
    zone = str(form.get("zone", "")).strip()
    zone_id = None if zone in ("", "flat") else _id(zone)
    try:
        days, minutes = int(str(form.get("days", "")).strip()), int(str(form.get("minutes", "")).strip() or 15)
    except ValueError as e:
        raise ValueError("Give the interval in whole days and the minutes as a whole number.") from e
    if not 1 <= days <= 730 or not 1 <= minutes <= 240:
        raise ValueError("The interval is 1 to 730 days and the time 1 to 240 minutes.")
    howto = " ".join(str(form.get("howto") or "").split())[:300] or None
    return name, zone_id, days, minutes, _lines(form.get("steps"), 8, 80), howto, _words(form.get("words"))


def chore_new(form, key=None, session=None) -> dict:
    try:
        name, zone_id, days, minutes, steps, howto, words = _chore_fields(form)
    except ValueError as e:
        return _rejected(str(e))
    with connect() as conn, conn.cursor() as cur:
        if zone_id is not None:
            cur.execute("SELECT 1 FROM home_zones WHERE id = %s AND active", (zone_id,))
            if not cur.fetchone():
                return _rejected("Pick a zone from the list, or the whole flat.")
        cur.execute("""INSERT INTO home_chores (name, zone_id, interval_days, minutes, steps, howto, words, sort_order)
                       SELECT %s, %s, %s, %s, %s, %s, %s, coalesce(max(sort_order), 0) + 10 FROM home_chores""",
                    (name, zone_id, days, minutes, steps, howto, words))
        logbook._audit(cur, "home", "changed", {"what": "new chore"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Added {name} ({interval_text(days)})."}


def chore_edit(form, key=None, session=None) -> dict:
    try:
        name, zone_id, days, minutes, steps, howto, words = _chore_fields(form)
    except ValueError as e:
        return _rejected(str(e))
    with connect() as conn, conn.cursor() as cur:
        if zone_id is not None:
            cur.execute("SELECT 1 FROM home_zones WHERE id = %s", (zone_id,))
            if not cur.fetchone():
                return _rejected("Pick a zone from the list, or the whole flat.")
        cur.execute("""UPDATE home_chores SET name = %s, zone_id = %s, interval_days = %s, minutes = %s, steps = %s, howto = %s, words = %s
                       WHERE id = %s RETURNING id""", (name, zone_id, days, minutes, steps, howto, words, _id(form.get("chore"))))
        if not cur.fetchone():
            return _rejected("That chore is not in the list.")
        logbook._audit(cur, "home", "changed", {"what": "chore changed"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Saved {name} ({interval_text(days)})."}


def chore_active(form, key=None, session=None) -> dict:
    on = form.get("on") == "1"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE home_chores SET active = %s WHERE id = %s RETURNING name", (on, _id(form.get("chore"))))
        r = cur.fetchone()
        if not r:
            return _rejected("That chore is not in the list.")
        logbook._audit(cur, "home", "changed", {"what": "chore back" if on else "chore retired"}, logbook.WEB)
    return {"outcome": "changed", "head": f'{r["name"]} is back.' if on else f'{r["name"]} is retired; its history stays.'}


# ---- Pick a job (step 2) ------------------------------------------------------------------------------

def parse_pairs(v) -> list[tuple[int, int]]:
    """"3-5,4-5" -> [(3, 5), (4, 5)] (a job's chore-zone pairs in a form)."""
    out = []
    for part in str(v or "").split(",")[:12]:
        a, _, b = part.partition("-")
        if a.isdigit() and b.isdigit():
            out.append((int(a), int(b)))
    return list(dict.fromkeys(out))


def pairs_value(pairs) -> str:
    return ",".join(f"{c}-{z}" for c, z in pairs)


def skipped(cur, day) -> set:
    cur.execute("SELECT chore_id, zone_id FROM home_skips WHERE day = %s", (day,))
    return {(r["chore_id"], r["zone_id"]) for r in cur.fetchall()}


def _single(p: dict, over: bool = False) -> dict:
    """One job. A whole-flat chore is a verb with the zone: "Vacuum the bathroom", "Dust the dressing area". Its steps are the chore's
    own; the zone's spots only when the chore has none (Dust)."""
    c, z = p["chore"], p["zone"]
    flat = c["zone_id"] is None
    return {"pairs": [(c["id"], z["id"])], "session": False, "over": over, "zone": z["name"], "age": p["age"], "minutes": p["minutes"],
            "title": f'{c["name"]} the {z["name"].lower()}' if flat else c["name"],
            "steps": list(c["steps"] or (z["spots"] if flat else []))[:STEPS], "howto": c["howto"]}


def _place(p: dict) -> tuple:
    """The order inside a zone session: Dust first, then the zone's own jobs, then the other whole-flat chores (Vacuum, Mop): top to
    bottom, the floor last."""
    c = p["chore"]
    if c["zone_id"] is not None:
        return (1, c["sort_order"], c["id"])
    dust = c["name"].lower().startswith("dust") or "dust" in c["words"]
    return (0 if dust else 2, c["sort_order"], c["id"])


def _session(chosen: list[dict]) -> dict:
    chosen = sorted(chosen, key=_place)
    ages = [p["age"] for p in chosen]
    z = chosen[0]["zone"]
    return {"pairs": [(p["chore"]["id"], p["zone"]["id"]) for p in chosen], "session": True, "over": False, "zone": z["name"],
            "age": None if None in ages else max(ages), "minutes": sum(p["minutes"] for p in chosen), "title": f'{z["name"]}: {len(chosen)} jobs',
            "steps": [f'{p["chore"]["name"]} ({p["minutes"]} min)' for p in chosen], "howto": None}


def _whole(mine: list[dict]) -> dict:
    """"Vacuum the whole flat": one whole-flat chore in every zone still waiting today, zone by zone (their steps)."""
    c = mine[0]["chore"]
    mine = sorted(mine, key=lambda p: p["zone"]["sort_order"])
    ages = [p["age"] for p in mine]
    return {"pairs": [(c["id"], p["zone"]["id"]) for p in mine], "session": True, "over": False, "zone": WHOLE.capitalize(),
            "age": None if None in ages else max(ages), "minutes": sum(p["minutes"] for p in mine), "title": f'{c["name"]} the {WHOLE}',
            "steps": [f'{p["zone"]["name"]} ({p["minutes"]} min)' for p in mine], "howto": c["howto"]}


def candidates(d: dict, minutes: int, hidden: set) -> list[dict]:
    """The jobs that fit `minutes`, best first: one per chore (its most overdue zone) in the Now list's order, without what was done
    today or marked "Not today". At 60 minutes a zone session (the most overdue job's zone, its due chores together, in the order of
    _place) comes first when two or more fit. When nothing fits, the shortest job is offered with over = True ("do what fits, stopping
    counts")."""
    avail = [p for p in d["pairs"] if p["age"] != 0 and (p["chore"]["id"], p["zone"]["id"]) not in hidden]
    fits = [p for p in avail if p["minutes"] <= minutes]
    jobs: list[dict] = []
    if minutes >= 60 and fits:
        z = fits[0]["zone"]
        total, chosen = 0, []
        for p in (p for p in fits if p["zone"]["id"] == z["id"]):
            if total + p["minutes"] <= minutes:
                chosen.append(p)
                total += p["minutes"]
        if len(chosen) >= 2:
            jobs.append(_session(chosen))
    seen = set()
    for p in fits:
        if p["chore"]["id"] not in seen:
            seen.add(p["chore"]["id"])
            jobs.append(_single(p))
    if minutes >= WHOLE_FROM and jobs:  # "Vacuum the whole flat" right after the first job, when all its waiting zones fit together
        wholes = []
        for c in (c for c in d["chores"] if c["zone_id"] is None):
            mine = [p for p in avail if p["chore"]["id"] == c["id"]]
            if len(mine) >= 2 and sum(p["minutes"] for p in mine) <= minutes and any(p["age"] is None or p["age"] >= p["interval"] for p in mine):
                wholes.append(_whole(mine))
        at = 2 if jobs[0]["session"] else 1
        jobs[at:at] = wholes
    if not jobs and avail:
        first: dict = {}
        for p in avail:
            first.setdefault(p["chore"]["id"], p)
        jobs.append(_single(min(first.values(), key=lambda p: (p["minutes"], p["rank"])), over=True))
    return jobs


def job_of(d: dict, pairs: list[tuple[int, int]]) -> Optional[dict]:
    """A running timer's job, rebuilt from its pairs (None when they are gone)."""
    by = {(p["chore"]["id"], p["zone"]["id"]): p for p in d["pairs"]}
    found = [by[x] for x in pairs if x in by]
    if not found:
        return None
    if len(found) == 1:
        return _single(found[0])
    one = {p["chore"]["id"] for p in found}
    return _whole(found) if len(one) == 1 and found[0]["chore"]["zone_id"] is None else _session(found)


def _valid(cur, pairs: list) -> list[tuple[int, int]]:
    """The pairs that are active chores in active zones (a whole-flat chore in any zone, a zone chore only in its own)."""
    good = []
    for c, z in pairs:
        cur.execute("""SELECT 1 FROM home_chores c, home_zones z WHERE c.id = %s AND z.id = %s AND c.active AND z.active
                       AND (c.zone_id IS NULL AND NOT c.zone_off @> to_jsonb(z.id) OR c.zone_id = z.id)""", (c, z))
        if cur.fetchone():
            good.append((c, z))
    return good


def _names(cur, pairs: list) -> str:
    out = []
    for c, z in pairs:
        cur.execute("SELECT c.name AS chore, z.name AS zone FROM home_chores c, home_zones z WHERE c.id = %s AND z.id = %s", (c, z))
        r = cur.fetchone()
        out.append(f'{r["chore"]} · {r["zone"]}')
    return "; ".join(out)


def timer_open(cur) -> Optional[dict]:
    """The timer of the last 6 hours that is not closed: state running / paused / over and the seconds left."""
    cur.execute("""SELECT id, day, minutes, pairs, ends_at, left_s,
                          CASE WHEN ends_at IS NULL THEN 'paused' WHEN ends_at > now() THEN 'running' ELSE 'over' END AS state,
                          CASE WHEN ends_at IS NULL THEN coalesce(left_s, 0) ELSE greatest(0, ceil(extract(epoch FROM ends_at - now())))::int END AS left
                   FROM home_timers WHERE NOT closed AND started_at > now() - interval '6 hours' ORDER BY id DESC LIMIT 1""")
    return cur.fetchone()


def _close_open(cur) -> None:
    """Close every open timer; one stopped before its end no longer counts as run out."""
    cur.execute("UPDATE home_timers SET ends_at = CASE WHEN ends_at > now() THEN NULL ELSE ends_at END, left_s = NULL, closed = true WHERE NOT closed")


def start(form, key=None, session=None) -> dict:
    """Start the timer for a job (pairs=3-5,4-5, minutes=15). An earlier open timer is closed."""
    try:
        minutes = int(str(form.get("minutes", "")).strip())
    except ValueError:
        minutes = 0
    with connect() as conn, conn.cursor() as cur:
        pairs = _valid(cur, parse_pairs(form.get("pairs")))
        if not pairs or not 1 <= minutes <= 240:
            return _rejected("That job is not in the list any more.")
        _close_open(cur)
        cur.execute("""INSERT INTO home_timers (day, minutes, pairs, ends_at) VALUES (%s, %s, %s, now() + make_interval(mins => %s))""",
                    (today(cur), minutes, Jsonb([list(x) for x in pairs]), minutes))
        logbook._audit(cur, "home", "changed", {"what": "timer started", "minutes": minutes}, logbook.WEB)
    return {"outcome": "changed", "head": f"Timer started: {minutes} minutes."}


def pause(form, key=None, session=None) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE home_timers SET left_s = greatest(0, ceil(extract(epoch FROM ends_at - now()))::int), ends_at = NULL
                       WHERE id = %s AND NOT closed AND ends_at > now() RETURNING id""", (_id(form.get("timer")),))
        if not cur.fetchone():
            return _rejected("That timer is not running.")
    return {"outcome": "changed", "head": "Timer paused."}


def resume(form, key=None, session=None) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE home_timers SET ends_at = now() + make_interval(secs => left_s), left_s = NULL
                       WHERE id = %s AND NOT closed AND ends_at IS NULL AND left_s IS NOT NULL RETURNING id""", (_id(form.get("timer")),))
        if not cur.fetchone():
            return _rejected("That timer is not paused.")
    return {"outcome": "changed", "head": "Timer running again."}


def stop(form, key=None, session=None) -> dict:
    """Close the timer without a tick. One that ran to its end still counts as a session; one stopped before does not."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM home_timers WHERE id = %s AND NOT closed", (_id(form.get("timer")),))
        if not cur.fetchone():
            return _rejected("That timer is already closed.")
        _close_open(cur)
    return {"outcome": "changed", "head": "Timer closed."}


def pick_done(form, key=None, session=None) -> dict:
    """"Done" on the pick page: every chore of the job is ticked now (source pick; those already done today are left), the timer closes."""
    pairs_in = parse_pairs(form.get("pairs"))
    req = hashlib.sha256(json.dumps(["pick", pairs_in]).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        seen = _once(cur, key, req)
        if seen:
            return seen
        pairs = _valid(cur, pairs_in)
        if not pairs:
            return _rejected("That job is not in the list any more.")
        day = today(cur)
        cur.execute("SELECT chore_id, zone_id FROM home_done WHERE day = %s AND chore_id = ANY(%s)", (day, [c for c, _ in pairs]))
        have = {(r["chore_id"], r["zone_id"]) for r in cur.fetchall()}
        new = [x for x in pairs if x not in have]
        _close_open(cur)
        if not new:
            out = {"outcome": "changed", "head": "Already ticked today."}
        else:
            said = _names(cur, new)
            cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('web', %s, %s) RETURNING id", (f"home: {said}", Jsonb({"home": True})))
            raw = cur.fetchone()["id"]
            ids = stamp(cur, new, "pick", raw, day)
            out = {"outcome": "saved", "head": f"Saved: {said}"[:160], "home_undo": {"delete": ids}}
            logbook._audit(cur, "home", "saved", {"raw_id": raw, "pick": True}, logbook.WEB)
        _remember(cur, key, req, out)
    return out


def skip(form, key=None, session=None) -> dict:
    """"Not today": the job is hidden until the next logging day; Undo brings it back."""
    with connect() as conn, conn.cursor() as cur:
        pairs = _valid(cur, parse_pairs(form.get("pairs")))
        if not pairs:
            return _rejected("That job is not in the list any more.")
        day = today(cur)
        new = []
        for c, z in pairs:
            cur.execute("INSERT INTO home_skips (chore_id, zone_id, day) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING chore_id", (c, z, day))
            if cur.fetchone():
                new.append([c, z, day.isoformat()])
        said = _names(cur, pairs)
        logbook._audit(cur, "home", "changed", {"what": "not today", "pairs": len(pairs)}, logbook.WEB)
    return {"outcome": "changed", "head": f"Not today: {said}"[:160], "home_undo": {"unskip": new}} if new else {"outcome": "changed", "head": "Hidden already."}


# ---- round 2, step B: per-zone settings, starting points, jobs done earlier, Done next job ------------------------------------------

def chore_zones(form, key=None, session=None) -> dict:
    """A whole-flat chore's settings per zone: on_<zone id>=1 (done there), days_<zone id> and min_<zone id> (blank = the chore's)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM home_chores WHERE id = %s AND zone_id IS NULL", (_id(form.get("chore")),))
        c = cur.fetchone()
        if not c:
            return _rejected("That is not a whole-flat chore.")
        cur.execute("SELECT id FROM home_zones WHERE active ORDER BY sort_order, id")
        zones = [r["id"] for r in cur.fetchall()]
        off, days, mins = [x for x in c["zone_off"] if x not in zones], {k: v for k, v in c["zone_days"].items() if int(k) not in zones}, \
            {k: v for k, v in c["zone_minutes"].items() if int(k) not in zones}  # retired zones keep what they had
        for z in zones:
            if form.get(f"on_{z}") != "1":
                off.append(z)
            for field, into, top in (("days", days, 730), ("min", mins, 240)):
                v = str(form.get(f"{field}_{z}", "")).strip()
                if not v:
                    continue
                if not v.isdigit() or not 1 <= int(v) <= top:
                    return _rejected("Give the days (1 to 730) and minutes (1 to 240) as whole numbers, or leave them blank.")
                if int(v) != (c["interval_days"] if field == "days" else c["minutes"]):
                    into[str(z)] = int(v)
        if all(z in off for z in zones):
            return _rejected(f'{c["name"]} needs at least one zone. Retire it instead.')
        cur.execute("UPDATE home_chores SET zone_off = %s, zone_days = %s, zone_minutes = %s WHERE id = %s",
                    (Jsonb(sorted(off)), Jsonb(days), Jsonb(mins), c["id"]))
        logbook._audit(cur, "home", "changed", {"what": "chore zones"}, logbook.WEB)
    return {"outcome": "changed", "head": f'Saved {c["name"]} per zone.'}


def unknown(cur) -> list[tuple[int, int]]:
    """The jobs answered "Don't know" on the starting point card (they stay "not logged yet")."""
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (UNKNOWN_KEY,))
    r = cur.fetchone()
    return [tuple(x) for x in ((r["value"] if r else {}).get("pairs") or []) if isinstance(x, list) and len(x) == 2]


def _set_unknown(cur, pairs: list) -> None:
    cur.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (UNKNOWN_KEY, Jsonb({"pairs": [list(x) for x in pairs]})))


def never_done(cur, d: dict) -> list[dict]:
    """The pairs for the starting point card: never logged and not answered "Don't know", in the Now list's order."""
    skip = set(unknown(cur))
    return [p for p in d["pairs"] if p["age"] is None and (p["chore"]["id"], p["zone"]["id"]) not in skip]


def start_point(form, key=None, session=None) -> dict:
    """"When did you last do these?": pairs=3-5, ago=week (3 days ago) / month (2 weeks ago) / longer (twice the interval ago) / unknown.
    A starting point is a done row of source 'start' at 12:00 of that day: not a session, not under Recently done."""
    ago = str(form.get("ago", ""))
    req = hashlib.sha256(json.dumps(["start", str(form.get("pairs")), ago]).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        seen = _once(cur, key, req)
        if seen:
            return seen
        pairs = _valid(cur, parse_pairs(form.get("pairs")))
        if not pairs or ago not in ("week", "month", "longer", "unknown"):
            return _rejected("That job is not in the list any more.")
        said = _names(cur, pairs)
        if ago == "unknown":
            had = unknown(cur)
            new = [x for x in pairs if x not in had]
            _set_unknown(cur, had + new)
            out = {"outcome": "changed", "head": f"Left as not logged yet: {said}"[:160], "home_undo": {"known": [list(x) for x in new]}}
        else:
            day, ids = today(cur), []
            for c, z in pairs:
                cur.execute("SELECT 1 FROM home_done WHERE chore_id = %s AND zone_id = %s", (c, z))
                if cur.fetchone():
                    continue  # logged meanwhile: nothing to start from
                if ago == "longer":
                    cur.execute("SELECT interval_days, zone_days FROM home_chores WHERE id = %s", (c,))
                    r = cur.fetchone()
                    back = 2 * int(r["zone_days"].get(str(z), r["interval_days"]))
                else:
                    back = START_AGO[ago]
                ids += stamp(cur, [(c, z)], "start", None, day - timedelta(days=back), dated=True)
            out = ({"outcome": "changed", "head": f"Starting point: {said}"[:160], "home_undo": {"delete": ids}} if ids
                   else {"outcome": "changed", "head": "Logged already."})
        logbook._audit(cur, "home", "changed", {"what": "starting point", "pairs": len(pairs)}, logbook.WEB)
        _remember(cur, key, req, out)
    return out


def earlier_day(cur, when: str):
    """when = 1 (yesterday), 2 (2 days ago) or a date (YYYY-MM-DD) within the last year, before today. -> the day, or None."""
    day = today(cur)
    when = str(when or "").strip()
    if when.isdigit() and 1 <= int(when) <= 7:
        return day - timedelta(days=int(when))
    try:
        got = date.fromisoformat(when[:10])
    except ValueError:
        return None
    return got if day - timedelta(days=EARLIER_DAYS) <= got < day else None


def earlier(form, key=None, session=None) -> dict:
    """A job done earlier (pairs=3-5, when=1 / 2 / a date): done rows at 12:00 of that day (History shows the date only)."""
    req = hashlib.sha256(json.dumps(["earlier", str(form.get("pairs")), str(form.get("when"))]).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        seen = _once(cur, key, req)
        if seen:
            return seen
        pairs = _valid(cur, parse_pairs(form.get("pairs")))
        if not pairs:
            return _rejected("Pick a job from the list.")
        day = earlier_day(cur, form.get("when"))
        if day is None:
            return _rejected("Pick a day before today, in the last year.")
        said = _names(cur, pairs)
        cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('web', %s, %s) RETURNING id",
                    (f"home: {said} (done {day:%d.%m.%Y})", Jsonb({"home": True})))
        raw = cur.fetchone()["id"]
        ids = stamp(cur, pairs, "web", raw, day, dated=True)
        out = {"outcome": "saved", "head": f"Saved: {said} on {day:%d.%m.}"[:160], "home_undo": {"delete": ids}}
        logbook._audit(cur, "home", "saved", {"raw_id": raw, "earlier": True}, logbook.WEB)
        _remember(cur, key, req, out)
    return out


def pick_next(form, key=None, session=None) -> dict:
    """"Done, next job" while the timer runs: the job is ticked (source pick) and the timer goes on with the next job that fits the time
    left. With nothing that fits (or under 3 minutes left) it is "Done": the timer closes."""
    pairs_in = parse_pairs(form.get("pairs"))
    req = hashlib.sha256(json.dumps(["next", pairs_in]).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        seen = _once(cur, key, req)
        if seen:
            return seen
        t = timer_open(cur)
        pairs = _valid(cur, pairs_in)
        if not t or t["id"] != _id(form.get("timer")) or t["state"] != "running" or not pairs:
            return _rejected("That timer is not running.")
        day = today(cur)
        cur.execute("SELECT chore_id, zone_id FROM home_done WHERE day = %s AND chore_id = ANY(%s)", (day, [c for c, _ in pairs]))
        have = {(r["chore_id"], r["zone_id"]) for r in cur.fetchall()}
        new = [x for x in pairs if x not in have]
        ids, said = [], _names(cur, new or pairs)
        if new:
            cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('web', %s, %s) RETURNING id", (f"home: {said}", Jsonb({"home": True})))
            raw = cur.fetchone()["id"]
            ids = stamp(cur, new, "pick", raw, day)
            logbook._audit(cur, "home", "saved", {"raw_id": raw, "pick": True}, logbook.WEB)
        left = t["left"] // 60
        nxt = candidates(load(cur), left, skipped(cur, day)) if t["left"] >= NEXT_FROM * 60 else []
        nxt = [j for j in nxt if not j["over"]]
        if nxt:
            cur.execute("UPDATE home_timers SET pairs = %s WHERE id = %s", (Jsonb([list(x) for x in nxt[0]["pairs"]]), t["id"]))
            head = f'Saved: {said}. Next: {nxt[0]["title"]}'
        else:
            _close_open(cur)
            head = f"Saved: {said}. Nothing else fits in the time left; the timer is closed."
        out = {"outcome": "saved" if ids else "changed", "head": head[:160]}
        if ids:
            out["home_undo"] = {"delete": ids}
        _remember(cur, key, req, out)
    return out


# ---- the weekly goal ------------------------------------------------------------------------------------

def goal(cur) -> int:
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (GOAL_KEY,))
    r = cur.fetchone()
    try:
        return min(7, max(1, int((r["value"] if r else {}).get("goal", GOAL))))
    except (TypeError, ValueError):
        return GOAL


def goal_set(form, key=None, session=None) -> dict:
    try:
        n = int(str(form.get("goal", "")).strip())
    except ValueError:
        n = 0
    if not 1 <= n <= 7:
        return _rejected("The weekly goal is 1 to 7 sessions.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (GOAL_KEY, Jsonb({"goal": n})))
        logbook._audit(cur, "home", "changed", {"what": "weekly goal"}, logbook.WEB)
    return {"outcome": "changed", "head": f"Weekly goal: {n} session{'' if n == 1 else 's'}."}


def week(cur) -> dict:
    """The weekly goal's numbers. A session is a logging day with a chore done, or with a timer that ran to its end (one a day at most);
    weeks start on Monday. -> {"goal", "this", "weeks": [the four weeks before, the latest first]}."""
    day = today(cur)
    monday = day - timedelta(days=day.weekday())
    start = monday - timedelta(days=28)
    cur.execute("""SELECT day FROM home_done WHERE day >= %s AND source <> 'start' UNION
                   SELECT day FROM home_timers WHERE ends_at IS NOT NULL AND ends_at <= now() AND day >= %s""", (start, start))
    days = {r["day"] for r in cur.fetchall()}
    return {"goal": goal(cur), "this": sum(1 for x in days if x >= monday),
            "weeks": [sum(1 for x in days if monday - timedelta(days=7 * (i + 1)) <= x < monday - timedelta(days=7 * i)) for i in range(4)]}


ACTIONS = {"tick": tick, "alldone": all_done, "remove": remove, "zonenew": zone_new, "zoneedit": zone_edit, "zoneactive": zone_active,
           "chorenew": chore_new, "choreedit": chore_edit, "choreactive": chore_active,
           "start": start, "pause": pause, "resume": resume, "stop": stop, "pickdone": pick_done, "skip": skip, "goalset": goal_set,
           "chorezones": chore_zones, "startpoint": start_point, "earlier": earlier, "picknext": pick_next}


# ---- the starter list (the owner's 25 chores of the spec, section 7; made once by `python -m app.homeseed`) -------------------------

# (name, short, other words, spots)
ZONES = (
    ("Bathroom", "Bath", ("bath", "wc", "restroom"), ("Sink", "Bathtub", "Toilet", "Shelves", "Radiator", "Floor", "Window", "Window sill")),
    ("Entrance", "Entrance", ("hall", "hallway", "entryway"), ("Shoe rack", "Coat hanger", "Floor", "Drawers", "Shelves")),
    ("Dressing area", "Dressing", ("dressing", "closet area", "wardrobe area"), ("Closet",)),
    ("Kitchen", "Kitchen", (), ("Cooking area", "Sink", "Chopping area", "Shelves", "Utensils cabinet", "Pantry shelves", "Pantry jars and bottles",
                                "Cookware storage")),
    ("Workstation", "Desk", ("desk", "office", "study"), ("Desk", "Chair", "Bookshelf", "PC", "Electronics")),
    ("Sleeping", "Sleeping", ("sleeping area", "bedroom", "bed room"), ("Bed", "Lamp", "Radiator", "Window", "Window sill", "Bedding")),
    ("Laundry", "Laundry", ("laundry room",), ("Machine", "Detergent and laundry supplies", "Storage cabinet")),
    ("Storage", "Storage", ("storage room", "storeroom"), ("Random shelves", "Boxes", "Old and seldom-used things")),
)
# (zone name or None = whole flat, name, interval days, minutes, other words, steps, how-to line)
CHORES = (
    (None, "Dust", 14, 15, ("dust", "dusted", "dusting"), (), "Top to bottom, with a damp cloth. Vacuuming comes after, another time is fine."),
    (None, "Vacuum", 7, 10, ("vacuum", "vacuumed", "hoover", "hoovered", "vacuuming"), (), "Start at the far end and finish at the door."),
    (None, "Mop", 14, 15, ("mop", "mopped", "mopping"), (), "After vacuuming, with a barely damp mop."),
    ("Bathroom", "Sink and toilet", 7, 15, ("sink", "toilet", "wc"), ("Sink and tap", "Toilet seat and bowl", "Floor around the toilet"), None),
    ("Bathroom", "Bathtub", 14, 15, ("bathtub", "bath tub", "tub", "shower"), ("Scrub the tub", "Rinse", "Wipe the tap"), None),
    ("Bathroom", "Window", 90, 15, ("window", "windows"), ("Glass", "Frame", "Window sill"), None),
    ("Entrance", "Tidy shoe rack and coats", 30, 10, ("shoe rack", "shoes", "coats", "coat hanger"), ("Shoes back on the rack", "Coats on their hooks"), None),
    ("Entrance", "Wipe drawers and shelves", 90, 15, ("entrance drawers", "drawers", "entrance shelves"), ("Empty one drawer at a time", "Wipe", "Put back"), None),
    ("Dressing area", "Tidy closet", 30, 20, ("closet", "wardrobe"), ("Take out what is out of place", "Fold and hang", "Put back"), None),
    ("Kitchen", "Cooking area and stove", 7, 15, ("stove", "cooktop", "hob", "cooking area"), ("Stove and knobs", "Counter beside it", "Wall behind"), None),
    ("Kitchen", "Scrub sink", 7, 10, ("sink", "kitchen sink", "scrub sink"), ("Sink and tap", "Drain"), None),
    ("Kitchen", "Shelves and cabinet fronts", 30, 20, ("cabinets", "cabinet fronts", "kitchen shelves"), ("Cabinet fronts and handles", "Shelves"), None),
    ("Kitchen", "Pantry check", 30, 10, ("pantry",), ("Look at dates", "Jars and bottles", "Note what is missing"), None),
    ("Kitchen", "Sort cookware and utensils", 90, 20, ("cookware", "utensils", "utensil drawer"), ("Cookware storage", "Utensils cabinet"), None),
    ("Workstation", "Clear and wipe desk", 7, 10, ("desk",), ("Clear the desk", "Wipe", "Put back only what is used"), None),
    ("Workstation", "Dust PC and electronics", 30, 10, ("pc", "electronics", "computer"), ("PC", "Screen and keyboard", "Cables"), None),
    ("Sleeping", "Change bedding", 14, 10, ("bedding", "bed sheets", "sheets", "changed the bed"), ("Strip the bed", "Fresh bedding", "Start the wash"), None),
    ("Sleeping", "Window", 90, 15, ("window", "windows"), ("Glass", "Frame", "Window sill"), None),
    ("Laundry", "Do laundry", 7, 20, ("laundry", "washing", "wash clothes"), ("Wash", "Dry", "Put away"), "Wash, dry, put away."),
    ("Laundry", "Clean machine", 60, 15, ("washing machine", "machine", "drum", "filter"), ("Drum", "Door seal", "Filter"), None),
)
MOP_WEEKLY = ("Kitchen", "Bathroom")  # Mop's interval is 7 days there


def seed() -> dict:
    """Make the starter zones and chores when there is no zone yet. Estimated minutes are first guesses: the owner edits them."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM home_zones")
        if cur.fetchone()["n"]:
            return {"made": False, "why": "there are zones already"}
        ids = {}
        for i, (name, short, words, spots) in enumerate(ZONES):
            cur.execute("INSERT INTO home_zones (name, short, words, spots, sort_order) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (name, short, list(words), list(spots), (i + 1) * 10))
            ids[name] = cur.fetchone()["id"]
        for i, (zone, name, days, minutes, words, steps, howto) in enumerate(CHORES):
            zone_days = {str(ids[z]): 7 for z in MOP_WEEKLY} if name == "Mop" else {}
            cur.execute("""INSERT INTO home_chores (name, zone_id, interval_days, zone_days, minutes, steps, howto, words, sort_order)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (name, ids[zone] if zone else None, days, Jsonb(zone_days), minutes, list(steps), howto, list(words), (i + 1) * 10))
        logbook._audit(cur, "home", "changed", {"what": "starter list", "zones": len(ZONES), "chores": len(CHORES)}, logbook.WEB)
    return {"made": True, "zones": len(ZONES), "chores": len(CHORES)}
