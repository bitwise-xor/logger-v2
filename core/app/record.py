"""Recording one free-text message: read it with the AI parser, settle foods, amounts and times with the core's rules,
then check and save with v1's code. Food, health, thoughts and private entries; to-dos (Todoist) and labs follow.

Outcomes follow the core contract: saved; partly_saved (the settled items are saved, questions about the rest);
needs_choice (only questions, nothing saved yet); needs_confirmation (v1's read-back: an odd value, nothing saved);
rejected; not_logged (a question or command for the assistant); kept (raw text only: to-dos until Todoist is in the
core). Each question is a row in `pending`, answered with answer(); the answer saves the missing item into the same
message."""
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from . import v1_log, v1_thoughts
from .ai import parser
from .ai.provider import Provider, Usage
from .clock import read_when
from .foods import amount, pick
from .v1_food import _find_foods, _fkey, _food_cat, _resolve_type, _types
from .v1_log import Entry, FoodItem, MealUse, NewFood, ReadBack, Reject
from .v1_thoughts import Thought

ESCALATION_MODEL = "claude-sonnet-5-5"
EXTRACTOR = "v2-ai-1"
QUESTION_HOURS = 72  # an open question can be answered for three days
v1_log.FOOD_VERSION = v1_log.HEALTH_VERSION = v1_thoughts.EXTRACTOR_VERSION = EXTRACTOR  # rows saved by v2 say so


@dataclass
class Reading:
    """A parser answer after the core's rules: ready-to-check v1 items, or questions for the user."""
    pred: dict
    foods: list[FoodItem] = field(default_factory=list)
    meals: list[MealUse] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    thoughts: list[Thought] = field(default_factory=list)
    private: Optional[str] = None  # "private" or "gratitude": the message goes to the private store only
    questions: list[dict] = field(default_factory=list)  # {kind, text, item, candidates}
    notes: list[str] = field(default_factory=list)


def _iso(ts: datetime, date_only: bool) -> str:
    return ts.strftime("%Y-%m-%d") if date_only else ts.strftime("%Y-%m-%d %H:%M")  # v1's parse_when reads both


def _food_item(cur, cat: dict, it: dict, now: datetime, f: Optional[dict] = None, how: str = "id",
               cands: list = ()) -> tuple[Optional[FoodItem], Optional[dict], Optional[str]]:
    """One parsed food item -> (v1 FoodItem, question, note). f is given when the user already chose the food.
    The user's word is tried first. The parser's English name ("frischkäse" -> "cream cheese") is only suggested,
    never taken silently: a translation can land on a different product (körniger Frischkäse is not cream cheese)."""
    if f is None:
        f, how, cands = pick(cur, cat, it["food"], it["cooked"], before=now)
        en = (it.get("food_en") or "").strip()
        if not f and en and en.lower() != it["food"].strip().lower():
            f2, how2, cands2 = pick(cur, cat, en, it["cooked"], before=now)
            sugg = [f2] if f2 else cands2[:6]
            if sugg:
                text = (f"Which food is '{it['food']}'? By its English name: "
                        + "; ".join(f"N{c['id']} {c['name']}" for c in sugg) + ". Or add it as a new food.")
                return None, dict(kind="food", text=text, item=it, candidates=[c["id"] for c in sugg]), None
    if not f:
        text = f"Which food is '{it['food']}'? " + ("; ".join(f"N{c['id']} {c['name']}" for c in cands[:6])
                                                    if cands else "Nothing in the catalogue fits.")
        return None, dict(kind="food", text=text, item=it, candidates=[c["id"] for c in cands[:6]]), None
    if it["amount"] is None:
        return None, dict(kind="amount", text=f"How much {f['name']}, in {f['basis']}?", item=it, food_id=f["id"]), None
    grams, est, prob = amount(cur, f, it["amount"], it["unit"], it["grams_per_unit"])
    if prob:
        return None, dict(kind="amount", text=f"{f['name']}: {prob}", item=it, food_id=f["id"]), None
    ts, date_only, tprob = read_when(it["when"], now)
    if tprob:
        return None, dict(kind="time", text=tprob, item=it, food_id=f["id"]), None
    note = None
    if how in ("close", "usual", "state"):
        note = f"'{it['food']}' -> N{f['id']} {f['name']}" + (" (your usual)" if how == "usual" else "")
    return FoodItem(food=f"N{f['id']}", amount=grams, when=_iso(ts, date_only), note=it["note"], estimated=bool(est),
                    drink=it["drink"]), None, note


def settle(cur, cat: dict, types: dict, pred: dict, now: datetime) -> Reading:
    """Turn the parser's words into exact foods (N ids), grams and times; collect what the core cannot settle alone."""
    r = Reading(pred)
    if pred.get("private"):  # private and gratitude entries hold one thought and nothing else
        r.private = pred["private"]
        r.thoughts = [Thought(summary=t["summary"], thought_type=t["thought_type"], area=t["area"], tags=t["tags"][:3])
                      for t in pred.get("thoughts", [])[:1]]
        return r
    for it in pred.get("food", []):
        item, q, note = _food_item(cur, cat, it, now)
        if q:
            r.questions.append(q)
        else:
            r.foods.append(item)
            if note:
                r.notes.append(note)
    for mu in pred.get("usual_meals", []):
        ts, date_only, tprob = read_when(mu["when"], now)
        if tprob:
            r.questions.append(dict(kind="time", text=tprob, item=mu))
            continue
        r.meals.append(MealUse(name=mu["name"], when=_iso(ts, date_only), skip=mu["skip"],
                               amounts={c["food"]: c["grams"] for c in mu["changes"]}))
    for h in pred.get("health", []):
        t, _ = _resolve_type(types, h["type"])
        if not t:
            r.questions.append(dict(kind="type", text=f"'{h['type']}' is not a known health type or habit. "
                                                      "Is it a habit to add, or a thought?", item=h))
            continue
        ts, date_only, tprob = read_when(h["when"], now)
        if tprob:
            r.questions.append(dict(kind="time", text=tprob, item=h))
            continue
        r.entries.append(Entry(type=t["name"], value=h["value"], value2=h["value2"], when=_iso(ts, date_only),
                               period=h["period"] if h["period"] in ("morning", "evening") else None, session=h["session"]))
    r.thoughts = [Thought(summary=t["summary"], thought_type=t["thought_type"], area=t["area"], tags=t["tags"][:3],
                          project=t["project"]) for t in pred.get("thoughts", [])]
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


def _ask(cur, raw_id: int, questions: list[dict], now: datetime) -> list[dict]:
    """Store each open question in `pending`; the answer comes back through answer()."""
    out = []
    for q in questions:
        pid = "p" + secrets.token_hex(4)
        cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'log', %s, %s, %s)",
                    (pid, Jsonb({**q, "raw_id": raw_id, "now": now.isoformat()}), q["text"], now + timedelta(hours=QUESTION_HOURS)))
        out.append({"pending_id": pid, "question": q["text"]})
    return out


def _save_private(cur, r: Reading, text: str, channel: str, now: datetime) -> dict:
    """The private store only, never the general inbox (v1's rule: fail closed). Labels found by the parser are
    stored as such, whether or not the message starts with 'private:' or 'gratitude:'."""
    th = r.thoughts[0] if r.thoughts else None
    cur.execute("""INSERT INTO private.entries (channel, label, content, summary, thought_type, area, tags)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (channel, r.private, text, th.summary if th else None, th.thought_type if th else None,
                 th.area if th else None, th.tags if th else []))
    pid = cur.fetchone()["id"]
    return {"outcome": "saved", "raw_id": None, "private_id": pid, "receipt": [f"+ P{pid} ({r.private}), private store only"],
            "notes": [] if re.match(r"^\s*(private|gratitude)\s*:", text, re.I) else
            [f"saved as {r.private} without a label; say 'not private' if it should be a normal entry"], "questions": []}


def save(cur, r: Reading, text: str, channel: str, now: datetime, confirmed: bool = False) -> dict:
    """Check with v1's rules and save in the caller's transaction. confirmed=True is the user's yes to a read-back.
    What is settled is saved and only the open items become questions (decided 25.10.2026; v1 saved nothing then).
    An odd value (v1's read-back) still holds the whole message until the user confirms.
    The logging day never stops a message: without a wake-up the 05:00 rule is used (v1's chat rule, no_wake)."""
    if r.pred.get("intent") in ("question", "command") and not (r.foods or r.meals or r.entries or r.thoughts or r.private):
        return {"outcome": "not_logged", "raw_id": None, "questions": []}
    if r.private:
        try:
            return _save_private(cur, r, text, channel, now)
        except Exception as e:  # noqa: BLE001 - fail closed: never fall back to the general inbox
            raise Reject(f"the private entry was not saved ({type(e).__name__}); nothing went to the normal inbox") from e
    for x in r.foods + r.entries:
        x.confirmed = confirmed
    try:
        prepared = v1_log._prepare_entries(cur, r.entries, now) if r.entries else []
        food = v1_log._prepare_food(cur, r.foods, r.meals, [], None, True, None, now, prepared) if (r.foods or r.meals) else None
    except ReadBack as e:
        return {"outcome": "needs_confirmation", "raw_id": None, "read_back": str(e)}
    except Reject as e:
        return {"outcome": "rejected", "raw_id": None, "reason": str(e)}
    meta = {"unprocessed": True} if r.questions and not (prepared or food or r.thoughts) else {}
    cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES (%s, %s, %s::jsonb) RETURNING id",
                (channel, text, json.dumps(meta)))
    raw_id = cur.fetchone()["id"]
    out: list[str] = []
    notes = list(r.notes)
    thoughts = r.thoughts
    if not (prepared or food or thoughts or r.questions or r.pred.get("todos")):
        # v1: a log message with nothing extracted is kept as one unsorted thought, flagged for review
        thoughts = [Thought(summary=v1_thoughts._short(text, 140), needs_review=True,
                            review_reason="nothing extracted by the parser; unsorted")]
    if prepared:
        v1_log._insert_entries(cur, raw_id, prepared, out, notes)
    if food:
        v1_log._insert_food(cur, raw_id, food, out, notes)
    ids: list[int] = []
    for th in thoughts:
        ids.append(v1_thoughts._log_thought(cur, raw_id, text, th, now, out, notes, ids))
    asked = _ask(cur, raw_id, r.questions, now)
    if prepared or food or thoughts:
        outcome = "partly_saved" if asked else "saved"
    else:  # only questions, or only to-dos (kept as raw text until Todoist is in the core)
        outcome = "needs_choice" if asked else "kept"
    return {"outcome": outcome, "raw_id": raw_id, "receipt": out, "notes": notes, "questions": asked}


def answer(cur, pending_id: str, reply: str, now: datetime) -> dict:
    """The user's answer to one open question. A food question takes an N id or a word; an amount question takes
    grams (or ml); "skip" drops the item. The item is saved into the same message, with its original time."""
    cur.execute("SELECT * FROM pending WHERE id = %s AND tool = 'log' FOR UPDATE", (pending_id,))
    p = cur.fetchone()
    if not p or p["resolved_at"] is not None or p["expires_at"] < now:
        return {"outcome": "rejected", "reason": "that question does not exist, was already answered, or has expired"}
    q = p["payload"]
    if reply.strip().lower() in ("skip", "cancel", "no", "none"):
        cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'cancelled' WHERE id = %s", (pending_id,))
        return {"outcome": "cancelled"}
    if q["kind"] not in ("food", "amount"):
        return {"outcome": "rejected", "reason": f"answers to '{q['kind']}' questions are not supported yet: log the item again"}
    cat = _food_cat(cur)
    asked_at = datetime.fromisoformat(q["now"])
    it = dict(q["item"])
    alias_note = None
    if q["kind"] == "food":
        m = re.fullmatch(r"\s*[nN]?#?(\d+)\s*", reply)
        if m:
            f = cat["by_id"].get(int(m[1]))
            if not f:
                return {"outcome": "rejected", "reason": f"there is no food N{m[1]}"}
        else:
            f, how, cands = pick(cur, cat, reply, it["cooked"])
            if not f:
                return {"outcome": "needs_choice", "question": f"'{reply}' fits several or no foods: "
                        + "; ".join(f"N{c['id']} {c['name']}" for c in cands[:6])
                        + ". If it is a new food, add it with add_food and this question id."}
        if f["id"] in (q.get("candidates") or []):  # the user picked one of the foods suggested for this word
            alias_note = _alias(cur, cat, f, it["food"])
        else:
            alias_note = _word_hint(f, it["food"])
    else:
        f = cat["by_id"][q["food_id"]]
        num = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(g|ml)?\s*", reply)
        if not num:
            return {"outcome": "rejected", "reason": f"give the amount in {f['basis']}, for example 120"}
        it.update(amount=float(num[1].replace(",", ".")), unit=None, grams_per_unit=None)
    item, q2, _ = _food_item(cur, cat, it, asked_at, f=f)
    if q2:
        return {"outcome": "needs_choice", "question": q2["text"]}
    try:
        prep = v1_log._prepare_food(cur, [item], [], [], None, True, None, asked_at, [])
    except ReadBack as e:
        return {"outcome": "needs_confirmation", "read_back": str(e)}
    out: list[str] = []
    notes: list[str] = [alias_note] if alias_note else []
    v1_log._insert_food(cur, q["raw_id"], prep, out, notes)
    cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (pending_id,))
    return {"outcome": "saved", "raw_id": q["raw_id"], "receipt": out, "notes": notes}


def _word_hint(f: dict, word: str) -> str:
    return f"to let '{word.strip()}' find N{f['id']} {f['name']} next time, save it with food_words"


def _similar(cur, cat: dict, name: str, nf: NewFood) -> list[dict]:
    """Foods that look like the one about to be created: by name ("Salad greens, mixed" vs N176 "Salad greens, mixed,
    raw"), by the base name before the first comma ("Cottage cheese, half-fat" vs "Cottage cheese, semi-fat"), or by
    the same values per 100 g (the same label added twice under another name)."""
    seen, out = set(), []
    for term in (name, re.sub(r"\(.*?\)", " ", name), name.split(",")[0]):
        matches, how = _find_foods(cat, term)
        for m in matches:
            if how != "fuzzy" and m["id"] not in seen and m["active"]:
                seen.add(m["id"])
                out.append(m)
    cur.execute("""SELECT id FROM foods WHERE active AND basis = %s AND kcal = %s AND protein = %s AND fat = %s AND carbs = %s
                   ORDER BY id""", (nf.basis, nf.kcal, nf.protein, nf.fat, nf.carbs))
    for r in cur.fetchall():
        if r["id"] not in seen and r["id"] in cat["by_id"]:
            seen.add(r["id"])
            out.append(cat["by_id"][r["id"]])
    return out[:6]


def _alias(cur, cat: dict, f: dict, word: str) -> Optional[str]:
    """v1's rule: after a pick for a word the catalogue did not know, save the word as an alias of that food.
    v2 applies it only when the user picked one of the foods the question suggested for that very word."""
    k = _fkey(word)
    if not k or k in cat["names"] or k in cat["aliases"] or k in cat["defaults"]:
        return None
    cur.execute("UPDATE foods SET aliases = array_append(aliases, %s) WHERE id = %s", (word.strip().lower(), f["id"]))
    return f"'{word.strip()}' saved as a word for N{f['id']} {f['name']}"


def add_words(cur, food_id: int, words, means: bool = False) -> dict:
    """Words for a food in the catalogue. Each word becomes an alias, so it finds this food; a word another food
    already uses is refused (v1's rule). means=True makes the word the user's default for this product instead
    ("waffle now means N250"): it then wins over other foods with that name or alias."""
    cat = _food_cat(cur)
    f = cat["by_id"].get(food_id)
    if not f:
        return {"outcome": "rejected", "reason": f"there is no food N{food_id}"}
    out, notes = [], []
    for w in words or []:
        word = " ".join(str(w).split()).lower()
        k = _fkey(word)
        if not k:
            continue
        if means:
            cur.execute("""INSERT INTO food_defaults (term, food_id) VALUES (%s, %s)
                           ON CONFLICT (term) DO UPDATE SET food_id = EXCLUDED.food_id""", (word, food_id))
            out.append(f"'{word}' now means N{food_id} {f['name']}")
            continue
        users = {x["id"] for x in cat["names"].get(k, []) + cat["aliases"].get(k, [])}
        if food_id in users:
            notes.append(f"'{word}' already finds N{food_id}")
        elif users:
            other = cat["by_id"][min(users)]
            notes.append(f"'{word}' is already used by N{other['id']} {other['name']}; not added. "
                         f"To make it mean this food anyway, use it with means")
        else:
            cur.execute("UPDATE foods SET aliases = array_append(aliases, %s) WHERE id = %s", (word, food_id))
            cat["aliases"][k].append(f)
            out.append(f"'{word}' now finds N{food_id} {f['name']}")
    return {"outcome": "saved", "food_id": food_id, "receipt": out, "notes": notes}


def add_food(cur, name: str, values: dict, channel: str, now: datetime, question_id: Optional[str] = None,
             food_code: Optional[str] = None, confirmed: bool = False, aliases=()) -> dict:
    """A food missing from the catalogue, with values per 100 g (or 100 ml), through v1's own new-food code and checks:
    impossible values are rejected; odd values, and every label, need the user's yes (v1 showed a label read-back table
    with a code). With question_id the waiting item of that message is saved with the new food and the user's word
    becomes an alias; without it the food is only added to the catalogue (v1's rule: log 1 g, then remove the entry)."""
    values = dict(values)
    drink = bool(values.pop("drink", False))
    try:
        nf = NewFood(**values)
    except ValidationError as e:
        return {"outcome": "rejected", "reason": "; ".join(err["msg"] for err in e.errors())}
    q = None
    when = now
    if question_id:
        cur.execute("SELECT * FROM pending WHERE id = %s AND tool = 'log' FOR UPDATE", (question_id,))
        p = cur.fetchone()
        if not p or p["resolved_at"] is not None or p["expires_at"] < now or p["payload"]["kind"] != "food":
            return {"outcome": "rejected", "reason": "that food question does not exist, was already answered, or has expired"}
        q = p["payload"]
        it = q["item"]
        if it["amount"] is None:
            return {"outcome": "rejected", "reason": "the waiting item has no amount: log it again with the amount"}
        when = datetime.fromisoformat(q["now"])
        ts, date_only, tprob = read_when(it["when"], when)
        item = FoodItem(food=name, amount=it["amount"], unit=it["unit"], unit_g=it["grams_per_unit"], when=_iso(ts, date_only),
                        note=it["note"], drink=it["drink"] if it["drink"] is not None else drink, new=nf, confirmed=confirmed)
    else:
        item = FoodItem(food=name, amount=1, when=_iso(now, False), drink=drink, new=nf, confirmed=confirmed)
    similar = [] if confirmed else _similar(cur, _food_cat(cur), name, nf)
    read_back, code = None, None
    try:
        prep = v1_log._prepare_food(cur, [item], [], [], None, True, food_code, when, [])
    except ReadBack as e:
        read_back = str(e)
        m = re.search(r'food_code="([0-9A-F]{6})"', read_back)
        code = m[1] if m else None
    except Reject as e:
        return {"outcome": "rejected", "reason": str(e)}
    if similar or read_back:  # one read-back for both: similar foods, and v1's label table or odd values
        text = ("Similar foods already exist: " + "; ".join(f"N{s['id']} {s['name']}" for s in similar)
                + ". If it is one of them, use that N number instead (answer the food question with it); "
                  "answer yes only to create the new food anyway.") if similar else ""
        return {"outcome": "needs_confirmation", "read_back": "\n".join(x for x in (text, read_back) if x), "code": code}
    if q:
        raw_id = q["raw_id"]
    else:
        cur.execute("INSERT INTO raw_inbox (channel, content) VALUES (%s, %s) RETURNING id", (channel, f"add food: {name}"))
        raw_id = cur.fetchone()["id"]
    out: list[str] = []
    notes: list[str] = []
    v1_log._insert_food(cur, raw_id, prep, out, notes)
    fid = next(iter(prep["new"].values()))["id"]
    if q:  # the question's word is not saved as an alias here: the new food may answer a different question
        cur.execute("SELECT id, name FROM foods WHERE id = %s", (fid,))
        notes.append(_word_hint(cur.fetchone(), q["item"]["food"]))
        cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
    else:  # catalogue only: nothing was eaten
        cur.execute("DELETE FROM food_entries WHERE raw_id = %s", (raw_id,))
        out = [line for line in out if "NEW food" in line]
    if aliases:
        w = add_words(cur, fid, aliases)
        out += w["receipt"]
        notes += w["notes"]
    return {"outcome": "saved", "raw_id": raw_id, "food_id": fid, "receipt": out, "notes": notes}
