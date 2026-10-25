"""The core's free-text operations: log one message, answer one open question. A thin layer around record.py that
adds what a live call needs: the connection, the AI provider, the context (earlier messages, today's meals),
the idempotency key, the audit log (ids, outcomes and AI usage only, never health values) and a short reply text."""
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

from psycopg.types.json import Jsonb

from . import record
from .ai.provider import AnthropicProvider, Provider
from .db import connect
from .v1_food import _food_cat, _types
from .when import BERLIN

CHANNEL = "mcp"
PROVIDER: Optional[Provider] = None  # created on first use; tests put a fake here
# A confirmation counts when the reply starts with a yes ("yes, it is correct ..."); only a clear no cancels.
YES = re.compile(r"^\s*(yes|y|ok|okay|confirm(ed)?|correct|right|ja|jawohl|stimmt|richtig)\b", re.I)
NO = re.compile(r"^\s*(no|nope|nein|skip|cancel|stop|drop)\b", re.I)


def clock() -> datetime:
    """Now, in Berlin. The replay test sets this to each message's original time."""
    return datetime.now(BERLIN)


def provider() -> Provider:
    global PROVIDER
    if PROVIDER is None:
        PROVIDER = AnthropicProvider(os.environ["ANTHROPIC_API_KEY"])
    return PROVIDER


def _audit(cur, tool: str, outcome: str, detail: dict) -> None:
    cur.execute("INSERT INTO audit_log (tool, outcome, client, detail) VALUES (%s, %s, 'mcp', %s)", (tool, outcome, Jsonb(detail)))


def _context(cur, now: datetime) -> tuple[list, list]:
    """The parser's context: the last few messages of the last 6 hours, and what was eaten and drunk today."""
    cur.execute("""SELECT received_at, content FROM raw_inbox WHERE received_at > %s - interval '6 hours'
                   AND channel <> 'catalogue' AND content NOT LIKE 'add food: %%'
                   ORDER BY received_at DESC LIMIT 4""", (now,))  # the logger's own catalogue rows are not messages
    prev = [(r["received_at"].astimezone(BERLIN).replace(tzinfo=None), r["content"]) for r in reversed(cur.fetchall())]
    cur.execute("""SELECT e.day, e.meal, e.is_drink, e.eaten_at, f.name FROM food_entries e JOIN foods f ON f.id = e.food_id
                   WHERE e.time_known AND e.eaten_at <= %s AND e.eaten_at > %s - interval '18 hours' ORDER BY e.eaten_at, e.id""",
                (now, now))
    meals: dict = {}
    for m in cur.fetchall():
        key = ("drink", m["eaten_at"]) if m["is_drink"] else ("meal", m["day"], m["meal"])
        label = "drink" if m["is_drink"] else f"meal {m['meal']}"
        t = m["eaten_at"].astimezone(BERLIN).replace(tzinfo=None)
        meals.setdefault(key, [label, t, []])[2].append(m["name"])
    return prev, [tuple(v) for v in meals.values()]


def _message(out: dict) -> str:
    """One short text for the chat app to pass on."""
    o = out["outcome"]
    head = {"saved": "Saved.", "partly_saved": "Saved what was clear; one or more questions are open.",
            "needs_choice": "Nothing saved yet: a question is open.", "kept": "Kept as text (to-dos come later).",
            "not_logged": "Not logged: this reads as a question or command for you, so answer the user yourself.",
            "needs_confirmation": "Nothing saved yet: please confirm.", "rejected": "Nothing saved.",
            "cancelled": "Skipped.", "replayed": "Already saved earlier (same message)."}.get(o, o)
    lines = [head] + out.get("receipt", []) + [f"note: {n}" for n in out.get("notes", [])]
    lines += [f"question {q['question_id']}: {q['question']}" for q in out.get("questions", [])]
    if out.get("read_back"):
        lines.append(out["read_back"])
    if out.get("reason"):
        lines.append(out["reason"])
    if out.get("questions") or o == "needs_confirmation":
        lines.append("Ask the user, then call answer with the question id and their reply.")
    return "\n".join(lines)


def _public(out: dict) -> dict:
    out = dict(out)
    out["questions"] = [{"question_id": q["pending_id"], "question": q["question"]} for q in out.get("questions", [])]
    out["message"] = _message(out)
    return out


def log(text: str, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    now = clock()
    if not text or not text.strip():
        return {"outcome": "rejected", "message": "Nothing saved: the message is empty."}
    req_hash = hashlib.sha256(text.encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        if idempotency_key:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (idempotency_key,))
            cur.execute("SELECT request_hash, response FROM idempotency_keys WHERE key = %s", (idempotency_key,))
            row = cur.fetchone()
            if row:
                if row["request_hash"] != req_hash:
                    _audit(cur, "log", "rejected", {"why": "key reused"})
                    return {"outcome": "rejected", "message": "This idempotency key was already used for a different message."}
                _audit(cur, "log", "replayed", {})
                return {**row["response"], "replayed": True}
        cur.execute("""SELECT received_at FROM raw_inbox WHERE channel = %s AND lower(content) = lower(%s)
                       AND received_at > %s - interval '30 minutes' AND NOT (metadata ? 'unprocessed')
                       ORDER BY received_at DESC LIMIT 1""", (CHANNEL, text, now))
        same = cur.fetchone()
        if same:  # the same words again within 30 minutes: ask before saving twice
            pid = "p" + secrets.token_hex(4)
            at = same["received_at"].astimezone(BERLIN).strftime("%H:%M")
            cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'log_repeat', %s, %s, %s)",
                        (pid, Jsonb({"text": text, "now": now.isoformat()}), f"same message already logged at {at}",
                         now + timedelta(hours=record.QUESTION_HOURS)))
            resp = _public({"outcome": "needs_confirmation", "reason": f"This exact message was already logged at {at}.",
                            "questions": [{"pending_id": pid, "question": "Log it again (yes) or not (no)?"}]})
            _audit(cur, "log", "needs_confirmation", {"why": "repeat", "questions": [pid]})
            return resp
        out = _process(cur, text, now)
        resp = _public(out)
        if idempotency_key:
            cur.execute("INSERT INTO idempotency_keys (key, tool, request_hash, response) VALUES (%s, 'log', %s, %s)",
                        (idempotency_key, req_hash, Jsonb(resp)))
        return resp


def _process(cur, text: str, now: datetime) -> dict:
    """Read, save and audit one message; the caller makes it public."""
    prev, meals = _context(cur, now)
    r, usages, escalated = record.read(cur, provider(), text, now, prev, meals)
    out = record.save(cur, r, text, CHANNEL, now)
    if out["outcome"] == "needs_confirmation":  # v1's read-back: keep the reading, the user's yes saves it
        pid = "p" + secrets.token_hex(4)
        cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'log_confirm', %s, %s, %s)",
                    (pid, Jsonb({"text": text, "pred": r.pred, "now": now.isoformat()}), out["read_back"][:500],
                     now + timedelta(hours=record.QUESTION_HOURS)))
        out["questions"] = [{"pending_id": pid, "question": "Confirm this (yes), or correct it by logging again."}]
    _audit(cur, "log", out["outcome"], {"raw_id": out.get("raw_id"), "private_id": out.get("private_id"),
                                        "questions": [q["pending_id"] for q in out.get("questions", [])], "escalated": escalated,
                                        "ai": [{"model": u.model, "in": u.input_tokens + u.cache_read_tokens + u.cache_write_tokens,
                                                "out": u.output_tokens, "usd": round(u.usd, 6)} for u in usages]})
    return out


def _confirm_food(cur, out: dict, args: dict, now: datetime) -> dict:
    """A new food waiting for the user's yes (a label, or odd values): keep the call, the yes repeats it."""
    pid = "p" + secrets.token_hex(4)
    cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'add_food_confirm', %s, %s, %s)",
                (pid, Jsonb({**args, "code": out.get("code"), "now": now.isoformat()}), out["read_back"][:500],
                 now + timedelta(hours=record.QUESTION_HOURS)))
    out["questions"] = [{"pending_id": pid, "question": "Show the user these values; if they match the pack (or are right), "
                                                        "answer yes, otherwise call add_food again with the corrected values."}]
    return out


def add_food(name: str, values: dict, question_id: Optional[str] = None, aliases=()) -> dict[str, Any]:
    now = clock()
    with connect() as conn, conn.cursor() as cur:
        out = record.add_food(cur, name, values, "catalogue", now, question_id, aliases=list(aliases or []))
        if out["outcome"] == "needs_confirmation":
            out = _confirm_food(cur, out, {"name": name, "values": values, "question_id": question_id,
                                           "aliases": list(aliases or [])}, now)
        resp = _public(out)
        _audit(cur, "add_food", out["outcome"], {"food_id": out.get("food_id"), "question_id": question_id})
        return resp


def food_words(food_id: int, words: list, means: bool = False) -> dict[str, Any]:
    with connect() as conn, conn.cursor() as cur:
        out = record.add_words(cur, food_id, words, means)
        resp = _public(out)
        _audit(cur, "food_words", out["outcome"], {"food_id": food_id, "means": means})
        return resp


def answer(question_id: str, reply: str) -> dict[str, Any]:
    now = clock()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM pending WHERE id = %s FOR UPDATE", (question_id,))
        p = cur.fetchone()
        if not p or p["tool"] not in ("log", "log_confirm", "log_repeat", "add_food_confirm"):
            _audit(cur, "answer", "rejected", {"why": "unknown question"})
            return {"outcome": "rejected", "message": "There is no such open question."}
        yes = bool(YES.match(reply))
        if p["tool"] == "log":
            out = record.answer(cur, question_id, reply, now)
        elif p["resolved_at"] is not None or p["expires_at"] < now:
            out = {"outcome": "rejected", "reason": "that question was already answered or has expired"}
        elif NO.match(reply):
            cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'cancelled' WHERE id = %s", (question_id,))
            out = {"outcome": "cancelled"}
        elif not yes:  # unclear: keep the question open
            out = {"outcome": "needs_confirmation", "reason": "answer yes to save it, or no to drop it",
                   "questions": [{"pending_id": question_id, "question": "Yes or no?"}]}
        elif p["tool"] == "log_repeat":
            out = _process(cur, p["payload"]["text"], now)
            cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        elif p["tool"] == "add_food_confirm":
            d = p["payload"]
            out = record.add_food(cur, d["name"], d["values"], CHANNEL, now, d["question_id"], food_code=d["code"],
                                  confirmed=True, aliases=d.get("aliases") or [])
            if out["outcome"] == "saved":
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        else:
            d = p["payload"]
            asked = datetime.fromisoformat(d["now"])
            r = record.settle(cur, _food_cat(cur), _types(cur)[1], d["pred"], asked)
            out = record.save(cur, r, d["text"], CHANNEL, asked, confirmed=True)
            if out["outcome"] in ("saved", "partly_saved"):
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        if "question" in out and "questions" not in out:  # a follow-up question about the same item
            out["questions"] = [{"pending_id": question_id, "question": out.pop("question")}]
        resp = _public(out)
        _audit(cur, "answer", out["outcome"], {"question_id": question_id, "raw_id": out.get("raw_id")})
        return resp
