"""Build the test set from a restored v1 database: every chat message with the entries v1 saved from it.

Run against a throwaway copy of v1 (never the live database):
    GOLDEN_DSN=postgresql://pgadmin:...@127.0.0.1:55434/appdata .venv/bin/python tools/golden/build.py
Writes tests/golden/cases.jsonl and tests/golden/catalogue.json. Both hold personal data and stay out of git.
"""
import json
import os
import pathlib
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

TZ = ZoneInfo("Europe/Berlin")
OUT = pathlib.Path(__file__).resolve().parents[2] / "tests" / "golden"

# Messages v1 stored without saving entries, with what they really were. 206 failed and was resent, so it is skipped.
NO_ENTRIES = {98: "question", 149: "command", 265: "command", 283: "command", 206: "skip"}

# Owner decisions of 25.10.2026 that change what the right answer is (see tools/import/v1_fixes.sql).
NOT_DRINKS = ("Protein powder, whey (Aldi Sports Whey Deluxe)",)  # whey in a bowl is food
EXTRA_DEFAULTS = {"tea": 230, "potato chips": 235}
UNIT_DEFAULTS = {"glass": 300, "cup": 250}


def local(ts) -> str:
    return ts.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def todo_items(actions: list[str]) -> list[str]:
    items = []
    for a in actions:
        m = re.search(r"\(([^()]*\|[^()]*)\)", a)            # "(Milk | Lait | Milch)"
        if m:
            items.append(m.group(1).split("|")[0].strip())
            continue
        m = re.match(r"\s*(?:unchecked|created|added)\s*:?\s*([^(]+?)\s*(?:\(|$)", a, re.I)
        if m:
            items += [x.split("|")[0].strip() for x in re.split(r",|\band\b", m.group(1)) if x.strip()]
            continue
        m = re.search(r"already on (?:the )?list[^:]*:\s*([^|(]+?)\s*[|(]", a, re.I)  # "already on list: Cola | ..."
        if m:
            items.append(m.group(1).strip())
    return items


def main() -> None:
    dsn = os.environ["GOLDEN_DSN"]
    OUT.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        raws = q("SELECT id, received_at, content, metadata FROM raw_inbox WHERE channel = 'claude-chat' ORDER BY received_at, id")
        privs = q("SELECT id, received_at, content, label FROM private.entries WHERE channel = 'claude-chat' ORDER BY received_at")
        foods = q("SELECT f.id, f.name, f.basis, f.is_drink, f.state FROM foods f")
        cases, unparsed = [], []

        def context(ts):
            prev = [(local(r["received_at"]), r["content"]) for r in raws
                    if ts - timedelta(hours=6) <= r["received_at"] < ts][-4:]
            rows = q("""SELECT e.day, e.meal, e.is_drink, e.eaten_at, f.name FROM food_entries e JOIN foods f ON f.id = e.food_id
                        WHERE e.time_known AND e.logged_at < %s AND e.eaten_at <= %s AND e.eaten_at > %s - interval '18 hours'
                        ORDER BY e.eaten_at, e.id""", ts, ts, ts)
            meals: dict = {}
            for m in rows:  # today's meals and drinks as the core would show them: (label, time of first item, names)
                key = ("drink", m["eaten_at"]) if m["is_drink"] else ("meal", m["day"], m["meal"])
                label = "drink" if m["is_drink"] else f"meal {m['meal']}"
                meals.setdefault(key, [label, local(m["eaten_at"]), []])[2].append(m["name"])
            return prev, list(meals.values())

        for r in raws:
            rid, ts = r["id"], r["received_at"]
            exp = {"intent": "log", "private": None, "food": [], "health": [], "thoughts": [], "todos": [], "updates": 0}
            # new = the food was created by this very message (v1 added it from a database or an estimate)
            exp["food"] = [dict(food_id=e["food_id"], name=e["name"], amount=float(e["amount"]), drink=e["is_drink"] and e["name"] not in NOT_DRINKS,
                                at=local(e["eaten_at"]), time_known=e["time_known"], note=e["note"], new=e["new"],
                                day=str(e["day"]), meal=e["meal"])
                           for e in q("""SELECT e.*, f.name, f.created_at BETWEEN %s - interval '2 minutes' AND %s + interval '30 minutes' AS new
                                         FROM food_entries e JOIN foods f ON f.id = e.food_id
                                         WHERE e.raw_id = %s ORDER BY e.id""", ts, ts, rid)]
            names = " ".join(f["name"].lower() for f in exp["food"])
            if "milk" in names and "cocoa powder" in names:  # decided 25.10.: milk with cocoa powder is a hot chocolate
                for f in exp["food"]:
                    if f["name"].lower().startswith(("milk", "cocoa powder")):
                        f["drink"] = True
            exp["health"] = [dict(type=m["type"], value=float(m["value1"]), value2=None if m["value2"] is None else float(m["value2"]),
                                  period=m["period"], at=local(m["event_at"]), time_known=m["time_known"], day=str(m["day"]))
                             for m in q("""SELECT m.*, t.name AS type FROM measurements m JOIN log_types t ON t.id = m.type_id
                                           WHERE m.raw_id = %s ORDER BY m.id""", rid)]
            exp["health"] += [dict(type=e["type"], value=None, value2=None, period=None, at=local(e["event_at"]),
                                   time_known=e["time_known"], session=(e["details"] or {}).get("session"), day=str(e["day"]))
                              for e in q("""SELECT e.*, t.name AS type FROM events e JOIN log_types t ON t.id = e.type_id
                                            WHERE e.raw_id = %s ORDER BY e.id""", rid)]
            exp["thoughts"] = [dict(thought_type=t["thought_type"], area=t["area"], summary=t["summary"])
                               for t in q("SELECT * FROM thoughts WHERE raw_id = %s ORDER BY id", rid)]
            exp["updates"] = len(q("SELECT 1 FROM thought_updates WHERE raw_id = %s", rid))
            if "todoist" in (r["metadata"] or {}):
                exp["todos"] = todo_items(r["metadata"]["todoist"])
                if not exp["todos"]:
                    unparsed.append((rid, r["metadata"]["todoist"]))
            empty = not any(exp[k] for k in ("food", "health", "thoughts", "todos", "updates"))
            if empty:
                exp["intent"] = NO_ENTRIES.get(rid, "unknown")
            prev, meals = context(ts)
            cases.append(dict(id=f"r{rid}", now=local(ts), text=r["content"], previous=prev, meals=meals,
                              expected=exp, skip=exp["intent"] in ("skip", "unknown")))

        for p in privs:
            prev, meals = context(p["received_at"])
            exp = {"intent": "log", "private": p["label"], "food": [], "health": [], "thoughts": [{}], "todos": [], "updates": 0}
            cases.append(dict(id=f"p{p['id']}", now=local(p["received_at"]), text=p["content"], previous=prev,
                              meals=meals, expected=exp, skip=False))

        cat = {
            "foods": [dict(id=f["id"], name=f["name"], basis=f["basis"], drink=f["is_drink"], state=f["state"]) for f in foods],
            "aliases": [dict(food_id=a["id"], alias=a["alias"]) for a in q("SELECT id, unnest(aliases) AS alias FROM foods")],
            "defaults": {d["term"]: d["food_id"] for d in q("SELECT term, food_id FROM food_defaults")} | EXTRA_DEFAULTS,
            "units": [dict(food_id=u["food_id"], unit=u["unit"], grams=float(u["grams"])) for u in q("SELECT * FROM food_units")],
            "unit_defaults": UNIT_DEFAULTS,
            "types": [dict(name=t["name"], aliases=t["aliases"]) for t in q("SELECT name, aliases FROM log_types")],
        }

    (OUT / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    (OUT / "catalogue.json").write_text(json.dumps(cat, ensure_ascii=False, indent=1))
    kinds = {}
    for c in cases:
        k = "skip" if c["skip"] else c["expected"]["intent"]
        kinds[k] = kinds.get(k, 0) + 1
    print(f"{len(cases)} cases {kinds}; catalogue: {len(cat['foods'])} foods, {len(cat['units'])} units")
    print("todo items found:", sum(len(c["expected"]["todos"]) for c in cases), "| todoist actions not understood:", unparsed)


if __name__ == "__main__":
    main()
