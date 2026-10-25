"""Run the parser over the test set and score it against what v1 saved.

    .venv/bin/python tools/golden/evaluate.py --run r2 --limit 20     # a sample
    .venv/bin/python tools/golden/evaluate.py --run r2                # the rest; answers already fetched are reused
    .venv/bin/python tools/golden/evaluate.py --run r2 --score-only   # score again without calling the API

Food names, units and times go through the code v2 will use (v1's matcher, unit table and date reader, plus
foods.pick), against the throwaway v1 copy in GOLDEN_DSN. tests/golden/disputed.json lists cases where v1's
saved entries are not a fair answer (corrected later, or needing chat history); they are scored both ways.
"""
import argparse
import json
import os
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
from app.ai import parser  # noqa: E402
from app.ai.provider import AnthropicProvider, Usage  # noqa: E402
from app.foods import amount, pick  # noqa: E402
from app.v1_food import _food_cat, _resolve_type, _types  # noqa: E402
from app.clock import read_when  # noqa: E402
from app.when import BERLIN  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
FMT = "%Y-%m-%d %H:%M"
DSN = os.environ.get("GOLDEN_DSN", "postgresql://pgadmin:golden@127.0.0.1:55434/appdata")


def env_value(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():  # literal, no shell parsing
        if line.startswith(key + "="):
            return line[len(key) + 1:].strip()
    sys.exit(f"{key} is not set in {ROOT / '.env'}")


def aware(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=BERLIN)


def when(text, now: datetime):
    """-> (datetime, problem). Null means now, as in the core."""
    ts, _, prob = read_when(text, now)
    return ts, prob


def near(a: datetime, b: str, known=True) -> bool:
    return not known or abs((a - aware(b)).total_seconds()) <= 15 * 60


def score_case(cur, cat, types, case: dict, pred: dict) -> dict:
    exp, now = case["expected"], aware(case["now"])
    r = {"id": case["id"], "problems": [], "food": {}, "health": {}}
    want = exp["intent"]
    r["intent_ok"] = pred["intent"] == want or (want == "command" and bool(pred.get("command_text")))
    if not r["intent_ok"]:
        r["problems"].append(f"intent: expected {want}, got {pred['intent']}")
    r["private_ok"] = pred.get("private") == exp["private"]
    if not r["private_ok"]:
        r["problems"].append(f"private: expected {exp['private']}, got {pred.get('private')}")

    # food: what the core would do with each parsed item
    items = []
    for it in pred.get("food", []):
        f, how, cands = pick(cur, cat, it["food"], it["cooked"], before=now)
        grams = est = prob = None
        if f and it["amount"] is not None:
            grams, est, prob = amount(cur, f, it["amount"], it["unit"], it["grams_per_unit"])
        at, tprob = when(it["when"], now)
        drink = it["drink"] if it["drink"] is not None else bool(f and f["is_drink"])  # null = the food's own setting
        items.append(dict(it, f=f, how=how, cands=[c["id"] for c in cands], grams=grams, est=est, prob=prob or tprob, at=at, drink=drink))
    # the same food at the same time counts once, with the amounts added ("2 glasses" = one item or two)
    merged = []
    for p in items:
        same = next((q for q in merged if p["f"] and q["f"] and q["f"]["id"] == p["f"]["id"] and abs((q["at"] - p["at"]).total_seconds()) <= 900), None)
        if same and same["grams"] is not None and p["grams"] is not None:
            same["grams"] += p["grams"]
        else:
            merged.append(p)
    items = merged
    efood = []
    for e in exp["food"]:
        same = next((x for x in efood if x["food_id"] == e["food_id"] and x["at"] == e["at"]), None)
        if same:
            same["amount"] += e["amount"]
        else:
            efood.append(dict(e))
    r["needs_help"] = any(p["how"] in ("ask", "none") or (p["f"] and p["grams"] is None) or p["prob"] for p in items)
    used, c = set(), dict(expected=len(efood), produced=len(items), right_food=0, exact=0, asked=0, new_food=0, estimated=0)
    for e in efood:
        hit = [i for i, p in enumerate(items) if i not in used and p["f"] and p["f"]["id"] == e["food_id"]]
        if not hit:
            ask = [i for i, p in enumerate(items) if i not in used and p["how"] == "ask" and e["food_id"] in p["cands"]]
            if ask:
                used.add(ask[0])
                c["asked"] += 1
                r["problems"].append(f"food {e['name']}: would ask (\"{items[ask[0]]['food']}\" has {len(items[ask[0]]['cands'])} candidates)")
            elif e["new"]:
                c["new_food"] += 1
                r["problems"].append(f"food {e['name']}: new food, v1 added it from a database or estimate")
                miss = [i for i, p in enumerate(items) if i not in used and not p["f"]]
                if miss:
                    used.add(miss[0])
            else:
                r["problems"].append(f"food missing: {e['name']} {e['amount']:g}")
            continue
        i = min(hit, key=lambda i: abs((items[i]["grams"] or 0) - e["amount"]))
        used.add(i)
        p, bad = items[i], []
        c["right_food"] += 1
        tol = 0.25 if p["est"] else 0.05
        if p["grams"] is None:
            bad.append(p["prob"] or "no amount")
        elif abs(p["grams"] - e["amount"]) > max(2, tol * e["amount"]):
            bad.append(f"amount {p['grams']:g} vs {e['amount']:g}" + (" (estimated unit)" if p["est"] else ""))
        if not near(p["at"], e["at"], e["time_known"]):
            bad.append(f"time {p['at']:%d.%m %H:%M} vs {e['at'][8:10]}.{e['at'][5:7]} {e['at'][11:]}" + (f" (when='{p['when']}')" if p["when"] else ""))
        if bool(p["drink"]) != bool(e["drink"]):
            bad.append("drink flag")
        if bad:
            r["problems"].append(f"food {e['name']}: " + ", ".join(bad))
        else:
            c["exact"] += 1
            c["estimated"] += bool(p["est"])
    extra = [f"{p['food']} -> {('N%d %s' % (p['f']['id'], p['f']['name'])) if p['f'] else p['how']}" for i, p in enumerate(items) if i not in used]
    if extra:
        r["problems"].append(f"food extra: {extra}")
    r["food"] = c

    # health and habits
    hp = []
    for h in pred.get("health", []):
        t, sess = _resolve_type(types, h["type"])
        at, tprob = when(h["when"], now)
        hp.append(dict(h, t=t["name"] if t else h["type"], at=at, prob=tprob))
        r["needs_help"] = r["needs_help"] or not t or bool(tprob)
    used, h_ok = set(), 0
    for e in exp["health"]:
        cands = [i for i, p in enumerate(hp) if i not in used and p["t"] == e["type"]]
        if not cands:
            r["problems"].append(f"health missing: {e['type']} {e['value'] if e['value'] is not None else ''}")
            continue
        i = min(cands, key=lambda i: abs((hp[i]["at"] - aware(e["at"])).total_seconds()))
        used.add(i)
        p, bad = hp[i], []
        if e["value"] is not None and (p["value"] is None or abs(p["value"] - e["value"]) > 0.05):
            bad.append(f"value {p['value']} vs {e['value']:g}")
        if e["value2"] is not None and (p["value2"] is None or abs(p["value2"] - e["value2"]) > 0.05):
            bad.append(f"value2 {p['value2']} vs {e['value2']:g}")
        if not near(p["at"], e["at"], e["time_known"]):
            bad.append(f"time {p['at']:%d.%m %H:%M} vs {e['at'][8:10]}.{e['at'][5:7]} {e['at'][11:]}" + (f" (when='{p['when']}')" if p["when"] else ""))
        if bad:
            r["problems"].append(f"health {e['type']}: " + ", ".join(bad))
        else:
            h_ok += 1
    extra = [p["type"] for i, p in enumerate(hp) if i not in used]
    if extra:
        r["problems"].append(f"health extra: {extra}")
    r["health"] = dict(expected=len(exp["health"]), produced=len(hp), exact=h_ok)

    et, pt = exp["thoughts"], pred.get("thoughts", [])
    r["thoughts_ok"] = len(et) == len(pt) and bool(exp["private"] or sorted(t["thought_type"] for t in et) == sorted(t["thought_type"] for t in pt))
    if not r["thoughts_ok"]:
        r["problems"].append(f"thoughts: expected {[t.get('thought_type') for t in et]}, got {[t['thought_type'] for t in pt]}")
    norm = lambda s: " ".join(s.lower().split())
    ew, pw = [norm(x) for x in exp["todos"]], [norm(t["item"]) for t in pred.get("todos", [])]
    r["todos_ok"] = len(ew) == len(pw) and all(any(a in b or b in a for b in pw) for a in ew)
    if not r["todos_ok"]:
        r["problems"].append(f"todos: expected {exp['todos']}, got {[t['item'] for t in pred.get('todos', [])]}")
    r["exact"] = not r["problems"]
    return r


def run(cases, provider, model, cache_path, workers):
    done = {}
    if cache_path.exists():
        for line in cache_path.read_text().splitlines():
            d = json.loads(line)
            done[d["id"]] = d
    todo = [c for c in cases if c["id"] not in done]

    def one(c):
        prev = [(datetime.strptime(t, FMT), m) for t, m in c["previous"]]
        meals = [(label, datetime.strptime(t, FMT), names) for label, t, names in c["meals"]]
        try:
            pred, u = parser.parse(provider, c["text"], datetime.strptime(c["now"], FMT), prev, meals, model)
            return dict(id=c["id"], pred=pred, usage=u.__dict__)
        except Exception as e:  # noqa: BLE001 - keep going, record the failure
            return dict(id=c["id"], error=f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(workers) as pool, cache_path.open("a") as out:
        for i, d in enumerate(pool.map(one, todo), 1):
            out.write(json.dumps(d, ensure_ascii=False) + "\n")
            out.flush()
            done[d["id"]] = d
            if i % 25 == 0:
                print(f"  {i}/{len(todo)} done", flush=True)
    return done


def summary(title: str, results: list, usages: list) -> list[str]:
    n = len(results)
    s = lambda k: sum(1 for _, r in results if r[k])
    f = lambda k: sum(r["food"][k] for _, r in results)
    h = lambda k: sum(r["health"][k] for _, r in results)
    pct = lambda x, y: f"{100 * x / y:.0f}%" if y else "-"
    fe = f("expected")
    out = [f"### {title}: {n} messages", "",
           f"- Messages fully right: {s('exact')} of {n} ({pct(s('exact'), n)})",
           f"- Intent right: {s('intent_ok')} ({pct(s('intent_ok'), n)}); private label right: {s('private_ok')} ({pct(s('private_ok'), n)})",
           f"- Food entries: {fe} expected, {f('produced')} produced",
           f"  - right food chosen automatically: {f('right_food')} ({pct(f('right_food'), fe)}); fully right incl. amount, time, drink: {f('exact')} ({pct(f('exact'), fe)}), of which {f('estimated')} with an estimated unit weight",
           f"  - the core would ask which food (right one among the choices): {f('asked')} ({pct(f('asked'), fe)})",
           f"  - new foods v1 added from a database or estimate: {f('new_food')} ({pct(f('new_food'), fe)})",
           f"- Health and habit entries: {h('expected')} expected, {h('produced')} produced, fully right {h('exact')} ({pct(h('exact'), h('expected'))})",
           f"- Thoughts right: {s('thoughts_ok')} ({pct(s('thoughts_ok'), n)}); to-dos right: {s('todos_ok')} ({pct(s('todos_ok'), n)})",
           f"- Messages where the core could not finish alone (would escalate or ask): {s('needs_help')} ({pct(s('needs_help'), n)})"]
    if usages:
        usd = sum(u.usd for u in usages)
        secs = sorted(u.seconds for u in usages)
        out += [f"- Cost: about ${usd:.3f}, ${usd / len(usages) * 1000:.2f} per 1,000 messages (estimate)",
                f"- Wait per message: median {secs[len(secs) // 2]:.1f} s, 90% under {secs[int(len(secs) * 0.9)]:.1f} s, slowest {secs[-1]:.1f} s"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="name of this run (answers are cached under it)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default=parser.DEFAULT_MODEL)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--score-only", action="store_true")
    a = ap.parse_args()

    cases = [json.loads(x) for x in (GOLDEN / "cases.jsonl").read_text().splitlines()]
    cases = [c for c in cases if not c["skip"]][: a.limit]
    dfile = GOLDEN / "disputed.json"
    disputed = json.loads(dfile.read_text()) if dfile.exists() else {}
    runs = GOLDEN / "runs"
    runs.mkdir(exist_ok=True)
    cache = runs / f"{a.run}.jsonl"
    if a.score_only:
        answers = {json.loads(x)["id"]: json.loads(x) for x in cache.read_text().splitlines()}
    else:
        answers = run(cases, AnthropicProvider(env_value("ANTHROPIC_API_KEY"), a.effort), a.model, cache, a.workers)

    results, usages, errors = [], [], []
    with psycopg.connect(DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cat = _food_cat(cur)  # the copy has tools/import/v1_fixes.sql applied, like the real import
        _, types = _types(cur)
        for c in cases:
            d = answers.get(c["id"])
            if not d:
                continue
            if "error" in d:
                errors.append((c["id"], d["error"]))
                continue
            usages.append(Usage(**d["usage"]))
            results.append((c, score_case(cur, cat, types, c, d["pred"])))

    fair = [(c, r) for c, r in results if c["id"] not in disputed]
    lines = [f"# Parser run '{a.run}' ({a.model}, effort {a.effort}), {len(errors)} errors", ""]
    lines += summary("All messages", results, usages) + [""]
    lines += summary(f"Without the {len(results) - len(fair)} disputed cases", fair, []) + ["", "## Messages with problems"]
    for c, r in results:
        if r["problems"]:
            tag = f" — DISPUTED: {disputed[c['id']]}" if c["id"] in disputed else ""
            lines.append(f"\n**{c['id']}** ({c['now']}){tag}: {c['text'][:300]!r}")
            lines += [f"- {p}" for p in r["problems"]]
    for cid, e in errors:
        lines.append(f"\n**{cid}**: ERROR {e}")
    report = runs / f"{a.run}-report.md"
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[: lines.index("## Messages with problems")]))
    print(f"full report: {report}")


if __name__ == "__main__":
    main()
