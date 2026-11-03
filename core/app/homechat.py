"""Chores typed in the chat or the web box, read without the AI (03.11.2026, Home page, spec section 5): "vacuumed the kitchen",
"dusted everything", "did laundry", "cleaned the bathroom". A word match on the chores' names and other words and on the zones'
names (home.py's data). A line must say that something was done (a verb) and be made only of known words: a line with anything
else in it (a weight, a meal, a time) goes to the AI as before. A match that is not clear asks (a "home_choice" question, like
food names) and saves nothing until it is answered. The words are kept in raw_inbox first, as always; the receipt shows date and
time; the chat's undo takes the rows back (commands._undo)."""
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

from psycopg.types.json import Jsonb

from . import home
from .record import QUESTION_HOURS

VERBS = {"vacuumed", "hoovered", "dusted", "mopped", "cleaned", "wiped", "scrubbed", "washed", "did", "done", "finished", "changed", "tidied",
         "sorted", "checked", "cleared", "emptied", "organized", "organised"}
FILLER = {"i", "ve", "the", "a", "an", "my", "all", "of", "in", "up", "out", "and", "again", "just", "also", "now", "then", "finally", "quickly",
          "some", "today", "it", "whole", "entire", "off", "down", "with", "to", "was", "were", "have", "had"}
FLATISH = {"flat", "apartment", "house", "rooms", "zones"}
EVERY = {"everything", "everywhere"}
NO = re.compile(r"^\s*(no|nope|nein|skip|cancel|stop|drop)\b", re.I)
_TOK = re.compile(r"[a-zäöüß]+")
_EXTRA = re.compile(r"[^a-zäöüß\s.,!;'’-]")  # a digit or any other sign: not a plain chore line


def _stem(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def _key(phrase: str) -> tuple:
    return tuple(_stem(t) for t in _TOK.findall(re.sub(r"\(.*?\)", " ", phrase.lower())))


def _index(data: dict) -> dict:
    """phrase (stems) -> [("zone", id) | ("chore", id)] for the active zones and chores."""
    idx: dict = {}
    for z in data["zones"]:
        for p in [z["name"], z["short"], *z["words"]]:
            if _key(p):
                idx.setdefault(_key(p), [])
                if ("zone", z["id"]) not in idx[_key(p)]:
                    idx[_key(p)].append(("zone", z["id"]))
    for c in data["chores"]:
        for p in [c["name"], *c["words"]]:
            if _key(p):
                idx.setdefault(_key(p), [])
                if ("chore", c["id"]) not in idx[_key(p)]:
                    idx[_key(p)].append(("chore", c["id"]))
    return idx


def _pairs_text(data: dict, pairs: list) -> str:
    """"Dust · whole flat", "Vacuum · Kitchen, Desk", "Sink and toilet · Bathroom": the pairs, one part per chore."""
    zones = {z["id"]: z for z in data["all_zones"]}
    chores = {c["id"]: c for c in data["all_chores"]}
    every = {z["id"] for z in data["zones"]}
    by: dict = {}
    for cid, zid in pairs:
        by.setdefault(cid, []).append(zid)
    out = []
    for cid, zids in by.items():
        c = chores[cid]
        if c["zone_id"] is None and set(zids) >= every and len(every) > 1:
            out.append(f'{c["name"]} · {home.WHOLE}')
        else:
            out.append(f'{c["name"]} · {", ".join(zones[z]["name"] for z in zids)}')
    return "; ".join(out)


def _resolve(data: dict, text: str) -> Optional[dict]:
    """{"pairs": [(chore id, zone id)]} when the line is clear, {"ask": question, "cands": [{"label", "pairs"}]} when it is not,
    None when it is not a chore line."""
    if not data["chores"] or _EXTRA.search(text.lower()):
        return None
    toks = _TOK.findall(text.lower())
    idx = _index(data)
    longest = max((len(k) for k in idx), default=1)
    stems = [_stem(t) for t in toks]
    matches, i = [], 0
    while i < len(toks):
        for n in range(min(longest, len(toks) - i), 0, -1):
            refs = idx.get(tuple(stems[i:i + n]))
            if refs:
                matches.append(refs)
                i += n
                break
        else:
            if toks[i] not in VERBS and toks[i] not in FILLER and toks[i] not in FLATISH and toks[i] not in EVERY:
                return None  # something else is in the line: the AI reads it
            i += 1
    if not any(t in VERBS for t in toks):
        return None
    whole = bool(set(toks) & (FLATISH | EVERY))
    zone_of = {z["id"]: z for z in data["zones"]}
    chore_of = {c["id"]: c for c in data["chores"]}
    flat_hit = any(chore_of[r[1]]["zone_id"] is None for m in matches for r in m if r[0] == "chore")
    zones_m: list = []
    chores_m: list = []  # one entry per matched phrase: its candidate chores
    for m in matches:
        zs = [r[1] for r in m if r[0] == "zone"]
        cs = [r[1] for r in m if r[0] == "chore"]
        for z in zs:
            if z not in zones_m:
                zones_m.append(z)
        if cs and not (zs and flat_hit):  # "dusted the desk": the desk is a place there, not the chore "Clear and wipe desk"
            chores_m.append(cs)
    if not (zones_m or chores_m or (whole and (set(toks) & FLATISH))):
        return None
    flat = [c for c in data["chores"] if c["zone_id"] is None]
    all_zones = [z["id"] for z in data["zones"]]
    pairs: list = []
    pending_flat: list = []  # whole-flat chores that still need their zones
    for cs in chores_m:
        cands = [chore_of[c] for c in cs]
        if zones_m:
            narrowed = [c for c in cands if c["zone_id"] is None or c["zone_id"] in zones_m]
            cands = narrowed or cands
        if len(cands) > 1:
            return {"ask": "Which one did you mean?", "cands": [{"label": home.label(c, zone_of[c["zone_id"]]) if c["zone_id"] else c["name"],
                                                                 "pairs": _chore_pairs(c, zones_m, all_zones, whole)} for c in cands
                                                                 if c["zone_id"] or zones_m or whole]}
        c = cands[0]
        if c["zone_id"] is not None:
            pairs.append((c["id"], c["zone_id"]))
        elif zones_m:
            pairs += [(c["id"], z) for z in zones_m]
        elif whole:
            pairs += [(c["id"], z) for z in all_zones]
        else:
            pending_flat.append(c)
    if pending_flat:  # "vacuumed": which zone?
        cands = [{"label": z["name"] + " (" + ", ".join(c["name"] for c in pending_flat) + ")", "pairs": [(c["id"], z["id"]) for c in pending_flat]}
                 for z in data["zones"]]
        return {"ask": f'Which zone: {", ".join(c["name"] for c in pending_flat)}?', "cands": cands}
    if pairs:
        return {"pairs": list(dict.fromkeys(pairs))}
    if zones_m:  # only a place: "cleaned the bathroom"
        mine = [c for c in data["chores"] if c["zone_id"] in zones_m]
        if not mine:
            return None
        if len(mine) == 1:
            return {"pairs": [(mine[0]["id"], mine[0]["zone_id"])]}
        return {"ask": "Which job did you mean?", "cands": [{"label": home.label(c, zone_of[c["zone_id"]]), "pairs": [(c["id"], c["zone_id"])]} for c in mine]}
    return {"ask": "Which job did you mean?", "cands": [{"label": f'{c["name"]} · {home.WHOLE}', "pairs": [(c["id"], z) for z in all_zones]} for c in flat]}


def _chore_pairs(c: dict, zones_m: list, all_zones: list, whole: bool) -> list:
    if c["zone_id"] is not None:
        return [(c["id"], c["zone_id"])]
    return [(c["id"], z) for z in (zones_m or all_zones if whole else [])]


def _when(now: datetime) -> str:
    return f"{now:%d.%m. %H:%M}"


def _save(cur, data: dict, raw: int, pairs: list, now: datetime) -> dict:
    ids = home.stamp(cur, [(int(c), int(z)) for c, z in pairs], "chat", raw, home.today(cur))
    return {"outcome": "saved", "raw_id": raw, "receipt": [f"Home: {_pairs_text(data, pairs)} ({_when(now)})"], "home_ids": ids}


def read(cur, text: str, now: datetime, channel: str) -> Optional[dict]:
    """The reply for a chore line, or None to let the AI read it (logbook.log)."""
    data = home.load(cur)
    r = _resolve(data, text)
    if r is None:
        return None
    cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES (%s, %s, %s) RETURNING id", (channel, text, Jsonb({"home_chat": True})))
    raw = cur.fetchone()["id"]
    if "pairs" in r:
        return _save(cur, data, raw, r["pairs"], now)
    pid = "p" + secrets.token_hex(4)
    cands = [{"label": c["label"], "pairs": [list(p) for p in c["pairs"]]} for c in r["cands"] if c["pairs"]]
    if len(cands) < 2:
        return {"outcome": "kept", "raw_id": raw, "reason": "Kept as text: no clear chore in it."}
    question = r["ask"] + " " + "; ".join(f"{i}. {c['label']}" for i, c in enumerate(cands, 1)) + " (or all of them)."
    cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'home_choice', %s, %s, %s)",
                (pid, Jsonb({"text": text, "raw_id": raw, "cands": cands, "now": now.isoformat()}), question[:500], now + timedelta(hours=QUESTION_HOURS)))
    return {"outcome": "needs_choice", "raw_id": raw, "questions": [{"pending_id": pid, "question": question}]}


def choose(cands: list, reply: str) -> Optional[list]:
    """The pairs the reply picks: "H2" or "2", "all", or words of one candidate's label; None when it is not clear."""
    said = reply.strip().lower()
    if re.fullmatch(r"(all( of (them|it))?|both|everything|alle)\W*", said):
        return list(dict.fromkeys(tuple(p) for c in cands for p in c["pairs"]))
    m = re.fullmatch(r"h?(\d+)\W*", said)
    if m:
        return [tuple(p) for p in cands[int(m[1]) - 1]["pairs"]] if 1 <= int(m[1]) <= len(cands) else None
    want = {_stem(t) for t in _TOK.findall(said)}
    hit = [c for c in cands if want and want <= {_stem(t) for t in _TOK.findall(c["label"].lower())}]
    return [tuple(p) for p in hit[0]["pairs"]] if len(hit) == 1 else None


def answer(cur, p: dict, reply: str, now: datetime) -> dict:
    """The owner's answer to a home_choice question (logbook.answer)."""
    d = p["payload"]
    if NO.match(reply):
        cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'cancelled' WHERE id = %s", (p["id"],))
        return {"outcome": "cancelled"}
    pairs = choose(d["cands"], reply)
    if not pairs:
        return {"outcome": "needs_choice", "reason": "I did not get which one: answer with its number, its name or all.",
                "questions": [{"pending_id": p["id"], "question": p["reason"]}]}
    out = _save(cur, home.load(cur), d["raw_id"], pairs, now)
    cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (p["id"],))
    return out
