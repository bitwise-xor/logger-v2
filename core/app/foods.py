"""Choosing one food for the user's word, and its amount. v1 split this between the server (matching) and the
chat rules (defaults, picking among candidates, estimating unit weights); in v2 the core does all of it, so every
client behaves the same."""
from datetime import datetime
from typing import Optional

from .v1_food import _UNIT_GENERIC, _find_foods, _fkey, _silent_close, _to_amount, _unit_rows

EXACT = ("id", "name", "alias", "default")


def use_counts(cur, ids: list[int], before: Optional[datetime] = None) -> dict:
    """How often each food was logged (only entries logged before `before`, when given)."""
    cur.execute("SELECT food_id, count(*) AS n FROM food_entries WHERE food_id = ANY(%s)"
                + (" AND logged_at < %s" if before else "") + " GROUP BY food_id",
                (ids, before) if before else (ids,))
    return {r["food_id"]: r["n"] for r in cur.fetchall()}


def _dominant(cur, cands: list[dict], before: Optional[datetime]) -> Optional[dict]:
    """The candidate used clearly most often: at least 3 uses and at least twice the next one (v1 chat rule)."""
    n = use_counts(cur, [c["id"] for c in cands], before)
    ranked = sorted(cands, key=lambda c: -n.get(c["id"], 0))
    top = n.get(ranked[0]["id"], 0)
    second = n.get(ranked[1]["id"], 0) if len(ranked) > 1 else 0
    return ranked[0] if top >= 3 and top >= 2 * second else None


def _cooked_partner(cat: dict, f: dict) -> Optional[dict]:
    """The cooked food that belongs to a raw one ("Potato, raw" -> "Potato, boiled"), when there is exactly one."""
    head = _fkey(f["name"]).split()[0]
    hits = [g for g in cat["foods"] if g["active"] and g.get("state") == "cooked" and _fkey(g["name"]).split()[:1] == [head]]
    return hits[0] if len(hits) == 1 else None


def pick(cur, cat: dict, term: str, cooked: bool = False, before: Optional[datetime] = None) -> tuple[Optional[dict], str, list[dict]]:
    """-> (food, how, candidates). how: default (the user's own product for a word, checked first, as the v1 chat
    rules did), id, name, alias, close (the only clear match), usual (most used of several), state (raw/cooked chosen
    by the raw rule), ask (several, the user decides), none."""
    f, how, cands = _pick(cur, cat, term, cooked, before)
    if f and cooked and f.get("state") == "raw":  # "100 g potatoes, boiled": the word matched the raw food
        g = _cooked_partner(cat, f)
        if g:
            return g, "state", [g]
    return f, how, cands


def _pick(cur, cat: dict, term: str, cooked: bool, before: Optional[datetime]) -> tuple[Optional[dict], str, list[dict]]:
    d = cat["defaults"].get(_fkey(term))
    if d:
        return d, "default", [d]
    matches, how = _find_foods(cat, term)
    act = [m for m in matches if m["active"]] or matches
    if how in EXACT:
        if len(act) == 1:
            return act[0], how, act
        if act:
            d = _dominant(cur, act, before)
            return (d, "usual", act) if d else (None, "ask", act)
        return None, "none", []
    if how == "close":
        f = _silent_close(cat, term, act, how)
        if f:
            return f, "close", act
        cands = act[:8]
        want = "cooked" if cooked else "raw"
        if any(c.get("state") for c in cands):
            st = [c for c in cands if c.get("state") == want]
            if len(st) == 1:
                return st[0], "state", cands
            if st:
                cands = st
        d = _dominant(cur, cands, before)
        return (d, "usual", cands) if d else (None, "ask", cands)
    return None, ("ask" if act else "none"), act[:4]


def amount(cur, food: dict, qty: float, unit: Optional[str], grams_per_unit: Optional[float]) -> tuple[Optional[float], bool, Optional[str]]:
    """-> (amount in the food's basis, estimated, problem). A stored unit weight wins over the parser's estimate
    (v1 preferred the chat's estimate); a 'piece' falls back to a stored 'medium'."""
    grams, _, est, prob, _ = _to_amount(cur, food, qty, unit, None)
    if not prob:
        return grams, est, None
    if _fkey(unit or "") in _UNIT_GENERIC:
        medium = [r for r in _unit_rows(cur, food["id"]) if _fkey(r["unit"]) == "medium"]
        if medium:
            return qty * float(medium[0]["grams"]), medium[0]["source"] == "estimate", None
    if grams_per_unit:
        grams, _, est, prob, _ = _to_amount(cur, food, qty, unit, grams_per_unit)
        return grams, True, prob
    return None, False, prob
