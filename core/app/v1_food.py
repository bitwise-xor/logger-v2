"""Food matching, units and health types. Copied unchanged from logger-app connector/server.py (v1);
v2 additions live in foods.py."""
import difflib
import re
import unicodedata
from collections import defaultdict
from typing import Optional

_UNIT_SYN = {
    "tablespoon": "tbsp", "tbsp": "tbsp", "el": "tbsp", "essloffel": "tbsp", "esslöffel": "tbsp",
    "teaspoon": "tsp", "tsp": "tsp", "tl": "tsp", "teeloffel": "tsp", "teelöffel": "tsp",
    "cup": "cup", "tasse": "cup", "scheibe": "slice", "scheiben": "slice", "ei": "egg", "eier": "egg",
}


_UNIT_GENERIC = {"piece", "pc", "pcs", "unit", "stuck", "stück", "stk", "x", "each", "serving", "portion"}


_UNIT_BASE = {"g": 1.0, "gram": 1.0, "gramm": 1.0, "gr": 1.0, "kg": 1000.0, "ml": 1.0, "l": 1000.0, "liter": 1000.0,
              "litre": 1000.0, "cl": 10.0, "dl": 100.0}


def _num(x) -> str:
    return ("%.2f" % float(x)).rstrip("0").rstrip(".")


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", (s or "").strip().lower())


def _fkey(s: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", (s or "").replace("ß", "ss")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _sing(w: str) -> str:
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _food_cat(cur) -> dict:
    cur.execute("""SELECT id, name, aliases, basis, kcal, protein, fat, sat_fat, carbs, fibre, sugar_total, sugar_added, sodium_mg,
                          caffeine_mg, source, state, active, needs_review, review_reason, is_drink FROM foods ORDER BY id""")
    foods = cur.fetchall()
    by_id = {f["id"]: f for f in foods}
    names: dict = defaultdict(list)
    aliases: dict = defaultdict(list)
    for f in foods:
        names[_fkey(f["name"])].append(f)
        for a in f["aliases"] or []:
            aliases[_fkey(a)].append(f)
    cur.execute("SELECT term, food_id FROM food_defaults")
    defaults = {_fkey(r["term"]): by_id[r["food_id"]] for r in cur.fetchall() if r["food_id"] in by_id}
    cur.execute("SELECT id, food_id, name FROM recipes")
    recipes = {r["food_id"]: r for r in cur.fetchall()}
    return {"foods": foods, "by_id": by_id, "names": names, "aliases": aliases, "defaults": defaults, "recipes": recipes}


def _find_foods(cat: dict, term: str) -> tuple[list[dict], str]:
    """(matches, how). how: id, name, alias, default (exact: may be used), close, fuzzy, none (never used silently)."""
    t = (term or "").strip()
    m = re.fullmatch(r"[nN]#?(\d+)", t)
    if m:
        f = cat["by_id"].get(int(m[1]))
        return ([f] if f else []), "id"
    k = _fkey(t)
    if not k:
        return [], "none"
    for how, table in (("name", cat["names"]), ("alias", cat["aliases"])):
        hit = table.get(k)
        if hit:
            act = [f for f in hit if f["active"]]
            return (act or hit), how
    d = cat["defaults"].get(k)
    if d:
        return [d], "default"
    words = [_sing(w) for w in k.split()]
    hits = []
    for f in cat["foods"]:
        if not f["active"]:
            continue
        toks = [_sing(x) for x in (_fkey(f["name"]) + " " + " ".join(_fkey(a) for a in f["aliases"] or [])).split()]
        if all(any(tok == w or tok.startswith(w) for tok in toks) for w in words):
            hits.append(f)
    if hits:
        return hits, "close"
    close = difflib.get_close_matches(k, [x for x in cat["names"] if any(f["active"] for f in cat["names"][x])], n=4, cutoff=0.6)
    return [cat["names"][c][0] for c in close], "fuzzy"


def _unit_rows(cur, food_id: int) -> list[dict]:
    cur.execute("SELECT unit, grams, source FROM food_units WHERE food_id=%s ORDER BY unit", (food_id,))
    return cur.fetchall()


def _to_amount(cur, f: dict, qty: float, unit: Optional[str], unit_g: Optional[float]) -> tuple[Optional[float], str, bool, Optional[str], Optional[dict]]:
    """(amount in the food's basis, portion text, estimated, problem, new_unit_row). Problem set = cannot convert."""
    u = (unit or "").strip()
    uk = _fkey(u)
    basis = "ml" if f["basis"] == "ml" else "g"
    if not u or uk in _UNIT_BASE:
        mult = _UNIT_BASE.get(uk, 1.0)
        if uk in ("l", "liter", "litre", "cl", "dl") and basis == "g":
            return None, "", False, f"'{u}' is a volume but {f['name']} is measured in g: give grams", None
        if uk in ("kg",) and basis == "ml":
            return None, "", False, f"'{u}' is a weight but {f['name']} is measured in ml", None
        amount = qty * mult
        return amount, ("" if not u or uk in ("g", "ml") else f"{_num(qty)} {u}"), False, None, None
    uk = _UNIT_SYN.get(uk, uk)
    uk = _sing(uk)
    rows = _unit_rows(cur, f["id"])
    exact = [r for r in rows if _sing(_UNIT_SYN.get(_fkey(r["unit"]), _fkey(r["unit"]))) == uk]
    hit = exact
    if not hit:
        part = [r for r in rows if uk in [_sing(x) for x in _fkey(r["unit"]).split()]]
        hit = part if len(part) == 1 else []
        if len(part) > 1:
            return None, "", False, f"'{u}' fits several stored units of {f['name']}: " + ", ".join(f"{r['unit']} ({_num(r['grams'])} g)" for r in part), None
    if not hit and (uk in _UNIT_GENERIC) and len(rows) == 1:
        hit = rows
    if len(hit) > 1:
        return None, "", False, f"'{u}' fits several stored units of {f['name']}", None
    portion = f"{_num(qty)} {u}"
    if unit_g:
        est = True
        newrow = None if hit else {"food_id": f["id"], "unit": uk if uk not in _UNIT_GENERIC else u.lower(), "grams": unit_g}
        return qty * unit_g, portion, est, None, newrow
    if hit:
        return qty * float(hit[0]["grams"]), portion, hit[0]["source"] == "estimate", None, None
    stored = ", ".join(f"{r['unit']} = {_num(r['grams'])} {basis}" for r in rows) or "none"
    return None, "", False, (f"no stored weight for '{u}' of {f['name']} (stored: {stored}). Ask for the amount in {basis}, "
                             f"or the weight of one {u} (then pass unit_g)"), None


def _words_in(term: str, name: str) -> bool:
    ws = [_sing(w) for w in _fkey(term).split()]
    toks = [_sing(t) for t in _fkey(name).split()]
    return bool(ws) and all(any(t == w or t.startswith(w) for t in toks) for w in ws)


def _silent_close(cat: dict, term: str, matches: list, how: str) -> Optional[dict]:
    """A close match may be used without asking only when it is unambiguous: exactly ONE active candidate, every word the
    user said is a whole word of its name or aliases (no prefix guessing), no raw/cooked/dry state, and not a calculated
    recipe or seed-mix food (a composite is never silently taken for a plain food). Anything else is read back."""
    if how != "close" or len(matches) != 1:
        return None
    f = matches[0]
    if not f["active"] or f.get("state") or f["source"] == "calculated" or f["id"] in cat["recipes"]:
        return None
    words = [_sing(w) for w in _fkey(term).split()]
    toks = {_sing(x) for x in (_fkey(f["name"]) + " " + " ".join(_fkey(a) for a in f["aliases"] or [])).split()}
    return f if words and all(w in toks for w in words) else None


def _use_counts(cur, ids: list[int]) -> dict:
    cur.execute("SELECT food_id, count(*) AS n FROM food_entries WHERE food_id = ANY(%s) GROUP BY food_id", (ids,))
    return {r["food_id"]: r["n"] for r in cur.fetchall()}


def _types(cur) -> tuple[list[dict], dict]:
    """The type registry (read every call, so types added in NocoDB work at once). Returns (rows, lookup by name/alias)."""
    cur.execute("""SELECT id, name, label, kind, category, unit, min1, max1, min2, max2, aliases, on_checklist, active
                   FROM log_types ORDER BY sort_order, name""")
    rows = cur.fetchall()
    idx: dict = {}
    for r in rows:
        for a in r["aliases"] or []:
            idx.setdefault(_norm(a), r)
    for r in rows:  # names win over aliases
        idx[_norm(r["name"])] = r
    return rows, idx


def _resolve_type(idx: dict, text: str) -> tuple[Optional[dict], Optional[int]]:
    key = _norm(text)
    t = idx.get(key)
    if not t:
        return None, None
    m = re.fullmatch(r".*\s([12])", key)  # "shoonya 2" is an alias of shoonya with session 2
    return t, (int(m[1]) if m and t["name"] == "shoonya" else None)
