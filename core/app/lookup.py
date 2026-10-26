"""Looking up a food the catalogue does not know (decided 26.10.2026): by name in the local USDA copy (plain foods first)
and Open Food Facts (branded and German products first), or by barcode in Open Food Facts. A small AI call picks the
matching entry or none; the user confirms the values once; then the food is added with its source, barcode and USDA
portion weights, and the waiting item of the message is saved."""
import pathlib
import re
from datetime import datetime
from typing import Optional

from psycopg.types.json import Jsonb

from . import foodfacts, foods, record, usda
from .ai.parser import BOOL, STR, _null, _obj
from .ai.provider import Provider, ProviderError, Usage
from .foodfacts import FoodFactsError
from .v1_food import _food_cat
from .v1_log import NewFood

PICK_MODEL = "claude-haiku-5-5"
PICK_PROMPT = (pathlib.Path(__file__).parent / "ai" / "prompts" / "food_pick.md").read_text()
PICK_SCHEMA = _obj({"pick": _null(STR), "name": _null(STR),
                    "state": _null({"type": "string", "enum": ["raw", "cooked", "dry"]}), "drink": BOOL})
NUTRIENTS = ("kcal", "protein", "fat", "sat_fat", "carbs", "fibre", "sugar_total", "sodium_mg", "caffeine_mg")
# USDA's portion words -> the catalogue's units ("medium (2-3/4\" dia)" -> medium)
UNITS = {"cup": "cup", "tbsp": "tbsp", "tsp": "tsp", "slice": "slice", "small": "small", "medium": "medium",
         "large": "large", "piece": "piece", "fruit": "piece", "whole": "piece", "clove": "clove", "stalk": "stalk"}


def _n(x) -> str:
    return f"{float(x):.1f}".rstrip("0").rstrip(".")


def per100(v: dict) -> str:
    return (f"{_n(v['kcal'])} kcal, P {_n(v['protein'])}, F {_n(v['fat'])}"
            + (f" (sat {_n(v['sat_fat'])})" if v.get("sat_fat") is not None else "") + f", C {_n(v['carbs'])}"
            + (f", sugar {_n(v['sugar_total'])}" if v.get("sugar_total") is not None else "")
            + (f", sodium {_n(v['sodium_mg'])} mg" if v.get("sodium_mg") is not None else ""))


def _brands(b) -> str:
    return ", ".join(b) if isinstance(b, list) else (b or "")


def _candidates(cur, word: str, en: str, notes: list[str]) -> list[dict]:
    out = [dict(id=f"U{i}", src="usda", label=r["description"], fdc_id=r["fdc_id"],
                values={k: float(r[k]) for k in NUTRIENTS if r[k] is not None})
           for i, r in enumerate(usda.search(cur, en or word, 20), 1)]
    try:
        hits = foodfacts.search(word, 8)
    except FoodFactsError as e:
        notes.append(f"{e}; only USDA was searched")
        hits = []
    for h in hits:
        v = foodfacts.values(h.get("nutriments") or {})
        if v:
            name = h.get("product_name") or h.get("product_name_en") or "?"  # the English field is sometimes misused
            out.append(dict(id=f"O{len(out) + 1}", src="off", label=f"{name} ({_brands(h.get('brands'))})", code=h.get("code"),
                            brand=_brands(h.get("brands")), values=v, germany="en:germany" in (h.get("countries_tags") or [])))
    return out


def _pick(provider: Provider, word: str, en: str, text: str, cands: list[dict]) -> tuple[dict, Usage]:
    lines = [f"{c['id']} {c['label']} ({'USDA' if c['src'] == 'usda' else 'Open Food Facts' + (', Germany' if c.get('germany') else '')}"
             f"; {per100(c['values'])} per 100 g)" for c in cands]
    user = "\n".join([f"Word: {word}", f"English: {en}", "Candidates:", *lines, "", "Message:", '"""', text, '"""'])
    return provider.json(PICK_PROMPT, user, PICK_SCHEMA, PICK_MODEL, max_tokens=1500)


def _finish(cur, c: dict, p: dict) -> dict:
    """The chosen entry as a food to add: values per 100 g, source note, name, and what the user is shown."""
    v = dict(c["values"])
    if c["src"] == "off" and c.get("code"):
        try:  # the search index lacks salt: the full product has it
            full = foodfacts.product(c["code"])
            v = foodfacts.values((full or {}).get("nutriments") or {}) or v
        except FoodFactsError:
            pass
    note = (f"USDA FoodData Central SR Legacy (fdc {c['fdc_id']}, '{c['label']}')" if c["src"] == "usda"
            else f"Open Food Facts {c['code']} ({c['label']})")
    cand = dict(name=p.get("name") or c["label"], src=c["src"], label=c["label"], fdc_id=c.get("fdc_id"), barcode=c.get("code"),
                brand=c.get("brand") or None, values={**v, "source": "database", "source_note": note,
                                                      **({"state": p["state"]} if p.get("state") else {}), "drink": bool(p.get("drink"))})
    cur.execute("SELECT id, name FROM foods WHERE lower(name) = lower(%s) AND active", (cand["name"],))
    have = cur.fetchone()
    if have:
        cand["existing"] = have["id"]
        cand["offer"] = f"Your catalogue already has N{have['id']} {have['name']}"
        return cand
    try:
        similar = record._similar(cur, _food_cat(cur), cand["name"], NewFood(**{k: v for k, v in cand["values"].items() if k != "drink"}))
    except Exception:  # noqa: BLE001 - impossible values: v1's checks refuse them when the food is added
        similar = []
    cand["offer"] = (f"{'USDA' if c['src'] == 'usda' else 'Open Food Facts'} has {c['label']}: {per100(v)} per 100 g"
                     f" (it would be added as \"{cand['name']}\")"
                     + (". Similar foods you already have: " + "; ".join(f"N{s['id']} {s['name']}" for s in similar)
                        + " (answer with the N number to use one)" if similar else ""))
    return cand


def by_name(cur, provider: Provider, word: str, en: str, text: str) -> tuple[Optional[dict], list[Usage], list[str]]:
    notes: list[str] = []
    cands = _candidates(cur, word, en, notes)
    if not cands:
        return None, [], notes
    p, u = _pick(provider, word, en, text, cands)
    c = next((c for c in cands if c["id"] == p.get("pick")), None)
    return (_finish(cur, c, p) if c else None), [u], notes


def by_barcode(cur, provider: Provider, code: str) -> tuple[Optional[dict], list[Usage], str]:
    """-> (candidate, usages, reason when there is none)."""
    code = re.sub(r"\D", "", code or "")
    if len(code) < 8:
        return None, [], "that is not a barcode (8 to 14 digits)"
    cur.execute("SELECT id, name FROM foods WHERE barcode = %s AND active", (code,))
    have = cur.fetchone()
    if have:
        return dict(existing=have["id"], name=have["name"], offer=f"Your catalogue already has N{have['id']} {have['name']}"), [], ""
    prod = foodfacts.product(code)
    if not prod or not (prod.get("product_name") or prod.get("product_name_en") or prod.get("nutriments")):  # empty entries
        return None, [], f"barcode {code} is not in Open Food Facts: send a photo of the label instead"
    v = foodfacts.values(prod.get("nutriments") or {})
    label = f"{prod.get('product_name') or prod.get('product_name_en') or '?'} ({_brands(prod.get('brands'))})"
    if not v:
        return None, [], f"Open Food Facts knows {label} but not all its values: send a photo of the label instead"
    c = dict(id="O1", src="off", label=label, code=code, brand=_brands(prod.get("brands")), values=v)
    p, u = _pick(provider, prod.get("product_name") or label, prod.get("product_name_en") or "", f"barcode {code}", [c])
    return _finish(cur, c, {**p, "name": p.get("name") or label}), [u], ""


def accept(cur, cand: dict, now: datetime, question_id: Optional[str] = None, word: Optional[str] = None) -> dict:
    """The user's yes: add the food (v1's checks still refuse impossible values), save the waiting item, store the
    barcode, brand and USDA portion weights, and make the user's word find it."""
    if cand.get("existing"):
        return record.answer(cur, question_id, f"N{cand['existing']}", now) if question_id else \
            {"outcome": "saved", "receipt": [cand["offer"]]}
    out = record.add_food(cur, cand["name"], dict(cand["values"]), "catalogue", now, question_id, confirmed=True)
    if out["outcome"] not in ("saved", "partly_saved") or not out.get("food_id"):
        return out
    fid = out["food_id"]
    cur.execute("UPDATE foods SET barcode = coalesce(%s, barcode), brand = coalesce(%s, brand), carbs_include_fibre = %s WHERE id = %s",
                (cand.get("barcode"), cand.get("brand"), cand["src"] == "usda", fid))
    units = []
    for po in usda.portions(cur, cand["fdc_id"]) if cand.get("fdc_id") else []:
        unit = UNITS.get(re.split(r"[ ,(]", po["unit"].lower())[0])
        if unit and unit not in [u for u, _ in units]:
            units.append((unit, float(po["grams"]) / float(po["amount"])))
    if units and "piece" not in dict(units) and "medium" in dict(units):
        units.append(("piece", dict(units)["medium"]))
    for unit, grams in units:
        cur.execute("""INSERT INTO food_units (food_id, unit, grams, source) VALUES (%s, %s, %s, 'database')
                       ON CONFLICT (food_id, unit) DO NOTHING""", (fid, unit, round(grams, 1)))
    if units:
        out.setdefault("receipt", []).append("unit weights from USDA: " + ", ".join(f"{u} {_n(g)} g" for u, g in units))
    if word:
        w = record.add_words(cur, fid, [word])
        out.setdefault("notes", []).extend(w["receipt"] + w["notes"])
    return out


def offer(cur, provider: Provider, out: dict, text: str) -> list[Usage]:
    """For each food question the catalogue could not answer at all, look the food up and offer what was found."""
    usages = []
    for q in out.get("questions", []):
        cur.execute("SELECT * FROM pending WHERE id = %s", (q["pending_id"],))
        p = cur.fetchone()
        pl = p["payload"] if p else {}
        if not p or p["tool"] != "log" or pl.get("kind") != "food":
            continue
        it = pl["item"]
        cat = _food_cat(cur)
        have = [cat["by_id"][i] for i in pl.get("candidates") or [] if i in cat["by_id"]]
        if have and not all(foods._other_product(it["food"], f) for f in have):
            continue  # real candidates: the user picks one of them
        try:
            cand, used, notes = by_name(cur, provider, it["food"], it.get("food_en") or it["food"], text)
        except (ProviderError, FoodFactsError):  # a failed lookup never stops the message: the plain question stays
            continue
        usages += used
        if not cand:
            continue
        ask = (f"'{it['food']}' is not in your catalogue" + (" (only " + "; ".join(f"N{f['id']} {f['name']}" for f in have)
                                                             + ", answer with its N number if you meant it)" if have else "")
               + f". {cand['offer']}. Add it (yes), send a label photo or barcode, or skip.")
        cur.execute("UPDATE pending SET payload = payload || jsonb_build_object('lookup', %s::jsonb), reason = %s WHERE id = %s",
                    (Jsonb(cand), ask[:500], p["id"]))
        q["question"] = ask
    return usages