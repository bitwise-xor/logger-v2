"""The core's free-text operations: log one message, answer one open question. A thin layer around record.py that
adds what a live call needs: the connection, the AI provider, the context (earlier messages, today's meals),
the idempotency key, the audit log (ids, outcomes and AI usage only, never health values) and a short reply text."""
import hashlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from psycopg.types.json import Jsonb

from . import commands, held, labs, lookup, record, todos
from .ai import parser
from .ai.provider import LIVE, AnthropicProvider, Provider, ProviderError
from .db import connect
from .v1_food import _food_cat, _types
from .v1_log import Reject
from .when import BERLIN

CHANNEL = "mcp"  # the chat apps (MCP connector)
WEB = "web"  # the web app
LIVE_CHANNELS = [CHANNEL, WEB]  # the repeat guard looks across these
PROVIDER: Optional[Provider] = None  # created on first use; tests put a fake here
_LOG = logging.getLogger("logger.log")
# A confirmation counts when the reply starts with a yes ("yes, it is correct ..."); only a clear no cancels.
YES = re.compile(r"^\s*(yes|y|ok|okay|confirm(ed)?|correct|right|ja|jawohl|stimmt|richtig)\b", re.I)
# The questions `answer` takes (the web app's Questions page lists these).
ANSWER_TOOLS = ("log", "log_confirm", "log_repeat", "add_food_confirm", "command", "command_confirm", "lookup_confirm",
                "report_confirm")
NO = re.compile(r"^\s*(no|nope|nein|skip|cancel|stop|drop)\b", re.I)


def clock() -> datetime:
    """Now, in Berlin. The replay test sets this to each message's original time."""
    return datetime.now(BERLIN)


def provider() -> Provider:
    global PROVIDER
    if PROVIDER is None:
        PROVIDER = AnthropicProvider(os.environ["ANTHROPIC_API_KEY"])
    return PROVIDER


def _audit(cur, tool: str, outcome: str, detail: dict, client: str = CHANNEL) -> None:
    cur.execute("INSERT INTO audit_log (tool, outcome, client, detail) VALUES (%s, %s, %s, %s)", (tool, outcome, client, Jsonb(detail)))


def _context(cur, now: datetime) -> tuple[list, list]:
    """The parser's context: the last few messages of the 6 hours before `now`, and what was eaten and drunk that day.
    `now` is a kept message's own time when it is read again later, so later messages are left out."""
    cur.execute("""SELECT received_at, content FROM raw_inbox WHERE received_at > %s - interval '6 hours' AND received_at <= %s
                   AND channel <> 'catalogue' AND content NOT LIKE 'add food: %%' AND NOT (metadata ? 'lab_report') AND NOT (metadata ? 'gym')
                   ORDER BY received_at DESC LIMIT 4""", (now, now))  # the logger's own catalogue rows are not messages
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
            "needs_choice": "Nothing saved yet: a question is open.", "kept": "Kept as text.",
            "not_logged": "Not logged: this reads as a question or command for you, so answer the user yourself.",
            "needs_confirmation": "Nothing saved yet: please confirm.", "rejected": "Nothing saved.",
            "cancelled": "Skipped.", "replayed": "Already saved earlier (same message).",
            "answered": "Found.", "changed": "Changed.",
            "held": "Not read yet, but kept: the logger has the words and reads them again by itself. Do not send this "
                    "message again; tell the user in one line."}.get(o, o)
    if o == "rejected" and out.get("steps"):  # a command that matched nothing
        head = "Nothing changed."
    if o == "not_logged" and out.get("intent") == "command":
        head = "Nothing done: this reads as a request the logger cannot handle, so answer the user yourself."
    lines = [head] + out.get("receipt", []) + [f"note: {n}" for n in out.get("notes", [])]
    if out.get("result"):
        lines.append("Look-up result (answer the user from it, briefly; do not repeat it all):\n" + out["result"])
    if out.get("command"):
        lines.append(_message({**out["command"], "questions": []}))
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


def log(text: str, idempotency_key: Optional[str] = None, pred: Optional[dict] = None, channel: str = CHANNEL) -> dict[str, Any]:
    """pred: a ready parser answer (the web app's quick buttons); the AI then does not read the text and the message is
    saved at once. Without one, the words are kept first (held.py) and then read and saved by read_held, so an AI or save
    failure cannot lose them (01.11.2026).
    channel: where the message came from (CHANNEL or WEB), stored with it and in the audit log."""
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
                    _audit(cur, "log", "rejected", {"why": "key reused"}, channel)
                    return {"outcome": "rejected", "message": "This idempotency key was already used for a different message."}
                _audit(cur, "log", "replayed", {}, channel)
                return {**row["response"], "replayed": True}
        hid = held.find(cur, idempotency_key) if pred is None else None  # sent again while it waits: read the kept one
        if hid is None:
            cur.execute("""SELECT received_at FROM raw_inbox WHERE channel = ANY(%s) AND lower(content) = lower(%s)
                           AND received_at > %s - interval '30 minutes' AND NOT (metadata ? 'unprocessed')
                           ORDER BY received_at DESC LIMIT 1""", (LIVE_CHANNELS, text, now))
            same = cur.fetchone()
            waiting = held.waiting_same(cur, text, now) if pred is None and not same else None
            if same or waiting:  # the same words again within 30 minutes: ask before saving twice
                pid = "p" + secrets.token_hex(4)
                at = (same["received_at"] if same else waiting).astimezone(BERLIN).strftime("%H:%M")
                why = f"already logged at {at}" if same else f"already waiting to be read (sent at {at})"
                cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'log_repeat', %s, %s, %s)",
                            (pid, Jsonb({"text": text, "now": now.isoformat()}), f"same message {why}",
                             now + timedelta(hours=record.QUESTION_HOURS)))
                resp = _public({"outcome": "needs_confirmation", "reason": f"This exact message was {why}.",
                                "questions": [{"pending_id": pid, "question": "Log it again (yes) or not (no)?"}]})
                _audit(cur, "log", "needs_confirmation", {"why": "repeat", "questions": [pid]}, channel)
                return resp
            if pred is not None:
                out = _process(cur, text, now, pred, channel)
                resp = _public(out)
                if idempotency_key:
                    cur.execute("INSERT INTO idempotency_keys (key, tool, request_hash, response) VALUES (%s, 'log', %s, %s)",
                                (idempotency_key, req_hash, Jsonb(resp)))
                return resp
            hid = held.keep(cur, text, channel, idempotency_key, now)
    return read_held(hid)


def read_held(hid: int, background: bool = False) -> dict[str, Any]:
    """Read a kept message with the AI and save it (core items 1 and 2). The AI reads with no transaction or connection
    open; the save, its audit row, the idempotency key and the removal of the kept words are one transaction. Whatever
    fails, the words stay kept, "not read yet", for the next try. A message read again later keeps its own time.
    background: the cron job's try, with nobody watching the reply: a message the logger refuses or will not log stays
    kept (marked "needs you", no more automatic tries) instead of being dropped. Any other read is live: the AI gets the
    shorter wait (provider.LIVE)."""
    token = LIVE.set(not background)
    try:
        return _read_held(hid, background)
    finally:
        LIVE.reset(token)


def _read_held(hid: int, background: bool) -> dict[str, Any]:
    started = time.monotonic()
    row = held.claim(hid)
    if row is None:
        return _public({"outcome": "rejected", "reason": "That message is no longer waiting: it was saved or deleted."})
    if row == "busy":
        return _public({"outcome": "held", "held_id": hid, "reason": "It is being read right now."})
    text, channel, key = row["content"], row["channel"], row["idempotency_key"]
    now = row["received_at"].astimezone(BERLIN)
    try:
        with connect() as conn, conn.cursor() as cur:
            prev, meals = _context(cur, now)
        preds, usages = _read(text, now, prev, meals)
        with connect() as conn, conn.cursor() as cur:
            if key:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
            sent = todos.retry(cur, provider(), now)  # to-dos that waited because Todoist was down
            r = record.best(cur, preds, now)
            out = _save(cur, text, now, r, usages + sent["usages"], len(preds) > 1, channel, prev, sent, row["tries"], started)
            if background and out["outcome"] in ("rejected", "not_logged"):  # nothing was saved: keep the words for the owner
                why = out.get("reason") or ("it reads as a question or a request, not an entry" if out["outcome"] == "not_logged" else "")
                held.needs_you(cur, hid, why)
                return _public({**out, "held_id": hid})
            resp = _public(out)
            if key:
                cur.execute("""INSERT INTO idempotency_keys (key, tool, request_hash, response) VALUES (%s, 'log', %s, %s)
                               ON CONFLICT (key) DO NOTHING""", (key, hashlib.sha256(text.encode()).hexdigest(), Jsonb(resp)))
            asked = [q["question_id"] for q in resp.get("questions", [])]
            if asked:  # read again days later: its questions still get their full time to be answered
                cur.execute("UPDATE pending SET expires_at = greatest(expires_at, now() + make_interval(hours => %s)) WHERE id = ANY(%s)",
                            (record.QUESTION_HOURS, asked))
            held.done(cur, hid)
        return resp
    except Exception as e:  # noqa: BLE001 - whatever failed, the words stay kept for the next try
        kind = type(e).__name__
        _LOG.warning("message %s not read yet: %s", hid, kind)  # the kind only, never the words
        try:
            with connect() as conn, conn.cursor() as cur:
                held.failed(cur, hid, kind)
                _audit(cur, "log", "held", {"held_id": hid, "error": kind, "tries": row["tries"]}, channel)
        except Exception:  # noqa: BLE001 - the database is down too: the 10-minute lease frees the message by itself
            pass
        reason = ("The AI service did not answer." if isinstance(e, ProviderError) or "anthropic" in type(e).__module__
                  else "Something went wrong while saving it.")
        return _public({"outcome": "held", "held_id": hid,
                        "reason": f"{reason} Your words are kept: the logger tries again by itself every 5 minutes for "
                                  f"{held.AUTO_HOURS} hours, and Today has a Try again button."})


def _read(text: str, now: datetime, prev: list, meals: list) -> tuple[list[dict], list]:
    """The AI's reading, with no transaction open: the small model, and the larger model's re-read when the first reading
    leaves a question (checked in a short look at the catalogue). -> (parser answers, usages)."""
    naive = now.replace(tzinfo=None)
    pred, u = parser.parse(provider(), text, naive, prev, meals)
    with connect() as conn, conn.cursor() as cur:
        unsettled = bool(record.best(cur, [pred], now).questions)
    if not unsettled:
        return [pred], [u]
    pred2, u2 = parser.parse(provider(), text, naive, prev, meals, model=record.ESCALATION_MODEL)
    return [pred, pred2], [u, u2]


def _process(cur, text: str, now: datetime, pred: Optional[dict] = None, channel: str = CHANNEL) -> dict:
    """Read, save and audit one message in the caller's transaction (a quick button's ready answer, or a repeated message
    the user confirmed); the caller makes it public."""
    started = time.monotonic()
    prev, meals = _context(cur, now)
    sent = todos.retry(cur, provider(), now)  # to-dos that waited because Todoist was down
    r, usages, escalated = record.read(cur, provider(), text, now, prev, meals, pred=pred)
    return _save(cur, text, now, r, usages + sent["usages"], escalated, channel, prev, sent, started=started)


def _save(cur, text: str, now: datetime, r: record.Reading, usages: list, escalated: bool, channel: str, prev: list,
          sent: dict, tries: int = 0, started: Optional[float] = None) -> dict:
    """Save one read message with everything that follows from it (to-dos, look-ups, changes) and its audit row.
    started: when the work on it began (time.monotonic), so the audit row shows how long the message took ("secs")."""
    out = record.save(cur, r, text, channel, now)
    if r.pred.get("todos") and out.get("raw_id"):  # to-dos and shopping go to Todoist (v1's rules, in todos.py)
        t = todos.apply(cur, provider(), r.pred["todos"], out["raw_id"], now, text)
        usages += t["usages"]
        out["receipt"] = out.get("receipt", []) + t["receipt"]
        out["notes"] = out.get("notes", []) + t["notes"]
        asked = record._ask(cur, out["raw_id"], t["ask"], now)
        out["questions"] = out.get("questions", []) + asked
        if out["outcome"] in ("kept", "needs_choice"):
            out["outcome"] = "partly_saved" if t["receipt"] and asked else "saved" if t["receipt"] else "needs_choice" if asked else out["outcome"]
    usages += lookup.offer(cur, provider(), out, text)  # a food nothing in the catalogue fits: USDA or Open Food Facts
    if sent["receipt"] or sent["notes"]:
        out["notes"] = out.get("notes", []) + sent["receipt"] + sent["notes"]
    if out["outcome"] == "needs_confirmation":  # v1's read-back: keep the reading, the user's yes saves it
        pid = "p" + secrets.token_hex(4)
        cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'log_confirm', %s, %s, %s)",
                    (pid, Jsonb({"text": text, "pred": r.pred, "now": now.isoformat()}), out["read_back"][:500],
                     now + timedelta(hours=record.QUESTION_HOURS)))
        out["questions"] = [{"pending_id": pid, "question": "Confirm this (yes), or correct it by logging again."}]
    if out["outcome"] == "not_logged" and out.get("intent") == "command":  # a look-up or a change
        out, used = commands.handle(cur, provider(), r.pred.get("command_text") or text, now, prev)
        usages += used
    elif r.pred.get("command_text") and out["outcome"] in ("saved", "partly_saved", "kept"):  # mixed: new entries + a change
        sub, used = commands.handle(cur, provider(), r.pred["command_text"], now, prev)
        usages += used
        out["command"] = sub
        out["questions"] = out.get("questions", []) + sub.get("questions", [])
    _audit(cur, "log", out["outcome"], {"raw_id": out.get("raw_id"), "private_id": out.get("private_id"),
                                        "questions": [q["pending_id"] for q in out.get("questions", [])], "escalated": escalated,
                                        "escalation": r.escalation, **({"tries": tries} if tries > 1 else {}),
                                        **({"secs": round(time.monotonic() - started, 2)} if started is not None else {}),
                                        "steps": out.get("steps") or out.get("command", {}).get("steps"),
                                        # "in" is all input tokens; the cached share is kept apart to show whether prompt caching is used
                                        "ai": [{"model": u.model, "in": u.input_tokens + u.cache_read_tokens + u.cache_write_tokens,
                                                "out": u.output_tokens, "cache_read": u.cache_read_tokens, "cache_write": u.cache_write_tokens,
                                                "usd": round(u.usd, 6), "s": round(u.seconds, 2)} for u in usages]}, channel)
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


def food_lookup(barcode: Optional[str] = None, name: Optional[str] = None, question_id: Optional[str] = None) -> dict[str, Any]:
    """A food looked up by barcode (Open Food Facts) or name (USDA and Open Food Facts); shown for the user's yes."""
    now = clock()
    with connect() as conn, conn.cursor() as cur:
        word = None
        if question_id:
            cur.execute("SELECT payload FROM pending WHERE id = %s AND tool = 'log' AND resolved_at IS NULL", (question_id,))
            q = cur.fetchone()
            if not q or q["payload"].get("kind") != "food":
                return _public({"outcome": "rejected", "reason": "there is no open food question with that id"})
            word = q["payload"]["item"]["food"]
        try:
            if barcode:
                cand, usages, why = lookup.by_barcode(cur, provider(), barcode)
            elif name:
                cand, usages, notes = lookup.by_name(cur, provider(), name, name, name)
                why = "nothing fitting found in USDA or Open Food Facts: send a label photo, or add it as an estimate"
            else:
                cand, usages, why = None, [], "give a barcode or a name"
        except lookup.FoodFactsError as e:
            cand, usages, why = None, [], f"{e}; try again later or send a label photo"
        if not cand:
            out = {"outcome": "rejected", "reason": why}
        elif cand.get("existing"):
            out = lookup.accept(cur, cand, now, question_id, word)
        else:
            pid = "p" + secrets.token_hex(4)
            cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'lookup_confirm', %s, %s, %s)",
                        (pid, Jsonb({"cand": cand, "question_id": question_id, "word": word, "now": now.isoformat()}),
                         cand["offer"][:500], now + timedelta(hours=record.QUESTION_HOURS)))
            out = {"outcome": "needs_confirmation", "read_back": cand["offer"] + ".",
                   "questions": [{"pending_id": pid, "question": "Show the user these values; add it (yes) or not (no)?"}]}
        resp = _public(out)
        _audit(cur, "food_lookup", out["outcome"], {"question_id": question_id, "barcode": bool(barcode),
                                                    "ai": [{"model": u.model, "usd": round(u.usd, 6)} for u in usages]})
        return resp


def lab_report(text: str, corrections: Optional[str] = None, source: str = "image") -> dict[str, Any]:
    """A report's text, structured and read back (labs.py); saved only after the user's yes."""
    now = clock()
    with connect() as conn, conn.cursor() as cur:
        usages = []
        try:
            rb, usages = labs.read_back(cur, provider(), text, now, source, corrections or "")
        except Reject as e:
            out = {"outcome": "rejected", "reason": str(e)}
        except ProviderError as e:
            out = {"outcome": "rejected", "reason": f"the report could not be read ({e}); try again"}
        else:
            pid = "p" + secrets.token_hex(4)
            cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s, 'report_confirm', %s, %s, %s)",
                        (pid, Jsonb({"report": rb["report"], "code": rb["code"], "text": text, "now": now.isoformat()}),
                         "lab report read-back", now + timedelta(hours=record.QUESTION_HOURS)))
            out = {"outcome": "needs_confirmation", "read_back": rb["read_back"],
                   "questions": [{"pending_id": pid, "question": "Is the whole table right (yes), or what is wrong?"}]}
        resp = _public(out)
        _audit(cur, "lab_report", out["outcome"], {"ai": [{"model": u.model, "usd": round(u.usd, 6)} for u in usages]})  # no values
        return resp


def food_words(food_id: int, words: list, means: bool = False) -> dict[str, Any]:
    with connect() as conn, conn.cursor() as cur:
        out = record.add_words(cur, food_id, words, means)
        resp = _public(out)
        _audit(cur, "food_words", out["outcome"], {"food_id": food_id, "means": means})
        return resp


def answer(question_id: str, reply: str, channel: str = CHANNEL) -> dict[str, Any]:
    now = clock()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM pending WHERE id = %s FOR UPDATE", (question_id,))
        p = cur.fetchone()
        if not p or p["tool"] not in ANSWER_TOOLS:
            _audit(cur, "answer", "rejected", {"why": "unknown question"}, channel)
            return {"outcome": "rejected", "message": "There is no such open question."}
        yes = bool(YES.match(reply))
        if p["tool"] == "log" and p["payload"].get("kind") != "todo" and not (p["payload"].get("lookup") and yes):
            out = record.answer(cur, question_id, reply, now)
        elif p["resolved_at"] is not None or p["expires_at"] < now:
            out = {"outcome": "rejected", "reason": "that question was already answered or has expired"}
        elif p["tool"] == "log" and p["payload"].get("lookup"):  # yes to a food found in USDA or Open Food Facts
            out = lookup.accept(cur, p["payload"]["lookup"], now, question_id, p["payload"]["item"]["food"])
        elif p["tool"] == "log":  # which shopping item was meant
            out = todos.answer(cur, provider(), p, reply, now)
        elif p["tool"] == "command":  # which entry was meant: read the request again with the answer
            prev, _ = _context(cur, now)
            out, _ = commands.handle(cur, provider(), f"{p['payload']['text']}\n(Asked: {p['reason']} Answer: {reply})", now, prev)
            cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        elif p["tool"] == "command_confirm" and p["payload"].get("ask_past") and (yes or re.match(r"^\s*(no|nein)\b", reply, re.I)):
            out = commands.apply(cur, p["payload"], now, past="yes" if yes else "no")
            if out["outcome"] == "changed":
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        elif NO.match(reply):
            cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'cancelled' WHERE id = %s", (question_id,))
            out = {"outcome": "cancelled"}
        elif not yes:  # unclear: keep the question open
            out = {"outcome": "needs_confirmation", "reason": "answer yes to save it, or no to drop it",
                   "questions": [{"pending_id": question_id, "question": "Yes or no?"}]}
        elif p["tool"] == "report_confirm":
            out = labs.save_report(cur, p["payload"], now)
            if out["outcome"] == "saved":
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        elif p["tool"] == "lookup_confirm":
            d = p["payload"]
            out = lookup.accept(cur, d["cand"], now, d.get("question_id"), d.get("word"))
            if out["outcome"] in ("saved", "partly_saved"):
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        elif p["tool"] == "command_confirm":
            out = commands.apply(cur, p["payload"], now)
            if out["outcome"] == "changed":
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        elif p["tool"] == "log_repeat":
            out = _process(cur, p["payload"]["text"], now, None, channel)
            cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        elif p["tool"] == "add_food_confirm":
            d = p["payload"]
            out = record.add_food(cur, d["name"], d["values"], channel, now, d["question_id"], food_code=d["code"],
                                  confirmed=True, aliases=d.get("aliases") or [])
            if out["outcome"] == "saved":
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        else:
            d = p["payload"]
            asked = datetime.fromisoformat(d["now"])
            r = record.settle(cur, _food_cat(cur), _types(cur)[1], d["pred"], asked)
            out = record.save(cur, r, d["text"], channel, asked, confirmed=True)
            if out["outcome"] in ("saved", "partly_saved"):
                cur.execute("UPDATE pending SET resolved_at = now(), resolution = 'confirmed' WHERE id = %s", (question_id,))
        if "question" in out and "questions" not in out:  # a follow-up question about the same item
            out["questions"] = [{"pending_id": question_id, "question": out.pop("question")}]
        resp = _public(out)
        _audit(cur, "answer", out["outcome"], {"question_id": question_id, "raw_id": out.get("raw_id"), "steps": out.get("steps")}, channel)
        return resp
