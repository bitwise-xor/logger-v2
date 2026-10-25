"""Replay all real v1 chat messages, in order, through the MCP tools `log` and `answer`, as if the user had used v2
from the start. The sandbox keeps v1's imported history but none of v1's chat entries; every message is sent at its
original time and committed, so later messages see what v2 saved earlier. Open questions are answered and odd values
confirmed the way the user would. Then each message's saved rows are compared with what v1 saved.

The small model's answers come from a cached parser run (no new cost); the larger model is called live when needed.

    tools/import/sandbox.sh up private-dumps/<v1 dump>
    .venv/bin/python tools/golden/replay.py --answers r7 --run replay1
    tools/import/sandbox.sh down
"""
import argparse
import hashlib
import json
import os
import pathlib
import secrets
import sys
from datetime import datetime

os.environ.update(DB_HOST="127.0.0.1", DB_PORT="55435", CORE_RW_PASSWORD="sandbox-core", APP_OWNER_PASSWORD="sandbox-owner",
                  MCP_PATH="/mcp", ALLOWED_HOSTS="log.example.test", PUBLIC_URL="https://log.example.test")
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "tools" / "golden"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app import logbook  # noqa: E402
from app.ai import parser  # noqa: E402
from app.ai.provider import AnthropicProvider, Usage  # noqa: E402
from app.mcp_app import build_app  # noqa: E402
from e2e import FMT, aware, compare, env_value, saved_rows  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"
ADMIN = "postgresql://pgadmin:sandbox@127.0.0.1:55435/appdata"
HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "log.example.test"}


class CachedProvider:
    """Small-model answers from a cached run; anything else (the larger model) goes to the real API."""

    def __init__(self, cache: dict, real):
        self.cache, self.real, self.live = cache, real, []

    def json(self, system, user, schema, model, max_tokens=4000):
        text = user.split('Message:\n"""\n', 1)[1].rsplit('\n"""', 1)[0]
        hit = self.cache.get(text)
        if model == parser.DEFAULT_MODEL and hit:
            return hit["pred"], Usage(**hit["usage"])
        pred, u = self.real.json(system, user, schema, model, max_tokens)
        self.live.append(u)
        return pred, u


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--run", required=True)
    a = ap.parse_args()

    cases = [json.loads(x) for x in (GOLDEN / "cases.jsonl").read_text().splitlines()]
    cached = [json.loads(x) for x in (GOLDEN / "runs" / f"{a.answers}.jsonl").read_text().splitlines()]
    by_id = {c["id"]: c for c in cases}
    cache = {by_id[d["id"]]["text"]: d for d in cached if "pred" in d}
    disputed = json.loads((GOLDEN / "disputed.json").read_text())
    prov = CachedProvider(cache, AnthropicProvider(env_value("ANTHROPIC_API_KEY")))
    logbook.PROVIDER = prov

    admin = psycopg.connect(ADMIN, row_factory=dict_row, autocommit=True)

    def q(sql, *args):
        cur = admin.execute(sql, args)
        return cur.fetchall() if cur.description else []

    # remove v1's chat entries; v1's imported history stays (it existed before the first chat message).
    # No lab report came from the chat, so the lab tables are left alone.
    chat = "(SELECT id FROM raw_inbox WHERE channel = 'claude-chat')"
    for t in ("food_entries", "measurements", "events", "thought_updates", "thoughts", "food_changes"):
        admin.execute(f"DELETE FROM {t} WHERE raw_id IN {chat}")
    admin.execute("DELETE FROM private.entries WHERE channel = 'claude-chat'")
    admin.execute("DELETE FROM raw_inbox WHERE channel = 'claude-chat'")
    tok = secrets.token_urlsafe(32)
    admin.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, scopes, expires_at) VALUES (%s, 'access', 'replay', ARRAY['log'], now() + interval '1 day')",
                  (hashlib.sha256(tok.encode()).hexdigest(),))
    auth = {"Authorization": "Bearer " + tok}

    results = []
    with TestClient(build_app(), base_url="http://log.example.test") as c:
        def call(name, **args):
            r = c.post("/mcp", headers={**HDR, **auth}, content=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}))
            res = r.json()["result"]
            if res.get("isError"):
                return {"outcome": "error", "message": res["content"][0]["text"][:300]}
            return res.get("structuredContent") or json.loads(res["content"][0]["text"])

        for case in sorted((x for x in cases if not x["skip"]), key=lambda x: (x["now"], x["id"])):
            now = aware(case["now"])
            logbook.clock = lambda now=now: now
            exp = case["expected"]
            res = dict(id=case["id"], text=case["text"], disputed=disputed.get(case["id"]))
            out = call("log", text=case["text"], idempotency_key=case["id"])
            res["outcome"] = out["outcome"]
            raw_id = out.get("raw_id")
            for qn in out.get("questions", []):  # answer the way the user would
                p = q("SELECT tool, payload FROM pending WHERE id = %s", qn["question_id"])[0]
                if p["tool"] == "log_confirm":
                    res["confirmed"] = True
                    out2 = call("answer", question_id=qn["question_id"], reply="yes")
                    raw_id = out2.get("raw_id") or raw_id
                    res["outcome"] = out2["outcome"]
                    continue
                have = [r["food_id"] for r in q("SELECT food_id FROM food_entries WHERE raw_id = %s", raw_id)] if raw_id else []
                missing = [e for e in exp["food"] if e["food_id"] not in have]
                pay = p["payload"]
                if pay["kind"] == "food":
                    m = next((e for e in missing if not pay.get("candidates") or e["food_id"] in pay["candidates"]), None)
                    reply = f"N{m['food_id']}" if m else "skip"
                elif pay["kind"] == "amount":
                    m = next((e for e in missing if e["food_id"] == pay.get("food_id")), None)
                    reply = f"{m['amount']:g}" if m else "skip"
                else:
                    reply = "skip"
                call("answer", question_id=qn["question_id"], reply=reply)
                res["answered"] = res.get("answered", 0) + 1
            private = None
            if out.get("private_id"):
                private = q("SELECT label FROM private.entries WHERE id = %s", out["private_id"])[0]["label"]
            with admin.cursor() as cur:
                food, health, thoughts = saved_rows(cur, raw_id) if raw_id else ([], [], [])
            res["problems"], res["counts"] = compare(exp, food, health, thoughts, private,
                                                     "saved" if res["outcome"] == "needs_confirmation" else res["outcome"])
            res["message"] = out.get("message", "")
            results.append(res)

    def block(title, rs):
        n = len(rs)
        cnt = lambda k: sum(r["counts"][k] for r in rs)
        oc = lambda o: sum(1 for r in rs if r["outcome"] == o)
        pct = lambda x, y: f"{100 * x / y:.0f}%" if y else "-"
        right = sum(1 for r in rs if not r["problems"])
        return [f"### {title}: {n} messages", "",
                f"- Fully right after answering questions: {right} of {n} ({pct(right, n)})",
                f"- First reply: saved {oc('saved')}, partly saved {oc('partly_saved')}, only questions {oc('needs_choice')}, "
                f"not logged {oc('not_logged')}, kept as text {oc('kept')}, rejected {oc('rejected')}, errors {oc('error')}",
                f"- Log vs question vs command right: {cnt('i_ok')} of {n} ({pct(cnt('i_ok'), n)})",
                f"- Food rows right: {cnt('f_ok')} of {cnt('f_exp')} ({pct(cnt('f_ok'), cnt('f_exp'))})",
                f"- Health rows right: {cnt('h_ok')} of {cnt('h_exp')} ({pct(cnt('h_ok'), cnt('h_exp'))})",
                f"- Thoughts right: {cnt('t_ok')} of {cnt('t_exp')} ({pct(cnt('t_ok'), cnt('t_exp'))}); "
                f"private and gratitude right: {cnt('p_ok')} of {cnt('p_exp')}",
                f"- Confirmations given: {sum(r.get('confirmed', False) for r in rs)}; questions answered: {sum(r.get('answered', 0) for r in rs)}"]

    fair = [r for r in results if not r["disputed"]]
    usd = sum(u.usd for u in prov.live)
    lines = [f"# Replay '{a.run}' through the MCP tools (small-model answers from '{a.answers}')", ""] + block("All", results) \
        + [""] + block(f"Without the {len(results) - len(fair)} disputed", fair) \
        + ["", f"Live larger-model calls: {len(prov.live)}, about ${usd:.3f}", "", "## Messages with problems"]
    for r in results:
        if r["problems"] or r["outcome"] == "error":
            lines.append(f"\n**{r['id']}** [{r['outcome']}]" + (f" DISPUTED: {r['disputed']}" if r["disputed"] else "")
                         + f": {r['text'][:200]!r}")
            lines += [f"- {p}" for p in r["problems"]]
            if r["outcome"] == "error":
                lines.append(f"- error: {r['message']}")
    sample = next((r for r in results if r["outcome"] == "saved" and r["counts"]["f_exp"] >= 2 and not r["problems"]), None)
    if sample:
        lines += ["", "## Example reply the chat app receives", "", f"Message: {sample['text']!r}", "", "```", sample["message"], "```"]
    rep = GOLDEN / "runs" / f"{a.run}-replay-report.md"
    rep.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[: lines.index("## Messages with problems")]))
    print(f"full report: {rep}")


if __name__ == "__main__":
    main()
