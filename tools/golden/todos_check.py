"""Your real v1 to-do messages through the v2 Todoist rules, against a copy of your Todoist (read once, read-only, into
a fake), with the parser's cached answers and the real placement call. Nothing is written to Todoist or the database.

    .venv/bin/python tools/golden/todos_check.py --answers r13
"""
import argparse
import copy
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "tools" / "golden"))

from app import todos  # noqa: E402
from app.ai.provider import AnthropicProvider  # noqa: E402
from app.todoist import FakeTodoist, TodoistAPI  # noqa: E402
from evaluate import env_value  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"


def snapshot() -> FakeTodoist:
    real = TodoistAPI(env_value("TODOIST_API_TOKEN"))
    secs, tasks = {}, []
    for p in (todos.SHOPPING, todos.LISTS):
        secs[p] = [(s["id"], s["name"]) for s in real.sections(p)]
        tasks += [{**t, "checked": False} for t in real.open_tasks(p)]
    tasks += [{**t, "checked": True} for t in real.completed_tasks(todos.SHOPPING)]
    uniq = {}
    for t in tasks:
        uniq.setdefault(t["id"], t)
    return FakeTodoist(secs, list(uniq.values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    a = ap.parse_args()
    cases = [json.loads(x) for x in (GOLDEN / "cases.jsonl").read_text().splitlines()]
    preds = {json.loads(x)["id"]: json.loads(x)["pred"] for x in (GOLDEN / "runs" / f"{a.answers}.jsonl").read_text().splitlines()}
    base = snapshot()
    todos._record = lambda *args, **kw: None  # no database here
    prov = AnthropicProvider(env_value("ANTHROPIC_API_KEY"))
    usd = 0.0
    print(f"# Your real to-do messages through v2's Todoist rules (copy of your list: {len(base.tasks)} tasks)\n")
    for c in cases:
        if not c["expected"].get("todos"):
            continue
        pred = preds.get(c["id"]) or {}
        todos.CLIENT, todos._cache = copy.deepcopy(base), {}
        out = todos.apply(None, prov, pred.get("todos") or [], 0, None, c["text"])
        usd += sum(u.usd for u in out["usages"])
        print(f"**{c['id']}** {c['text'][:90]!r}")
        print(f"- v1 handled: {', '.join(c['expected']['todos'])}")
        print(f"- v2 parser: {', '.join(f'{t['item']} ({t['list']}{', ' + t['store'] if t['store'] else ''})' for t in pred.get('todos') or []) or 'none'}")
        for line in out["receipt"] + [f"note: {n}" for n in out["notes"]] + [f"question: {q['text']}" for q in out["ask"]]:
            print(f"- {line}")
        print()
    print(f"Placement calls: about ${usd:.4f}")


if __name__ == "__main__":
    main()