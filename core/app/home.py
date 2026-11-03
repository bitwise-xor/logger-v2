"""The Home page's data and actions (03.11.2026, step 1 of the plan in the doc "Logger v2: status and web app plan", tab Current;
spec docs/specs/home-page-spec.md on the branch docs/home-page-spec): household chores with an interval and a "last done", apart
from the habits. A chore belongs to one zone or to the whole flat (Dust, Vacuum, Mop), and then each zone is tracked on its own,
so a done row always names its zone. The page lists (chore, zone) pairs by how far past its interval each is (time since last done
divided by the interval; never done counts as the most overdue, shorter intervals first). Ages are plain text: no colour, no
counts of overdue things. Each action takes (form, key, session) like the Gym's and returns the reply shown under the entry box;
the tap is the owner's yes. A tick writes a raw_inbox row ("home: Dust · Sleeping") so Recent shows it. Days are log_day()."""
import hashlib
import json
from typing import Optional

from psycopg.types.json import Jsonb

from . import logbook
from .db import connect

SOURCES = ("web", "chat", "pick")
UNDO_TABLES = ("home_zones", "home_chores", "home_done")  # the order rows are put back in
WHOLE = "whole flat"


def interval_text(days: int) -> str:
    """7 -> weekly, 14 -> every 2 weeks, 30 -> monthly, 90 -> every 3 months."""
    if days == 1:
        return "daily"
    if days == 7:
        return "weekly"
    if days == 30:
        return "monthly"
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
        for z in (zones if c["zone_id"] is None else [by_zone[c["zone_id"]]] if c["zone_id"] in by_zone and by_zone[c["zone_id"]]["active"] else []):
            interval = int(c["zone_days"].get(str(z["id"]), c["interval_days"]))
            seen = last.get((c["id"], z["id"]))
            age = (day - seen["day"]).days if seen else None
            rank = (0, interval, c["sort_order"], z["sort_order"]) if age is None else (1, -age / interval, interval, c["sort_order"], z["sort_order"])
            pairs.append({"chore": c, "zone": z, "interval": interval, "age": age, "done_at": seen and seen["done_at"], "rank": rank})
    pairs.sort(key=lambda p: p["rank"])
    return {"today": day, "zones": zones, "all_zones": all_zones, "chores": chores, "all_chores": all_chores, "pairs": pairs}


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


def recent(cur, limit: int = 5) -> list[dict]:
    """The last done rows, newest first; rows stamped together (All done, "dusted everything") come back as one entry.
    Each: ids, at, label, source, zones."""
    cur.execute("""SELECT d.id, d.done_at, d.source, d.chore_id, d.zone_id, c.name AS chore, c.zone_id AS chore_zone, z.name AS zone
                   FROM home_done d JOIN home_chores c ON c.id = d.chore_id JOIN home_zones z ON z.id = d.zone_id
                   ORDER BY d.done_at DESC, d.id DESC LIMIT %s""", (limit * 9 + 9,))
    rows = cur.fetchall()
    cur.execute("SELECT count(*) AS n FROM home_zones WHERE active")
    n_zones = cur.fetchone()["n"]
    out: list[dict] = []
    for r in rows:
        if out and out[-1]["chore_id"] == r["chore_id"] and out[-1]["at"] == r["done_at"] and r["chore_zone"] is None:
            out[-1]["ids"].append(r["id"])
            out[-1]["zones"].append(r["zone"])
            continue
        out.append({"ids": [r["id"]], "at": r["done_at"], "source": r["source"], "chore_id": r["chore_id"], "chore": r["chore"],
                    "zones": [r["zone"]], "flat": r["chore_zone"] is None})
    for e in out:
        if not e["flat"]:
            e["label"] = f'{e["chore"]} · {e["zones"][0]}'
        elif len(e["zones"]) >= n_zones > 1:
            e["label"] = f'{e["chore"]} · {WHOLE}'
        else:
            e["label"] = f'{e["chore"]} · {", ".join(sorted(e["zones"]))}'
    return out[:limit]


# ---- ticking ---------------------------------------------------------------------------------------

def stamp(cur, pairs: list[tuple[int, int]], source: str, raw_id: Optional[int], day) -> list[int]:
    """Done rows for these (chore id, zone id) pairs, all with the same time. -> their ids."""
    ids = []
    cur.execute("SELECT now() AS t")
    at = cur.fetchone()["t"]
    for chore_id, zone_id in pairs:
        cur.execute("INSERT INTO home_done (chore_id, zone_id, done_at, day, source, raw_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (chore_id, zone_id, at, day, source, raw_id))
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
        if not c or not z or c["zone_id"] not in (None, z["id"]):
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
    """"All done" on a whole-flat chore: every active zone is stamped done now, as one message."""
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
        zones = [z["id"] for z in cur.fetchall()]
        day = today(cur)
        said = f'{c["name"]} · {WHOLE}'
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
        if h.get("delete"):
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


ACTIONS = {"tick": tick, "alldone": all_done, "remove": remove, "zonenew": zone_new, "zoneedit": zone_edit, "zoneactive": zone_active,
           "chorenew": chore_new, "choreedit": chore_edit, "choreactive": chore_active}


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
