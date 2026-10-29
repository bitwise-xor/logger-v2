"""What the Food page's buttons and forms do. Changes to food entries, foods and usual meals go through the logger's own
change function (commands._run_changes, v1's checks): the first Save shows v1's preview of the change with a Yes
button, and the Yes makes it (decided 26.10.2026). Notes, words, barcodes and "Looks right" are saved with Save, like
the Health card. Copying a meal is logged like a typed message with a ready parser answer (no AI call); a new usual
meal is made by v1's own code for "save as usual meal" (v1_log._prepare_food and _create_recipe)."""
import re
from datetime import datetime, timedelta
from typing import Optional

from . import commands, foodpage, logbook, v1_log
from .db import connect
from .todaypage import _plain
from .v1_log import ReadBack, RecipeDef, RecipeItem, Reject
from .webactions import TIME, _change, _num, _pred, _rejected
from .when import BERLIN

BARCODE = re.compile(r"\d{8,14}")
MAX_ROWS = 40


def _again(form) -> dict:
    """The form's fields, to send again with the user's Yes."""
    return {k: str(v) for k, v in form.multi_items() if k not in ("csrf", "confirmed")}


def _ask_first(steps: list[dict], what: str, ok: str, form, action: str, label: str = "Yes, change", shown: Optional[str] = None) -> dict:
    """The first Save shows the change (v1's own preview, made in a savepoint and rolled back); the Yes makes it."""
    if form.get("confirmed") == "1":
        return _change(steps, what, ok)
    with connect() as conn, conn.cursor() as cur:
        try:
            lines = commands._preview(cur, steps, datetime.now(BERLIN))
        except Reject as e:
            return _rejected(str(e))
    text = shown or ("\n".join(lines) if isinstance(lines, list) else str(lines))
    return {"outcome": "needs_confirmation", "read_back": text,
            "confirm": {"action": action, "label": label, "no": foodpage.safe_back(form.get("back")),
                        "fields": _again(form) | {"confirmed": "1"}}}


def _hm(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h):02d}:{m}"


# ---- the food log ----------------------------------------------------------------------------------

def entry(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    if not re.fullmatch(r"f\d{1,9}", ref):
        return _rejected("Unknown entry.")
    with connect() as c:
        e = c.execute("""SELECT e.food_id, e.amount, e.eaten_at, e.time_known, f.basis, f.name FROM food_entries e
                         JOIN foods f ON f.id = e.food_id WHERE e.id = %s""", (int(ref[1:]),)).fetchone()
    if not e:
        return _rejected("That entry is not there any more.")
    if form.get("act") == "delete":
        return _ask_first([{"action": "change", "args": {"id": ref, "delete": True}}], "food delete", f"Deleted {e['name']}.",
                          form, "/web/food/entry", "Yes, delete")
    fields: dict = {}
    food = str(form.get("food", ""))
    if food.isdigit() and int(food) != e["food_id"]:
        fields["food"] = f"N{int(food)}"
    a = _num(form.get("amount", ""))
    if a is None or not 0 < a <= 5000:
        return _rejected(f"Give the amount as a number above 0 and at most 5000 {e['basis']}.")
    if abs(a - float(e["amount"])) > 1e-9:
        fields.update(amount=a, unit=e["basis"])
    t = str(form.get("time", "")).strip()
    if t:
        if not TIME.match(t):
            return _rejected("Give the time as hh:mm.")
        old = e["eaten_at"].astimezone(BERLIN)
        if not e["time_known"] or old.strftime("%H:%M") != _hm(t):
            fields["when"] = f"{old:%d.%m.%Y} {_hm(t)}"
    if not fields:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    return _ask_first([{"action": "change", "args": {"id": ref, "fields": fields}}], "food entry", f"Changed {e['name']}.",
                      form, "/web/food/entry")


def note(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    if not re.fullmatch(r"f\d{1,9}", ref):
        return _rejected("Unknown entry.")
    text = " ".join(str(form.get("note", "")).split())[:300]
    return _change([{"action": "change", "args": {"id": ref, "fields": {"note": text or None}}}], "food note",
                   "Note saved." if text else "Note removed.")


def copy(form, key: Optional[str]) -> dict:
    """A logged meal (or the day's drinks) logged again today, with the same foods and amounts."""
    meal = str(form.get("meal", ""))
    t = str(form.get("time", "")).strip()
    if not meal.isdigit() or (t and not TIME.match(t)):
        return _rejected("Unknown meal, or the time is not hh:mm.")
    with connect() as conn, conn.cursor() as cur:
        today = foodpage._today(cur)
        day = foodpage.parse_day(form.get("day"), None)
        if not day or not today - timedelta(days=366) <= day <= today:
            return _rejected("Unknown day.")
        cur.execute("""SELECT e.food_id, e.amount, e.is_drink, f.name, f.basis FROM food_entries e JOIN foods f ON f.id = e.food_id
                       WHERE e.day = %s AND e.meal = %s ORDER BY e.eaten_at, e.id""", (day, int(meal)))
        rows = cur.fetchall()
    if not rows:
        return _rejected("That meal is not there any more.")
    when = _hm(t) if t else None
    items = [{"food": f"N{r['food_id']}", "food_en": r["name"], "amount": float(r["amount"]), "unit": r["basis"], "grams_per_unit": None,
              "cooked": False, "drink": r["is_drink"], "when": when, "note": None} for r in rows]
    what = "my drinks" if meal == "0" else f"meal {meal}"
    text = (f"{what} of {day:%d.%m.} again: " + ", ".join(f"{r['name']} {_plain(r['amount'])} {r['basis']}" for r in rows)
            + (f" at {when}" if when else ""))
    return logbook.log(text, key, _pred(food=items), channel=logbook.WEB)


def usual_new(form, key: Optional[str]) -> dict:
    """A logged meal saved as a usual meal, by v1's own code for "save as usual meal"."""
    name = " ".join(str(form.get("name", "")).split())[:60]
    meal = str(form.get("meal", ""))
    if len(name) < 2 or not meal.isdigit() or meal == "0":
        return _rejected("Give the usual meal a name.")
    now = datetime.now(BERLIN)
    with connect() as conn, conn.cursor() as cur:
        today = foodpage._today(cur)
        day = foodpage.parse_day(form.get("day"), None)
        if not day or not today - timedelta(days=366) <= day <= today:
            return _rejected("Unknown day.")
        try:
            prep = v1_log._prepare_food(cur, [], [], [RecipeDef(name=name, kind="usual", from_meal=f"{day:%d.%m.%Y} {meal}")],
                                        None, True, None, now, [])
            out: list[str] = []
            for rec in prep["recipes"]:
                v1_log._create_recipe(cur, rec, out)
        except (Reject, ReadBack) as e:
            conn.rollback()
            msg = str(e).replace("READ BACK, nothing saved (nothing else in this message was saved either):", "Not saved:").strip()
            return _rejected(msg)
        logbook._audit(cur, "web_change", "changed", {"what": "usual new"}, logbook.WEB)
    n = len(prep["recipes"][0]["items"]) if prep["recipes"] else 0
    return {"outcome": "changed", "head": f'Saved "{name}" as a usual meal ({n} items). It shows on Today and in the Catalogue.'}


def food_id(cur, text: str) -> Optional[int]:
    """The active food the user typed or picked from the list: its exact name or one of its words (not a guess)."""
    t = " ".join(str(text).split())
    if not t:
        return None
    m = re.fullmatch(r"[nN]#?(\d{1,9})", t)
    cur.execute("SELECT id FROM foods WHERE active AND (lower(name) = lower(%s) OR %s = ANY(SELECT lower(a) FROM unnest(aliases) a)"
                + (" OR id = %s" if m else "") + ")", (t, t.lower()) + ((int(m[1]),) if m else ()))
    rows = cur.fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


NEW_ROWS = 8


def usual_create(form, key: Optional[str]) -> dict:
    """A new usual meal or usual drink made from foods typed or picked by name and grams (no logged meal needed)."""
    name = " ".join(str(form.get("name", "")).split())[:60]
    if len(name) < 2:
        return _rejected("Give the usual meal a name.")
    drink = form.get("kind") == "drink"
    items, now = [], datetime.now(BERLIN)
    with connect() as conn, conn.cursor() as cur:
        for k in range(NEW_ROWS):
            typed, g_text = str(form.get(f"food_{k}", "")).strip(), str(form.get(f"g_{k}", "")).strip()
            if not typed and not g_text:
                continue
            fid = food_id(cur, typed)
            if fid is None:
                return _rejected(f"Row {k + 1}: pick a food from the list ({typed or 'empty'} is not exactly one food of the catalogue).")
            g = _num(g_text)
            if g is None or not 0 < g <= 5000:
                return _rejected(f"Row {k + 1}: give an amount above 0 and at most 5000.")
            items.append(RecipeItem(food=f"N{fid}", grams=g))
        if not items:
            return _rejected("Add at least one food with its amount.")
        try:
            prep = v1_log._prepare_food(cur, [], [], [RecipeDef(name=name, kind="usual", items=items, drink=drink)], None, True, None, now, [])
            out: list[str] = []
            for rec in prep["recipes"]:
                v1_log._create_recipe(cur, rec, out)
        except (Reject, ReadBack) as e:
            conn.rollback()
            return _rejected(str(e).replace("READ BACK, nothing saved (nothing else in this message was saved either):", "Not saved:").strip())
        logbook._audit(cur, "web_change", "changed", {"what": "usual new from foods", "drink": drink}, logbook.WEB)
    return {"outcome": "changed", "head": f'Saved "{name}" as a usual {"drink" if drink else "meal"} ({len(items)} item{"" if len(items) == 1 else "s"}). '
                                          'It shows on Today and in the list below.'}


# ---- the catalogue ---------------------------------------------------------------------------------

def _food(ref: str):
    if not re.fullmatch(r"N\d{1,9}", ref):
        return None
    with connect() as c:
        return c.execute("SELECT * FROM foods WHERE id = %s", (int(ref[1:]),)).fetchone()


def values(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    f = _food(ref)
    if not f:
        return _rejected("Unknown food.")
    changed: dict = {}
    for k, label in foodpage.NUT:
        s = form.get(k)
        if s is None:
            continue
        s = str(s).strip()
        if s == "":
            if f[k] is not None:
                return _rejected(f"{label}: give a number (an unknown value cannot be set here).")
            continue
        v = _num(s)
        if v is None or v < 0:
            return _rejected(f"{label}: give a number of 0 or more.")
        if f[k] is None or abs(v - float(f[k])) > 1e-9:
            changed[k] = v
    if not changed:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    past = "yes" if form.get("past") == "yes" else "no"
    return _ask_first([{"action": "change", "args": {"id": ref, "fields": changed, "past": past}}], "food values",
                      f"Changed {ref} {f['name']}.", form, "/web/food/values")


def words(form, key: Optional[str]) -> dict:
    ref, act = str(form.get("id", "")), form.get("act")
    f = _food(ref)
    w = " ".join(str(form.get("word", "")).split()).lower()[:60]
    if not f or act not in ("add", "remove"):
        return _rejected("Unknown food.")
    if not any(ch.isalpha() for ch in w):
        return _rejected("Type a word first.")
    field = "add_aliases" if act == "add" else "remove_aliases"
    ok = f'"{w}" now means {ref} {f["name"]}.' if act == "add" else f'"{w}" no longer means {ref} {f["name"]}.'
    return _change([{"action": "change", "args": {"id": ref, "fields": {field: [w]}}}], f"food words {act}", ok)


def barcode(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    f = _food(ref)
    b = re.sub(r"\s", "", str(form.get("barcode", "")))
    if not f:
        return _rejected("Unknown food.")
    if not BARCODE.fullmatch(b):
        return _rejected("A barcode is 8 to 14 digits.")
    if b == (f["barcode"] or ""):
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    return _change([{"action": "change", "args": {"id": ref, "fields": {"barcode": b}}}], "food barcode", f"Barcode {b} saved for {ref} {f['name']}.")


def reviewed(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    f = _food(ref)
    if not f:
        return _rejected("Unknown food.")
    return _change([{"action": "change", "args": {"id": ref, "fields": {"needs_review": False}}}], "food reviewed",
                   f"Kept. {ref} {f['name']} is no longer flagged.")


def lookup(form, key: Optional[str]) -> dict:
    name = " ".join(str(form.get("name", "")).split())[:120]
    if not name:
        return _rejected("Type the food's name or barcode first.")
    b = re.sub(r"\s", "", name)
    return logbook.food_lookup(barcode=b) if BARCODE.fullmatch(b) else logbook.food_lookup(name=name)


def usual_meal(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    if not re.fullmatch(r"U\d{1,9}", ref):
        return _rejected("Unknown usual meal.")
    with connect() as c:
        u = c.execute("SELECT id, name FROM usual_meals WHERE id = %s", (int(ref[1:]),)).fetchone()
        old = [(r["food_id"], float(r["grams"])) for r in c.execute(
            "SELECT food_id, grams FROM usual_meal_items WHERE meal_id = %s ORDER BY sort_order, id", (int(ref[1:]),)).fetchall()] if u else []
    if not u:
        return _rejected("That usual meal is not there any more.")
    if form.get("act") == "delete":
        return _ask_first([{"action": "change", "args": {"id": ref, "delete": True}}], "usual delete",
                          f'Deleted the usual meal "{u["name"]}". Past meals keep their values.', form, "/web/food/usualmeal", "Yes, delete")
    rows = form.get("rows", "")
    if not str(rows).isdigit() or int(rows) > MAX_ROWS:
        return _rejected("Unknown usual meal.")
    items: list[tuple[int, float]] = []
    for k in range(int(rows)):
        if form.get(f"rm_{k}"):
            continue
        fid, g = str(form.get(f"food_{k}", "")), _num(form.get(f"g_{k}", ""))
        if not fid.isdigit() or g is None or not 0 < g <= 5000:
            return _rejected("Give every food an amount above 0 and at most 5000.")
        items.append((int(fid), g))
    add_f, add_g = str(form.get("add_food", "")), str(form.get("add_g", "")).strip()
    if str(form.get("add_name", "")).strip():  # a food typed or picked by name (the page's list), not from a long drop-down
        with connect() as c, c.cursor() as cur:
            picked = food_id(cur, form.get("add_name"))
        if picked is None:
            return _rejected(f"Pick the added food from the list ({str(form.get('add_name')).strip()} is not exactly one food of the catalogue).")
        add_f = str(picked)
    if add_f:
        g = _num(add_g)
        if not add_f.isdigit() or g is None or not 0 < g <= 5000:
            return _rejected("Give the added food an amount above 0 and at most 5000.")
        items.append((int(add_f), g))
    if not items:
        return _rejected("A usual meal needs at least one food. To remove it, use Delete.")
    name = " ".join(str(form.get("name", "")).split())[:60]
    if len(name) < 2:
        return _rejected("Give the usual meal a name.")
    fields: dict = {}
    if name != u["name"]:
        fields["name"] = name
    if items != old:
        fields["items"] = [{"food": f"N{fid}", "grams": g} for fid, g in items]
    if not fields:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    with connect() as c:
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM foods WHERE id = ANY(%s)", ([f for f, _ in old + items],)).fetchall()}
    lines = [f"name: {u['name']} → {name}"] if "name" in fields else []
    before, after = dict(old), dict(items)
    for fid, g in old:
        if fid not in after:
            lines.append(f"{names.get(fid, f'N{fid}')} removed")
        elif after[fid] != g:
            lines.append(f"{names.get(fid, f'N{fid}')}: {format(g, 'g')} → {format(after[fid], 'g')}")
    lines += [f"{names.get(fid, f'N{fid}')} {format(g, 'g')} added" for fid, g in items if fid not in before]
    if "items" in fields and not lines:
        lines.append("new order of the foods")
    return _ask_first([{"action": "change", "args": {"id": ref, "fields": fields}}], "usual meal",
                      f'Changed the usual meal "{name}".', form, "/web/food/usualmeal",
                      shown=f'Change the usual meal "{u["name"]}":\n' + "\n".join(f"- {x}" for x in lines))


# Every form of the Food page: path under /web/food/, the function, and where the browser goes afterwards
# (None = the page the form came from).
ACTIONS = {"entry": (entry, None), "note": (note, None), "copy": (copy, "/web/food"), "usualnew": (usual_new, None), "usualcreate": (usual_create, None),
           "values": (values, None), "words": (words, None), "barcode": (barcode, None), "reviewed": (reviewed, None),
           "lookup": (lookup, "/web/questions"), "usualmeal": (usual_meal, None)}
