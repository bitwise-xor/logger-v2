"""End-to-end test of the food and health write path on the real v1 messages.

For each message: rewind the sandbox to the moment it was sent (entries logged later are removed, inside a transaction),
read it with the parser answers of a cached run (the larger model is called live only when something stays open),
check and save with record.py, compare the saved rows with what v1 saved, then roll everything back.

    tools/import/sandbox.sh up private-dumps/<v1 dump>
    .venv/bin/python tools/golden/e2e.py --answers r5 --run w1
    tools/import/sandbox.sh down
"""
import argparse
import json
import pathlib
import sys
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
from app import record  # noqa: E402
from app.ai.provider import AnthropicProvider  # noqa: E402
from app.when import BERLIN  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
FMT = "%Y-%m-%d %H:%M"
DSN = "postgresql://core_rw:sandbox-core@127.0.0.1:55435/appdata"  # the core's own role, so missing rights show up


def env_value(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():  # literal, no shell parsing
        if line.startswith(key + "="):
            return line[len(key) + 1:].strip()
    sys.exit(f"{key} is not set in {ROOT / '.env'}")


def aware(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=BERLIN)


def merged(rows: list[dict], key) -> list[dict]:
    """The same food at the same time counts once, amounts added (v1 sometimes saved '2 glasses' as two rows)."""
    out: list[dict] = []
    for r in rows:
        same = next((x for x in out if key(x) == key(r)), None)
        if same:
            same["amount"] += r["amount"]
        else:
            out.append(dict(r))
    return out


def compare(exp: dict, food: list[dict], health: list[dict]) -> tuple[list[str], dict]:
    probs, c = [], dict(f_exp=0, f_ok=0, f_meal=0, h_exp=len(exp["health"]), h_ok=0)
    efood = merged(exp["food"], lambda x: (x["food_id"], x["at"]))
    sfood = merged(food, lambda x: (x["food_id"], x["at"]))
    c["f_exp"] = len(efood)
    used = set()
    for e in efood:
        hit = [i for i, s in enumerate(sfood) if i not in used and s["food_id"] == e["food_id"]]
        if not hit:
            probs.append(f"food missing: {e['name']} {e['amount']:g}")
            continue
        i = min(hit, key=lambda i: abs(sfood[i]["amount"] - e["amount"]))
        used.add(i)
        s, bad = sfood[i], []
        if abs(s["amount"] - e["amount"]) > max(2, (0.25 if s["estimated"] else 0.05) * e["amount"]):
            bad.append(f"amount {s['amount']:g} vs {e['amount']:g}")
        if e["time_known"] and abs((aware(s["at"]) - aware(e["at"])).total_seconds()) > 900:
            bad.append(f"time {s['at'][5:]} vs {e['at'][5:]}")
        if s["day"] != e["day"]:
            bad.append(f"day {s['day']} vs {e['day']}")
        if bool(s["drink"]) != bool(e["drink"]):
            bad.append("drink flag")
        if bad:
            probs.append(f"food {e['name']}: " + ", ".join(bad))
        else:
            c["f_ok"] += 1
        c["f_meal"] += s["meal"] == e["meal"]
    extra = [s["name"] for i, s in enumerate(sfood) if i not in used]
    if extra:
        probs.append(f"food extra: {extra}")
    used = set()
    for e in exp["health"]:
        hit = [i for i, s in enumerate(health) if i not in used and s["type"] == e["type"]]
        if not hit:
            probs.append(f"health missing: {e['type']}")
            continue
        i = min(hit, key=lambda i: abs((aware(health[i]["at"]) - aware(e["at"])).total_seconds()))
        used.add(i)
        s, bad = health[i], []
        if e["value"] is not None and (s["value"] is None or abs(s["value"] - e["value"]) > 0.05):
            bad.append(f"value {s['value']} vs {e['value']:g}")
        if e["value2"] is not None and (s["value2"] is None or abs(s["value2"] - e["value2"]) > 0.05):
            bad.append(f"value2 {s['value2']} vs {e['value2']:g}")
        if e["time_known"] and abs((aware(s["at"]) - aware(e["at"])).total_seconds()) > 900:
            bad.append(f"time {s['at'][5:]} vs {e['at'][5:]}")
        if s["day"] != e["day"]:
            bad.append(f"day {s['day']} vs {e['day']}")
        if bad:
            probs.append(f"health {e['type']}: " + ", ".join(bad))
        else:
            c["h_ok"] += 1
    extra = [s["type"] for i, s in enumerate(health) if i not in used]
    if extra:
        probs.append(f"health extra: {extra}")
    return probs, c


def saved_rows(cur, raw_id: int) -> tuple[list[dict], list[dict]]:
    cur.execute("""SELECT e.food_id, f.name, e.amount, e.estimated, e.is_drink AS drink, e.day, e.meal,
                          to_char(e.eaten_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI') AS at
                   FROM food_entries e JOIN foods f ON f.id = e.food_id WHERE e.raw_id = %s ORDER BY e.id""", (raw_id,))
    food = [dict(r, amount=float(r["amount"]), day=str(r["day"])) for r in cur.fetchall()]
    cur.execute("""SELECT t.name AS type, m.value1 AS value, m.value2, m.day, to_char(m.event_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI') AS at
                   FROM measurements m JOIN log_types t ON t.id = m.type_id WHERE m.raw_id = %s
                   UNION ALL
                   SELECT t.name, NULL, NULL, e.day, to_char(e.event_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI')
                   FROM events e JOIN log_types t ON t.id = e.type_id WHERE e.raw_id = %s""", (raw_id, raw_id))
    health = [dict(r, value=None if r["value"] is None else float(r["value"]),
                   value2=None if r["value2"] is None else float(r["value2"]), day=str(r["day"])) for r in cur.fetchall()]
    return food, health


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True, help="cached parser run to use (tests/golden/runs/<name>.jsonl)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--no-escalation", action="store_true")
    a = ap.parse_args()

    cases = [json.loads(x) for x in (GOLDEN / "cases.jsonl").read_text().splitlines()]
    answers = {json.loads(x)["id"]: json.loads(x) for x in (GOLDEN / "runs" / f"{a.answers}.jsonl").read_text().splitlines()}
    disputed = json.loads((GOLDEN / "disputed.json").read_text())
    provider = AnthropicProvider(env_value("ANTHROPIC_API_KEY"))
    results, esc_usages = [], []
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        for c in cases:
            pred = answers.get(c["id"], {}).get("pred")
            if c["skip"] or pred is None or c["id"].startswith("p"):
                continue
            exp = c["expected"]
            if not (exp["food"] or exp["health"] or pred.get("food") or pred.get("health")):
                continue
            now = aware(c["now"])
            res = dict(id=c["id"], text=c["text"], disputed=disputed.get(c["id"]))
            try:
                with conn.cursor() as cur:
                    for t in ("food_entries", "measurements", "events"):  # rewind to the moment the message was sent
                        cur.execute(f"DELETE FROM {t} WHERE logged_at >= %s", (now,))
                    prev = [(datetime.strptime(t, FMT), m) for t, m in c["previous"]]
                    meals = [(lab, datetime.strptime(t, FMT), names) for lab, t, names in c["meals"]]
                    r, usages, esc = record.read(cur, provider, c["text"], now, prev, meals, pred=pred, escalate=not a.no_escalation)
                    esc_usages += usages
                    res["escalated"] = esc
                    out = record.save(cur, r, c["text"], "golden", now)
                    res["confirm"] = out["outcome"] == "needs_confirmation"
                    if res["confirm"]:
                        res["read_back"] = out["read_back"]
                        out = record.save(cur, r, c["text"], "golden", now, confirmed=True)  # the user says yes
                    res["outcome"] = out["outcome"]
                    res["questions"] = out.get("questions") or ([out["reason"]] if out.get("reason") else [])
                    food, health = saved_rows(cur, out["raw_id"]) if out["outcome"] in ("saved", "partly_saved") else ([], [])
                    res["problems"], res["counts"] = compare(exp, food, health)
                    res["receipt"] = out.get("receipt", [])
            except Exception as e:  # noqa: BLE001
                res.update(outcome="error", problems=[f"{type(e).__name__}: {e}"], counts=dict(f_exp=0, f_ok=0, f_meal=0, h_exp=0, h_ok=0))
            finally:
                conn.rollback()
            results.append(res)

    def block(title, rs):
        n = len(rs)
        cnt = lambda k: sum(r["counts"][k] for r in rs)
        oc = lambda o: sum(1 for r in rs if r["outcome"] == o)
        pct = lambda x, y: f"{100 * x / y:.0f}%" if y else "-"
        right = sum(1 for r in rs if r["outcome"] == "saved" and not r["problems"])
        return [f"### {title}: {n} messages with food or health", "",
                f"- Saved and fully right: {right} of {n} ({pct(right, n)})",
                f"- Outcomes: saved {oc('saved')}, partly saved with a question {oc('partly_saved')}, "
                f"only questions {oc('needs_choice')}, rejected {oc('rejected')}, "
                f"errors {oc('error')}, nothing to save {oc('nothing')}",
                f"- Needed a read-back confirmation first (odd value, second weight of the day ...): {sum(r.get('confirm', False) for r in rs)}",
                f"- Read again with the larger model: {sum(r.get('escalated', False) for r in rs)}",
                f"- Food rows right (food, amount, time, day, drink): {cnt('f_ok')} of {cnt('f_exp')} ({pct(cnt('f_ok'), cnt('f_exp'))}); "
                f"same meal number as v1: {cnt('f_meal')}",
                f"- Health rows right (type, values, time, day): {cnt('h_ok')} of {cnt('h_exp')} ({pct(cnt('h_ok'), cnt('h_exp'))})"]

    fair = [r for r in results if not r["disputed"]]
    esc_cost = sum(u.usd for u in esc_usages)
    lines = [f"# Write path run '{a.run}' (parser answers from '{a.answers}')", ""] + block("All", results) + [""] \
        + block(f"Without the {len(results) - len(fair)} disputed", fair) \
        + ["", f"Larger-model calls: {len(esc_usages)}, about ${esc_cost:.3f}", "", "## Messages with problems"]
    for r in results:
        if r["problems"] or r["outcome"] != "saved":
            lines.append(f"\n**{r['id']}** [{r['outcome']}]" + (f" DISPUTED: {r['disputed']}" if r["disputed"] else "")
                         + (" (escalated)" if r.get("escalated") else "") + f": {r['text'][:200]!r}")
            lines += [f"- {p}" for p in r["problems"]] + [f"- question: {q}" for q in r.get("questions", [])]
            if r.get("read_back"):
                lines.append(f"- read-back first: {r['read_back'][:300]!r}")
    rep = GOLDEN / "runs" / f"{a.run}-e2e-report.md"
    rep.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[: lines.index("## Messages with problems")]))
    print(f"full report: {rep}")


if __name__ == "__main__":
    main()
