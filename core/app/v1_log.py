"""Saving food and health entries. Copied unchanged from logger-app connector/server.py (v1), in v1's order;
helpers that v2 already has come from v1_food.py and when.py. v2 code calls these from record.py."""
import difflib
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .v1_food import (_UNIT_BASE, _UNIT_GENERIC, _UNIT_SYN, _find_foods, _fkey, _food_cat, _norm, _num, _resolve_type,
                      _silent_close, _sing, _to_amount, _types, _unit_rows, _use_counts, _words_in)
from .when import BERLIN, _day_of, parse_when

HEALTH_VERSION = "chat-health-v1"


class Reject(Exception):
    """A request problem. Everything done so far in the transaction is rolled back."""


class ReadBack(Exception):
    """A health value looks odd. Nothing is saved; the user must confirm or correct it first."""


class Entry(BaseModel):
    type: str = Field(description="type name or alias as the user said it: bp, weight, sugar, up, bed, meds, shoonya, any habit")
    value: Optional[float] = Field(None, description="weight, sugar, or systolic")
    value2: Optional[float] = Field(None, description="diastolic")
    when: Optional[str] = Field(None, description="the user's words; omit = now (also for up/bed)")
    period: Optional[Literal["morning", "evening"]] = Field(None, description="bp only; omit unless the user says it")
    session: Optional[int] = Field(None, ge=1, le=9, description="shoonya session 1 or 2")
    note: Optional[str] = None
    confirmed: bool = Field(False, description="true only after the user confirmed an odd value that was read back")


class NewFood(BaseModel):
    """Values per 100 g (per 100 ml when basis is ml)."""
    kcal: float
    protein: float
    fat: float
    carbs: float
    sat_fat: Optional[float] = None
    fibre: Optional[float] = None
    sugar_total: Optional[float] = None
    sugar_added: Optional[float] = None
    sodium_mg: Optional[float] = None
    caffeine_mg: Optional[float] = None
    basis: Literal["g", "ml"] = "g"
    source: Literal["label", "database", "estimate"]
    source_note: Optional[str] = None
    state: Optional[Literal["raw", "cooked", "dry"]] = None


class FoodItem(BaseModel):
    food: str
    amount: float
    unit: Optional[str] = None
    unit_g: Optional[float] = None
    when: Optional[str] = None
    meal: Optional[int] = Field(None, ge=1, le=20)
    note: Optional[str] = None
    estimated: bool = False
    new: Optional[NewFood] = None
    confirmed: bool = False
    drink: Optional[bool] = None


class MealUse(BaseModel):
    name: str
    when: Optional[str] = None
    skip: list[str] = Field(default_factory=list)
    amounts: dict[str, float] = Field(default_factory=dict)


class RecipeItem(BaseModel):
    food: str
    grams: float


class RecipeDef(BaseModel):
    name: str
    kind: Literal["recipe", "usual"] = "recipe"
    items: list[RecipeItem] = Field(default_factory=list)
    from_meal: Optional[str] = None
    finished_weight_g: Optional[float] = None
    drink: Optional[bool] = None


def fmt(dt: Optional[datetime], date_only: bool = False) -> str:
    if dt is None:
        return "-"
    dt = dt.astimezone(BERLIN)
    return dt.strftime("%d.%m.%Y") if date_only else dt.strftime("%d.%m.%Y %H:%M")


_ONCE_PER_DAY = {"weight", "blood_sugar", "meds", "wake"}


_WEIGHT_JUMP_KG = 5


def _dm(d: date) -> str:
    return d.strftime("%d.%m.")


def _range_problems(t: dict, v1: float, v2: Optional[float]) -> list[str]:
    out = []
    unit = t["unit"] or ""
    if t["min1"] is not None and not (float(t["min1"]) <= v1 <= float(t["max1"])):
        what = "systolic " if t["name"] == "bp" else ""
        out.append(f"{what}{_num(v1)} is outside the plausible range {_num(t['min1'])}-{_num(t['max1'])} {unit}".strip())
    if v2 is not None and t["min2"] is not None and not (float(t["min2"]) <= v2 <= float(t["max2"])):
        out.append(f"diastolic {_num(v2)} is outside the plausible range {_num(t['min2'])}-{_num(t['max2'])} {unit}".strip())
    if t["name"] == "bp" and v2 is not None and v1 <= v2:
        out.append("systolic is not higher than diastolic (swapped?)")
    return out


def _first(cur, table: str, type_id: int, day: date, extra: str = "", params: tuple = ()) -> tuple[int, Optional[dict]]:
    cur.execute(f"SELECT * FROM {table} WHERE type_id=%s AND day=%s {extra} ORDER BY event_at", (type_id, day, *params))
    rows = cur.fetchall()
    return len(rows), (rows[0] if rows else None)


def _prepare_entries(cur, entries: list[Entry], now: datetime) -> list[dict]:
    """Validate every entry BEFORE anything is written. Request errors raise Reject; odd values raise ReadBack (nothing saved)."""
    _, idx = _types(cur)
    prepared: list[dict] = []
    soft: list[str] = []
    tally: dict = defaultdict(int)  # entries of this same message count too
    for i, e in enumerate(entries, 1):
        t, sess_text = _resolve_type(idx, e.type)
        if not t:
            close = difflib.get_close_matches(_norm(e.type), list(idx), n=3, cutoff=0.6)
            raise Reject(f"unknown health type '{e.type}'" + (f" (closest: {', '.join(close)})" if close else "")
                         + ". If it is not a health entry, log it as a thought instead")
        if not t["active"]:
            raise Reject(f"type '{t['name']}' is inactive (history only)")
        name = t["name"]
        w = parse_when(e.when, now) if e.when else None
        if e.when and not w:
            raise Reject(f"could not read the time '{e.when}' for {name}")
        ts, date_only = w if w else (now, False)
        day = _day_of(ts, name)
        p = {"t": t, "name": name, "ts": ts, "date_only": date_only, "day": day, "v1": None, "v2": None,
             "period": None, "details": {}, "note": e.note, "notes": [], "confirmed": False}
        problems: list[str] = []
        if not date_only and ts > now + timedelta(hours=1):
            problems.append("the time lies in the future")

        if t["kind"] == "measurement":
            if e.value is None:
                raise Reject(f"{name} needs a value")
            v1, v2 = e.value, e.value2
            if name == "bp" and v2 is None:
                raise Reject("bp needs two values (systolic and diastolic)")
            if name != "bp" and v2 is not None:
                raise Reject(f"{name} takes one value")
            p["v1"], p["v2"] = v1, v2
            desc = f"{name} {_num(v1)}" + (f"/{_num(v2)}" if v2 is not None else "")
            problems += _range_problems(t, v1, v2)
            if name == "weight":
                cur.execute("SELECT value1, day FROM measurements WHERE type_id=%s AND day <= %s ORDER BY day DESC, event_at DESC LIMIT 1",
                            (t["id"], day))
                prev = cur.fetchone()
                if prev and (day - prev["day"]).days <= 60 and abs(float(prev["value1"]) - v1) > _WEIGHT_JUMP_KG:
                    problems.append(f"differs by more than {_WEIGHT_JUMP_KG} kg from the last weight "
                                    f"({_num(prev['value1'])} kg on {_dm(prev['day'])})")
            if name == "bp":
                counts = {}
                for per in ("morning", "evening"):
                    n, _r = _first(cur, "measurements", t["id"], day, "AND period=%s", (per,))
                    counts[per] = n + tally[("bp", day, per)]
                if e.period:
                    per = e.period
                    if counts[per]:
                        problems.append(f"the {per} BP is already logged for {_dm(day)}")
                else:
                    per = "morning" if not counts["morning"] else "evening"
                    if counts["morning"] and counts["evening"]:
                        problems.append(f"morning and evening BP are already logged for {_dm(day)}")
                    else:
                        p["notes"].append(f"bp period set to {per} ({'first' if per == 'morning' else 'second'} BP of {_dm(day)})")
                p["period"] = per
                tally[("bp", day, per)] += 1
            elif name == "blood_sugar":
                p["period"] = "fasted"
            if name in _ONCE_PER_DAY:
                n, r = _first(cur, "measurements", t["id"], day)
                if n + tally[(name, day)]:
                    problems.append(f"already logged for {_dm(day)}" + (f" ({_num(r['value1'])})" if r else ""))
                tally[(name, day)] += 1
        else:
            if e.value is not None or e.value2 is not None:
                raise Reject(f"{name} is a tick, not a number; put extra text in note")
            desc = name
            if name == "shoonya":
                n, _r = _first(cur, "events", t["id"], day)
                n += tally[("shoonya", day)]
                sess = e.session or sess_text or (n + 1)
                if sess > 2:
                    problems.append(f"session {sess}: only sessions 1 and 2 are expected")
                dn, _r = _first(cur, "events", t["id"], day, "AND details->>'session' = %s", (str(sess),))
                if dn + tally[("shoonya", day, sess)]:
                    problems.append(f"session {sess} is already logged for {_dm(day)}")
                tally[("shoonya", day)] += 1
                tally[("shoonya", day, sess)] += 1
                p["details"] = {"session": sess}
                desc = f"shoonya session {sess}"
            else:
                n, r = _first(cur, "events", t["id"], day)
                n += tally[(name, day)]
                if n and name in _ONCE_PER_DAY:
                    problems.append(f"already logged for {_dm(day)}" + (f" at {fmt(r['event_at'])[11:]}" if r and r["time_known"] else ""))
                elif n:
                    p["notes"].append(f"{name} was already logged for {_dm(day)}")
                tally[(name, day)] += 1
            if name in ("wake", "bed") and not date_only:
                cur.execute("""SELECT t.name, e.event_at FROM events e JOIN log_types t ON t.id = e.type_id
                               WHERE t.name IN ('wake','bed') AND e.event_at < %s ORDER BY e.event_at DESC LIMIT 1""", (ts,))
                prev = cur.fetchone()
                if prev and prev["name"] == name:
                    p["notes"].append(f"the previous sleep event is also a {name} ({fmt(prev['event_at'])}); sleep pairing may be off")

        if problems:
            if e.confirmed:
                p["confirmed"] = True
            else:
                soft.append(f"{i}) {desc}: " + "; ".join(problems))
        prepared.append(p)
    if soft:
        raise ReadBack("READ BACK, nothing saved (nothing else in this message was saved either):\n" + "\n".join(soft)
                       + "\nAsk the user to confirm or correct. Then resend the whole call, with the corrected value or "
                         "confirmed=true on the entry the user confirmed.")
    return prepared


def _insert_entries(cur, raw_id: int, prepared: list[dict], out: list[str], notes: list[str]) -> None:
    for p in prepared:
        t = p["t"]
        details = dict(p["details"])
        if p["confirmed"]:
            details["confirmed"] = "user"
        known = not p["date_only"]
        ts = p["ts"] if known else datetime.combine(p["ts"].astimezone(BERLIN).date(), time(12, 0), tzinfo=BERLIN)
        if t["kind"] == "measurement":
            cur.execute(
                """INSERT INTO measurements (raw_id, type_id, day, event_at, time_known, period, value1, value2, note, details,
                                             extractor_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING id""",
                (raw_id, t["id"], p["day"], ts, known, p["period"], Decimal(_num(p["v1"])),
                 None if p["v2"] is None else Decimal(_num(p["v2"])), p["note"], json.dumps(details), HEALTH_VERSION),
            )
            ref = "m%d" % cur.fetchone()["id"]
            val = _num(p["v1"]) + (f"/{_num(p['v2'])}" if p["v2"] is not None else "")
            line = f"+ {ref} {p['name']} {val} {t['unit'] or ''}".rstrip() + (f" ({p['period']})" if p["period"] else "")
        else:
            cur.execute(
                """INSERT INTO events (raw_id, type_id, day, event_at, time_known, note, details, extractor_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING id""",
                (raw_id, t["id"], p["day"], ts, known, p["note"], json.dumps(details), HEALTH_VERSION),
            )
            ref = "e%d" % cur.fetchone()["id"]
            line = f"+ {ref} {p['name']}" + (f" session {p['details']['session']}" if "session" in p["details"] else "")
        line += f" | day {_dm(p['day'])} | " + (fmt(ts) if known else fmt(ts, True) + " (date only)")
        if p["confirmed"]:
            line += " [confirmed by user]"
        out.append(line)
        notes += p["notes"]
        if p["name"] == "wake" and known:
            _move_fallback_food(cur, ts, p["day"], notes)
            cur.execute("SELECT bed_at, hours FROM v_sleep WHERE wake_at = %s LIMIT 1", (ts,))
            s = cur.fetchone()
            if s:
                h = float(s["hours"])
                out.append(f"  slept {_num(h)} h (bed {fmt(s['bed_at'])})" + ("  ! unusual, check the bed time" if h < 2 or h > 16 else ""))
            else:
                notes.append("no bed time found within 24 h before this wake-up, so no sleep duration")


FOOD_VERSION = "chat-food-v1"


_FOOD_BIG_G = 1500


_NUT = ("kcal", "protein", "fat", "sat_fat", "carbs", "fibre", "sugar_total", "sugar_added", "sodium_mg", "caffeine_mg")


_NUT_LABEL = {"kcal": "kcal", "protein": "protein", "fat": "fat", "sat_fat": "sat fat", "carbs": "carbs", "fibre": "fibre",
              "sugar_total": "total sugar", "sugar_added": "added sugar", "sodium_mg": "sodium", "caffeine_mg": "caffeine"}


def _n(x, d: int = 0) -> str:
    if x is None:
        return "?"
    return f"{float(x):,.{d}f}"


def _fdesc(f: dict) -> str:
    return f"N{f['id']} {f['name']}" + (f" [{f['state']}]" if f.get("state") else "")


def _food_line100(f: dict) -> str:
    u = "ml" if f["basis"] == "ml" else "g"
    return (f"per 100 {u}: {_n(f['kcal'])} kcal, P {_n(f['protein'], 1)}, F {_n(f['fat'], 1)} (sat {_n(f['sat_fat'], 1)}), "
            f"C {_n(f['carbs'], 1)} (fibre {_n(f['fibre'], 1)}, sugar {_n(f['sugar_total'], 1)}, added {_n(f['sugar_added'], 1)}), "
            f"sodium {_n(f['sodium_mg'])} mg, caffeine {_n(f['caffeine_mg'])} mg")


def _food_when(cur, text: Optional[str], now: datetime, health: list[dict], no_wake: bool):
    """-> (ts, time_known, day, rule, problems, need_wake). need_wake = the logging day is unclear."""
    w = parse_when(text, now) if text else None
    if text and not w:
        raise Reject(f"could not read the time '{text}'")
    problems: list[str] = []
    if w and w[1]:
        d = w[0].date()
        if d > now.date():
            problems.append("the date lies in the future")
        return datetime.combine(d, time(12, 0), tzinfo=BERLIN), False, d, "asked", problems, False
    ts = w[0] if w else now
    if ts > now + timedelta(hours=1):
        problems.append("the time lies in the future")
    wakes = sorted((p for p in health if p["name"] == "wake" and not p["date_only"]), key=lambda p: p["ts"], reverse=True)
    for p in wakes:
        if p["ts"] <= ts < p["ts"] + timedelta(hours=18):
            return ts, True, p["day"], "asked", problems, False
    cur.execute("SELECT food_day(%s) AS d", (ts,))
    d = cur.fetchone()["d"]
    if d:
        return ts, True, d, "wake", problems, False
    if no_wake:
        return ts, True, (ts - timedelta(hours=5)).date(), "fallback_05", problems, False
    return ts, True, None, None, problems, True


def _new_food_check(name: str, nf: NewFood) -> tuple[list[str], list[str]]:
    """(hard errors, soft problems)."""
    hard: list[str] = []
    soft: list[str] = []
    vals = {k: getattr(nf, k) for k in _NUT}
    for k, v in vals.items():
        if v is not None and v < 0:
            hard.append(f"{_NUT_LABEL[k]} is negative")
    if nf.fat is not None and nf.sat_fat is not None and nf.sat_fat > nf.fat + 0.05:
        hard.append(f"saturated fat {_num(nf.sat_fat)} is higher than fat {_num(nf.fat)}")
    if nf.sugar_total is not None and nf.sugar_total > nf.carbs + 0.05:
        hard.append(f"total sugar {_num(nf.sugar_total)} is higher than carbs {_num(nf.carbs)}")
    if nf.sugar_added is not None and nf.sugar_total is not None and nf.sugar_added > nf.sugar_total + 0.05:
        hard.append(f"added sugar {_num(nf.sugar_added)} is higher than total sugar {_num(nf.sugar_total)}")
    elif nf.sugar_added is not None and nf.sugar_total is None and nf.sugar_added > nf.carbs + 0.05:
        hard.append(f"added sugar {_num(nf.sugar_added)} is higher than carbs {_num(nf.carbs)}")
    if nf.fibre is not None and nf.fibre > nf.carbs + 0.05:
        soft.append(f"fibre {_num(nf.fibre)} is higher than carbs {_num(nf.carbs)}")
    if nf.protein + nf.fat + nf.carbs > 105:
        hard.append("protein + fat + carbs add up to more than 100 per 100")
    if nf.kcal > 900:
        soft.append(f"{_num(nf.kcal)} kcal per 100 is above the maximum of pure fat")
    exp = 4 * nf.protein + 4 * nf.carbs + 9 * nf.fat
    if abs(nf.kcal - exp) > max(30.0, 0.25 * max(nf.kcal, exp)):
        soft.append(f"{_num(nf.kcal)} kcal does not match the macros (about {_num(exp)} kcal)")
    return hard, soft


def _new_food_table(name: str, nf: NewFood) -> str:
    f = {**nf.model_dump(), "name": name}
    return f"- {name} ({nf.source}{', ' + nf.source_note if nf.source_note else ''}) " + _food_line100(f)


def _day_line(cur, day: date) -> str:
    cur.execute("SELECT * FROM v_food_day WHERE day=%s AND is_total", (day,))
    r = cur.fetchone()
    if not r:
        return f"Day {_dm(day)}: no food entries"

    def part(label, val, tgt, unit="", d=0):
        return f"{label} {_n(val, d)}" + (f" / {_n(tgt)}" if tgt is not None else "") + unit

    parts = [part("kcal", r["kcal"], r["target_kcal"]), part("P", r["protein"], r["target_protein"], " g", 1),
             part("F", r["fat"], r["target_fat"], " g", 1), part("C", r["carbs"], r["target_carbs"], " g", 1)]
    if r.get("drink_kcal"):
        parts[0] += f" (drinks {_n(r['drink_kcal'])})"
    unk = [f"{lab} unknown in {r[k + '_unknown']}" for k, lab in (("sat_fat", "sat fat"), ("sugar_total", "total sugar"),
                                                                   ("sugar_added", "added sugar")) if r[k + "_unknown"]]
    if 0 < r["caffeine_unknown"] < r["items"]:
        unk.append(f"caffeine unknown in {r['caffeine_unknown']}")
    return f"Day {_dm(day)} ({r['items']} item{'s' if r['items'] != 1 else ''}): " + " | ".join(parts) + (" | " + "; ".join(unk) if unk else "")


_MEAL_SPEC = re.compile(r"^(.*?)\s+(?:meal\s*)?(\d{1,2})$")


def _meal_spec_items(cur, spec: str, now: datetime) -> list[tuple[int, float]]:
    """Items of a logged meal, from the user's words: '<day> <meal number>', e.g. 'today 1', 'yesterday meal 2', '19.10. 1'.
    A bare number means that meal today. Drinks are not part of a meal. Same food twice is added up."""
    text = (spec or "").strip().lower()
    if not text:
        raise Reject("from_meal needs a day and a meal number, e.g. 'yesterday 1'")
    m = _MEAL_SPEC.match(text)
    if m:
        dtext, num = m[1].strip(), int(m[2])
    elif text.isdigit():
        dtext, num = "today", int(text)
    else:
        raise Reject(f"from_meal '{spec}': say the day and the meal number, e.g. 'yesterday 1' or '19.10. 2'")
    if dtext in ("", "today", "heute"):
        day = _food_today(cur, now)
    else:
        w = parse_when(dtext, now)
        if not w:
            raise Reject(f"from_meal '{spec}': I do not understand the day '{dtext}'")
        day = w[0].date()
    cur.execute("""SELECT food_id, amount FROM food_entries WHERE day=%s AND meal=%s AND NOT is_drink
                   ORDER BY eaten_at, id""", (day, num))
    rows = cur.fetchall()
    if not rows:
        raise Reject(f"from_meal '{spec}': no food in meal {num} on {day.strftime('%d.%m.%Y')} (find food shows the day)")
    merged: dict[int, float] = {}
    for r in rows:
        merged[r["food_id"]] = merged.get(r["food_id"], 0.0) + float(r["amount"])
    return list(merged.items())


def _prepare_food(cur, items: list[FoodItem], uses: list[MealUse], defs: list[RecipeDef], eaten_at: Optional[str],
                  no_wake: bool, code: Optional[str], now: datetime, health: list[dict]) -> dict:
    """Validate everything BEFORE anything is written. Request errors raise Reject; everything the user must decide raises ReadBack."""
    cat = _food_cat(cur)
    soft: list[str] = []
    jobs: list[dict] = []
    newf: dict = {}
    rows: list[dict] = []
    need_wake: list[str] = []
    for i, it in enumerate(items, 1):
        jobs.append({"label": f"{i}) {it.food}", "it": it, "when": it.when or eaten_at, "usual": None})

    if uses:
        cur.execute("SELECT id, name, aliases, is_drink FROM usual_meals ORDER BY id")
        ums = cur.fetchall()
    for mu in uses:
        k = _fkey(mu.name)
        hit = [u for u in ums if _fkey(u["name"]) == k or k in [_fkey(a) for a in u["aliases"] or []]]
        if not hit:
            hit = [u for u in ums if _words_in(mu.name, u["name"])]
        if len(hit) != 1:
            soft.append(f"usual meal '{mu.name}': " + ("no match. Usual meals: " + ", ".join(u["name"] for u in ums) if not hit
                                                       else "several match (" + ", ".join(u["name"] for u in hit) + "), ask which"))
            continue
        um = hit[0]
        cur.execute("""SELECT i.food_id, i.grams, f.name FROM usual_meal_items i JOIN foods f ON f.id=i.food_id
                       WHERE i.meal_id=%s ORDER BY i.sort_order, i.id""", (um["id"],))
        its = [dict(r) for r in cur.fetchall()]
        for term in mu.skip:
            m = [x for x in its if _words_in(term, x["name"])]
            if len(m) != 1:
                soft.append(f"{um['name']}: skip '{term}' " + ("matches nothing" if not m else "is ambiguous")
                            + " (items: " + ", ".join(x["name"] for x in its) + ")")
            else:
                its.remove(m[0])
        for term, grams in mu.amounts.items():
            m = [x for x in its if _words_in(term, x["name"])]
            if len(m) != 1:
                soft.append(f"{um['name']}: amount for '{term}' " + ("matches nothing" if not m else "is ambiguous")
                            + " (items: " + ", ".join(x["name"] for x in its) + ")")
            else:
                m[0]["grams"] = grams
        for x in its:
            jobs.append({"label": f"{um['name']}: {x['name']}", "it": FoodItem(food=f"N{x['food_id']}", amount=float(x["grams"]),
                                                                              meal=None, drink=True if um["is_drink"] else None),
                         "when": mu.when or eaten_at, "usual": um["id"]})

    for job in jobs:
        it: FoodItem = job["it"]
        lab = job["label"]
        if it.amount <= 0:
            raise Reject(f"{lab}: the amount must be above 0")
        matches, how = _find_foods(cat, it.food)
        exact = how in ("id", "name", "alias", "default")
        matched_from = None
        if it.new is not None:
            if exact and matches:
                soft.append(f"{lab}: {_fdesc(matches[0])} already exists ({_food_line100(matches[0])}). "
                            "Resend without 'new' (use the existing food), or fix its values with change.")
                continue
            name = it.food.strip()
            k = _fkey(name)
            if not k or len(name) > 120:
                raise Reject(f"{lab}: the new food needs a name of 1-120 characters")
            if k not in newf:
                hard, sp = _new_food_check(name, it.new)
                if hard:
                    raise Reject(f"{lab}: " + "; ".join(hard))
                if sp and not it.confirmed:
                    soft.append(f"{lab}: " + "; ".join(sp) + ". Check the values with the user; resend with confirmed=true if they are right.")
                    continue
                newf[k] = {"name": name, "nf": it.new, "id": None, "drink": bool(it.drink)}
            nf = it.new
            food = {"id": None, "name": name, "basis": nf.basis, "state": nf.state, "source": nf.source, "needs_review": False,
                    "new_key": k, "is_drink": bool(it.drink), **{c: getattr(nf, c) for c in _NUT}}
        else:
            if exact:
                if not matches:
                    soft.append(f"{lab}: {it.food} does not exist in the catalogue")
                    continue
                if len(matches) > 1:
                    soft.append(f"{lab}: matches several foods: " + "; ".join(_fdesc(m) for m in matches[:6]) + ". Ask which one; resend with its N-id")
                    continue
                food = matches[0]
                if not food["active"]:
                    soft.append(f"{lab}: {_fdesc(food)} is retired (not used any more). Ask which food is meant")
                    continue
            elif matches:
                food = _silent_close(cat, it.food, matches, how)
                if food is not None:
                    matched_from = it.food.strip()
                else:
                    states = any(m.get("state") for m in matches)
                    uses_n = _use_counts(cur, [m["id"] for m in matches[:6]])
                    cands = sorted(matches[:6], key=lambda m: -uses_n.get(m["id"], 0))
                    soft.append(f"{lab}: not an exact catalogue name. Did the user mean: "
                                + "; ".join(_fdesc(m) + (f" (used {uses_n[m['id']]}x)" if uses_n.get(m["id"]) else "") for m in cands)
                                + (" ? (raw or cooked?)" if states else " ?")
                                + " Ask, then resend with the exact name or N-id; after the user picks one, save their word with "
                                  "change N<id> fields {add_aliases: [word]}. If none fits, resend with 'new' (values per 100 g/ml, source).")
                    continue
            else:
                soft.append(f"{lab}: not in the catalogue and nothing similar found. To add it, resend with 'new' "
                            "(values per 100 g/ml and source: label, database or estimate)")
                continue
        amount, portion, est, prob, newunit = _to_amount(cur, food, it.amount, it.unit, it.unit_g)
        if prob:
            soft.append(f"{lab}: {prob}")
            continue
        if amount > 5000:
            raise Reject(f"{lab}: {_num(amount)} {food['basis']} is above the maximum of 5000 per entry")
        if amount > _FOOD_BIG_G and not it.confirmed:
            soft.append(f"{lab}: {_num(amount)} {food['basis']} in one item is a lot. Confirm with the user (resend with confirmed=true)")
            continue
        ts, known, day, rule, tp, need = _food_when(cur, job["when"], now, health, no_wake)
        if tp and not it.confirmed:
            soft.append(f"{lab}: " + "; ".join(tp) + ". Check with the user (confirmed=true if right)")
            continue
        if need:
            need_wake.append(lab)
        rows.append({"food": food, "amount": amount, "portion": portion or None,
                     "est": est or it.estimated, "note": it.note, "meal": it.meal, "ts": ts, "known": known, "day": day, "rule": rule,
                     "usual": job["usual"], "recipe": cat["recipes"].get(food["id"], {}).get("id") if food["id"] else None,
                     "newunit": newunit, "qty_unit_label": lab, "matched_from": matched_from,
                     "drink": it.drink if it.drink is not None else bool(food.get("is_drink"))})

    if need_wake:
        cur.execute("""SELECT w.event_at FROM events w JOIN log_types t ON t.id=w.type_id AND t.name='wake'
                       ORDER BY w.event_at DESC LIMIT 1""")
        lw = cur.fetchone()
        soft.append("logging day unclear: no wake-up is logged in the 18 hours before the food"
                    + (f" (last wake-up {fmt(lw['event_at'])})" if lw else " (none logged at all)")
                    + ". Ask the user when they got up and resend the whole call with an entries item {type: wake, when: ...}; "
                      "if they do not know, resend with no_wake=true (the 05:00 rule is used)")

    recs: list[dict] = []
    for rd in defs:
        name = rd.name.strip()
        k = _fkey(name)
        if not k:
            raise Reject("a recipe or usual meal needs a name")
        if rd.from_meal and rd.items:
            raise Reject(f"'{name}': give either items or from_meal, not both")
        if rd.from_meal and rd.kind != "usual":
            raise Reject(f"'{name}': from_meal works for usual meals only")
        if not rd.items and not rd.from_meal:
            raise Reject(f"'{name}' needs at least one item")
        src_items = ([RecipeItem(food=f"N{fid}", grams=g) for fid, g in _meal_spec_items(cur, rd.from_meal, now)]
                     if rd.from_meal else rd.items)
        if rd.kind == "recipe":
            if not rd.finished_weight_g or rd.finished_weight_g <= 0:
                raise Reject(f"recipe '{name}' needs finished_weight_g (weight after cooking)")
            if k in cat["names"] or k in cat["aliases"]:
                raise Reject(f"a food called '{name}' already exists")
            cur.execute("SELECT 1 FROM recipes WHERE lower(name)=lower(%s)", (name,))
        else:
            cur.execute("SELECT 1 FROM usual_meals WHERE lower(name)=lower(%s)", (name,))
        if cur.fetchone():
            raise Reject(f"'{name}' already exists")
        its = []
        for x in src_items:
            ms, how = _find_foods(cat, x.food)
            if how not in ("id", "name", "alias", "default") or len(ms) != 1 or not ms[0]["active"]:
                soft.append(f"{rd.kind} '{name}': item '{x.food}' " + ("is not an exact catalogue food" if not ms else
                            "is ambiguous or retired") + (": " + "; ".join(_fdesc(m) for m in ms[:5]) if ms else "")
                            + ". Ask, then resend with the exact name or N-id")
                continue
            if x.grams <= 0:
                raise Reject(f"'{name}': grams must be above 0 for {x.food}")
            if rd.kind == "recipe" and ms[0]["id"] in cat["recipes"]:
                raise Reject(f"recipe '{name}': a recipe cannot contain another recipe ({ms[0]['name']})")
            its.append((ms[0]["id"], x.grams))
        recs.append({"def": rd, "name": name, "items": its})

    label_new = {k: v for k, v in newf.items() if v["nf"].source == "label"}
    payload = json.dumps([[v["name"], v["nf"].model_dump()] for k, v in sorted(label_new.items())], sort_keys=True, default=str)
    new_code = hashlib.sha256(payload.encode()).hexdigest()[:6].upper()
    table = None
    if label_new and (code or "").strip().upper() != new_code:
        table = ("New food(s) from a LABEL. Show this table to the user, ask them to check it against the pack, and if it is right "
                 f"resend the same call with food_code=\"{new_code}\" (a correction gives a new code):\n"
                 + "\n".join(_new_food_table(v["name"], v["nf"]) for v in label_new.values()))
    if soft or table:
        msg = "READ BACK, nothing saved (nothing else in this message was saved either):\n" + "\n".join(f"- {s}" for s in soft)
        if table:
            msg += ("\n" if soft else "") + table
        raise ReadBack(msg)
    return {"rows": rows, "new": newf, "recipes": recs}


def _create_recipe(cur, rec: dict, out: list[str]) -> None:
    rd: RecipeDef = rec["def"]
    name = rec["name"]
    if rd.kind == "recipe":
        cur.execute("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, source_note, created_by)
                       VALUES (%s,'g',0,0,0,0,'calculated','recipe, calculated from its items','claude') RETURNING id""", (name,))
        fid = cur.fetchone()["id"]
        cur.execute("""INSERT INTO recipes (name, food_id, finished_weight_g, weight_measured, note)
                      VALUES (%s,%s,%s,%s,%s) RETURNING id""", (name, fid, Decimal(_num(rd.finished_weight_g)), True, None))
        rid = cur.fetchone()["id"]
        for n, (food_id, grams) in enumerate(rec["items"], 1):
            cur.execute("INSERT INTO recipe_items (recipe_id, food_id, grams, sort_order) VALUES (%s,%s,%s,%s)",
                        (rid, food_id, Decimal(_num(grams)), n * 10))
        cur.execute("SELECT * FROM foods WHERE id=%s", (fid,))
        f = cur.fetchone()
        out.append(f"+ C{rid} recipe '{name}' = N{fid}, {len(rec['items'])} items, finished {_num(rd.finished_weight_g)} g"
                   + f"; {_food_line100(f)}")
    else:
        cur.execute("INSERT INTO usual_meals (name, aliases, note, is_drink) VALUES (%s,%s,%s,%s) RETURNING id", (name, [], None, bool(rd.drink)))
        uid = cur.fetchone()["id"]
        for n, (food_id, grams) in enumerate(rec["items"], 1):
            cur.execute("INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order) VALUES (%s,%s,%s,%s)",
                        (uid, food_id, Decimal(_num(grams)), n * 10))
        out.append(f"+ U{uid} usual meal '{name}'" + (" (drink)" if rd.drink else "") + f", {len(rec['items'])} items")


def _insert_food(cur, raw_id: int, prep: dict, out: list[str], notes: list[str]) -> None:
    cur.execute("SELECT set_config('app.raw_id', %s, true)", (str(raw_id),))
    for k, v in prep["new"].items():
        nf: NewFood = v["nf"]
        review = nf.source == "estimate"
        cur.execute(
            """INSERT INTO foods (name, aliases, basis, kcal, protein, fat, sat_fat, carbs, fibre, sugar_total, sugar_added, sodium_mg,
                                  caffeine_mg, carbs_include_fibre, source, source_note, source_date, state, needs_review, review_reason,
                                  created_by, is_drink)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'claude',%s) RETURNING id""",
            (v["name"], [], nf.basis, nf.kcal, nf.protein, nf.fat, nf.sat_fat, nf.carbs, nf.fibre, nf.sugar_total, nf.sugar_added,
             nf.sodium_mg, nf.caffeine_mg, None, nf.source, nf.source_note, date.today(), nf.state, review,
             "estimate added from chat; check when convenient" if review else None, bool(v.get("drink"))))
        v["id"] = cur.fetchone()["id"]
        unknown = [_NUT_LABEL[c] for c in _NUT if getattr(nf, c) is None]
        out.append(f"+ N{v['id']} NEW food '{v['name']}' ({nf.source}" + (", flagged for review" if review else "") + "): "
                   + _food_line100({**nf.model_dump(), "name": v["name"]}))
        if unknown:
            notes.append(f"N{v['id']} {v['name']}: unknown values {', '.join(unknown)} (totals will say so)")
    made_units: set = set()
    for p in prep["rows"]:
        f = p["food"]
        fid = f["id"] if f["id"] is not None else prep["new"][f["new_key"]]["id"]
        p["food_id"] = fid
        nu = p["newunit"]
        if nu and (fid, nu["unit"]) not in made_units:
            made_units.add((fid, nu["unit"]))
            cur.execute("""INSERT INTO food_units (food_id, unit, grams, source) VALUES (%s,%s,%s,'estimate')
                           ON CONFLICT (food_id, unit) DO NOTHING""", (fid, nu["unit"], Decimal(_num(nu["grams"]))))
            notes.append(f"stored 1 {nu['unit']} of {f['name']} = {_num(nu['grams'])} {f['basis']} (estimate)")
    for rec in prep["recipes"]:
        _create_recipe(cur, rec, out)

    ids: list[int] = []
    for p in prep["rows"]:
        cur.execute(
            """INSERT INTO food_entries (raw_id, food_id, amount, portion_text, estimated, note, eaten_at, time_known, day, day_rule,
                                         meal, usual_meal_id, recipe_id, extractor_version, is_drink)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s) RETURNING id""",
            (raw_id, p["food_id"], Decimal(_num(p["amount"])), p["portion"], p["est"], p["note"], p["ts"], p["known"], p["day"],
             p["rule"], p["usual"], p["recipe"], FOOD_VERSION, p["drink"]))
        p["id"] = cur.fetchone()["id"]
        ids.append(p["id"])
    if not ids:
        return
    days = sorted({p["day"] for p in prep["rows"]})
    for d in days:
        cur.execute("SELECT assign_meals(%s)", (d,))
        untimed = [p for p in prep["rows"] if p["day"] == d and not p["known"] and not p["drink"]]
        nxt = None
        for p in untimed:
            if p["meal"]:
                m = p["meal"]
            else:
                if nxt is None:
                    cur.execute("SELECT coalesce(max(meal),0) AS m FROM food_entries WHERE day=%s AND NOT (id = ANY(%s))", (d, ids))
                    nxt = cur.fetchone()["m"] + 1
                m = nxt
            cur.execute("UPDATE food_entries SET meal=%s WHERE id=%s", (m, p["id"]))
            p["meal_set"] = m
    cur.execute("SELECT e.id, e.meal, e.kcal, e.protein, e.sat_fat, e.sugar_total, e.sugar_added FROM food_entries e WHERE e.id = ANY(%s)", (ids,))
    got = {r["id"]: r for r in cur.fetchall()}
    tot_k = tot_p = 0.0
    guessed: list[str] = []
    for p in prep["rows"]:
        r = got[p["id"]]
        f = p["food"]
        unit = "ml" if f["basis"] == "ml" else "g"
        tot_k += float(r["kcal"])
        tot_p += float(r["protein"])
        line = (f"+ f{p['id']} {f['name']} {_num(p['amount'])} {unit}" + (f" ({p['portion']})" if p["portion"] else "")
                + f" | {_n(r['kcal'])} kcal, P {_n(r['protein'], 1)} | {_dm(p['day'])} " + ("drink" if p["drink"] else f"meal {r['meal']}")
                + (f" {p['ts'].astimezone(BERLIN).strftime('%H:%M')}" if p["known"] else " (no time)"))
        if p["meal"] and p["known"] and not p["drink"]:
            notes.append(f"meal {p['meal']} ignored for {f['name']}: items with a time are numbered by the 2-hour rule")
        if p["est"]:
            line += " [estimated]"
        if p.get("matched_from"):
            line += f" [matched from '{p['matched_from']}']"
            guessed.append(f"'{p['matched_from']}' -> f{p['id']} N{f['id']} {f['name']}")
        if p["rule"] == "fallback_05":
            line += " [day by 05:00 rule]"
        out.append(line)
        if f.get("needs_review") and f["id"] is not None:
            notes.append(f"{f['name']} is flagged for review" + (f": {f['review_reason']}" if f.get("review_reason") else ""))
    out.append(f"= {len(ids)} item{'s' if len(ids) != 1 else ''}: {_n(tot_k)} kcal, protein {_n(tot_p)} g")
    for d in days:
        out.append(_day_line(cur, d))
    if guessed:
        notes.append("not exact names, used without asking (the only close match): " + "; ".join(guessed)
                     + ". Tell the user in a few words. If one is wrong: change f<id> food=<right food>")
    rules = {p["rule"] for p in prep["rows"]}
    if "asked" in rules:
        notes.append("logging day taken from the wake-up logged in this message or from the date given")


def _move_fallback_food(cur, wake_ts: datetime, wake_day: date, notes: list[str]) -> None:
    """A wake-up logged afterwards moves food that was logged with the 05:00 fallback to the right day."""
    cur.execute("""SELECT id, day FROM food_entries WHERE day_rule='fallback_05' AND eaten_at >= %s
                   AND eaten_at < %s + interval '18 hours' FOR UPDATE""", (wake_ts, wake_ts))
    rows = cur.fetchall()
    if not rows:
        return
    cur.execute("UPDATE food_entries SET day_rule='wake', day=%s WHERE id = ANY(%s)", (wake_day, [r["id"] for r in rows]))
    for d in sorted({r["day"] for r in rows} | {wake_day}):
        cur.execute("SELECT assign_meals(%s)", (d,))
    moved = [r for r in rows if r["day"] != wake_day]
    if moved:
        notes.append(f"{len(moved)} food entr{'y' if len(moved) == 1 else 'ies'} logged earlier with the 05:00 rule moved to "
                     f"{_dm(wake_day)} (wake-up {fmt(wake_ts)[11:]})")


def _food_today(cur, now: datetime) -> date:
    cur.execute("SELECT food_day(%s) AS d", (now,))
    d = cur.fetchone()["d"]
    return d or (now - timedelta(hours=5)).date()
