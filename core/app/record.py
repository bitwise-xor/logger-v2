"""Recording one free-text message: read it with the AI parser, settle foods, amounts and times with the core's rules,
then check and save with v1's code. Food and health for now; thoughts, private entries, to-dos and labs follow.

Outcomes follow the core contract: saved; partly_saved (the settled items are saved, questions about the rest);
needs_choice (only questions, nothing saved); needs_confirmation (v1's read-back: an odd value, nothing saved); rejected."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import v1_log
from .ai import parser
from .ai.provider import Provider, Usage
from .clock import read_when
from .foods import amount, pick
from .v1_food import _food_cat, _resolve_type, _types
from .v1_log import Entry, FoodItem, MealUse, ReadBack, Reject

ESCALATION_MODEL = "claude-sonnet-5-5"
EXTRACTOR = "v2-ai-1"
v1_log.FOOD_VERSION = v1_log.HEALTH_VERSION = EXTRACTOR  # rows saved by v2 say so (v1's insert code reads these names)


@dataclass
class Reading:
    """A parser answer after the core's rules: ready-to-check v1 items, or questions for the user."""
    pred: dict
    foods: list[FoodItem] = field(default_factory=list)
    meals: list[MealUse] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _iso(ts: datetime, date_only: bool) -> str:
    return ts.strftime("%Y-%m-%d") if date_only else ts.strftime("%Y-%m-%d %H:%M")  # v1's parse_when reads both


def settle(cur, cat: dict, types: dict, pred: dict, now: datetime) -> Reading:
    """Turn the parser's words into exact foods (N ids), grams and times; collect what the core cannot settle alone."""
    r = Reading(pred)
    for it in pred.get("food", []):
        f, how, cands = pick(cur, cat, it["food"], it["cooked"], before=now)
        if not f:
            r.questions.append(f"which food is '{it['food']}'? " + ("; ".join(f"N{c['id']} {c['name']}" for c in cands[:6])
                                                                 if cands else "nothing in the catalogue fits"))
            continue
        if it["amount"] is None:
            r.questions.append(f"how much {f['name']}?")
            continue
        grams, est, prob = amount(cur, f, it["amount"], it["unit"], it["grams_per_unit"])
        if prob:
            r.questions.append(f"{f['name']}: {prob}")
            continue
        ts, date_only, tprob = read_when(it["when"], now)
        if tprob:
            r.questions.append(tprob)
            continue
        if how in ("close", "usual", "state"):
            r.notes.append(f"'{it['food']}' -> N{f['id']} {f['name']}" + (" (your usual)" if how == "usual" else ""))
        r.foods.append(FoodItem(food=f"N{f['id']}", amount=grams, when=_iso(ts, date_only), note=it["note"],
                                estimated=bool(est), drink=it["drink"]))
    for mu in pred.get("usual_meals", []):
        ts, date_only, tprob = read_when(mu["when"], now)
        if tprob:
            r.questions.append(tprob)
            continue
        r.meals.append(MealUse(name=mu["name"], when=_iso(ts, date_only), skip=mu["skip"],
                               amounts={c["food"]: c["grams"] for c in mu["changes"]}))
    for h in pred.get("health", []):
        t, _ = _resolve_type(types, h["type"])
        if not t:
            r.questions.append(f"'{h['type']}' is not a known health type or habit")
            continue
        ts, date_only, tprob = read_when(h["when"], now)
        if tprob:
            r.questions.append(tprob)
            continue
        r.entries.append(Entry(type=t["name"], value=h["value"], value2=h["value2"], when=_iso(ts, date_only),
                               period=h["period"] if h["period"] in ("morning", "evening") else None, session=h["session"]))
    return r


def read(cur, provider: Provider, text: str, now: datetime, previous=(), meals=(), pred: Optional[dict] = None,
         escalate: bool = True) -> tuple[Reading, list[Usage], bool]:
    """Parse with the small model (or take a given answer), settle it, and when something stays open, read the message
    once more with the larger model and keep whichever reading leaves fewer questions.
    -> (reading, usages, escalated)."""
    cat, (_, types) = _food_cat(cur), _types(cur)
    naive = now.replace(tzinfo=None)
    usages = []
    if pred is None:
        pred, u = parser.parse(provider, text, naive, previous, meals)
        usages.append(u)
    r = settle(cur, cat, types, pred, now)
    if not (escalate and r.questions):
        return r, usages, False
    pred2, u2 = parser.parse(provider, text, naive, previous, meals, model=ESCALATION_MODEL)
    usages.append(u2)
    r2 = settle(cur, cat, types, pred2, now)
    return (r2 if len(r2.questions) < len(r.questions) else r), usages, True


def save(cur, r: Reading, text: str, channel: str, now: datetime, confirmed: bool = False) -> dict:
    """Check with v1's rules and save in the caller's transaction. confirmed=True is the user's yes to a read-back.
    What is settled is saved and only the open items become questions (decided 25.10.2026; v1 saved nothing then).
    An odd value (v1's read-back) still holds the whole message until the user confirms.
    The logging day never stops a message: without a wake-up the 05:00 rule is used (v1's chat rule, no_wake)."""
    if not (r.foods or r.meals or r.entries):
        if not r.questions:
            return {"outcome": "nothing", "raw_id": None}
        cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES (%s, %s, %s::jsonb) RETURNING id",
                    (channel, text, json.dumps({"unprocessed": True, "questions": r.questions})))
        return {"outcome": "needs_choice", "raw_id": cur.fetchone()["id"], "questions": r.questions}
    for x in r.foods + r.entries:
        x.confirmed = confirmed
    try:
        prepared = v1_log._prepare_entries(cur, r.entries, now) if r.entries else []
        food = v1_log._prepare_food(cur, r.foods, r.meals, [], None, True, None, now, prepared) if (r.foods or r.meals) else None
    except ReadBack as e:
        return {"outcome": "needs_confirmation", "raw_id": None, "read_back": str(e)}
    except Reject as e:
        return {"outcome": "rejected", "raw_id": None, "reason": str(e)}
    meta = {"questions": r.questions} if r.questions else {}
    cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES (%s, %s, %s::jsonb) RETURNING id",
                (channel, text, json.dumps(meta)))
    raw_id = cur.fetchone()["id"]
    out: list[str] = []
    notes = list(r.notes)
    if prepared:
        v1_log._insert_entries(cur, raw_id, prepared, out, notes)
    if food:
        v1_log._insert_food(cur, raw_id, food, out, notes)
    return {"outcome": "partly_saved" if r.questions else "saved", "raw_id": raw_id, "receipt": out, "notes": notes,
            "questions": r.questions}
