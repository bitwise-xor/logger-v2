"""The core's operations for the pilot: weight. Every write returns exactly one outcome:
saved | needs_confirmation | rejected. Anything but saved carries what the client needs to ask the human."""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from psycopg.types.json import Jsonb

from . import rules
from .db import connect

EXTRACTOR = "v2-pilot-1"
PENDING_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(cur, tool: str, outcome: str, detail: Optional[dict] = None) -> None:
    cur.execute("INSERT INTO audit_log (tool, outcome, client, detail) VALUES (%s,%s,'mcp',%s)", (tool, outcome, Jsonb(detail or {})))


def _num(x) -> str:
    return ("%.2f" % float(x)).rstrip("0").rstrip(".")


def _weight_type(cur) -> dict:
    cur.execute("SELECT id, min1, max1 FROM log_types WHERE name='weight'")
    return cur.fetchone()


def _reasons(cur, wt: dict, kg: float, at: datetime, known: bool, now: datetime) -> list[str]:
    out: list[str] = []
    if not (float(wt["min1"]) <= kg <= float(wt["max1"])):
        out.append(f"{_num(kg)} kg is outside the plausible range {_num(wt['min1'])} to {_num(wt['max1'])} kg")
    if rules.is_future(at, now):
        out.append(f"the time {rules.fmt(at)} is in the future")
    cur.execute("SELECT log_day(%s) AS d", (at,))
    day = cur.fetchone()["d"]
    cur.execute("SELECT value1, event_at, time_known FROM measurements WHERE type_id=%s AND day=%s ORDER BY event_at", (wt["id"], day))
    for r in cur.fetchall():
        out.append(f"there is already a weight for {day.strftime('%d.%m.')}: {_num(r['value1'])} kg ({rules.fmt(r['event_at'], r['time_known'])})")
    return out


def _save(cur, wt: dict, kg: float, at: datetime, known: bool, text: Optional[str]) -> dict:
    content = text.strip() if text and text.strip() else f"weight {_num(kg)} kg"
    cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('mcp', %s, %s) RETURNING id",
                (content, Jsonb({"tool": "record_weight", "synthesized": not (text and text.strip())})))
    raw_id = cur.fetchone()["id"]
    cur.execute("SELECT log_day(%s) AS d", (at,))
    day = cur.fetchone()["d"]
    cur.execute("""INSERT INTO measurements (raw_id, type_id, day, event_at, time_known, value1, extractor_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (raw_id, wt["id"], day, at, known, kg, EXTRACTOR))
    mid = cur.fetchone()["id"]
    receipt = {"id": f"m{mid}", "type": "weight", "value": kg, "unit": "kg", "day": day.isoformat(),
               "at": rules.fmt(at, known), "time_known": known}
    return {"outcome": "saved", "receipt": receipt, "message": f"Saved weight {_num(kg)} kg, {rules.fmt(at, known)} (m{mid})."}


def record_weight(kg: float, at: Optional[str] = None, text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    now = _now()
    try:
        kg = rules.check_number(kg, "kg")
        moment, known = rules.parse_at(at, now)
    except rules.BadInput as e:
        with connect() as conn, conn.cursor() as cur:
            _audit(cur, "record_weight", "rejected", {"why": "bad input"})
        return {"outcome": "rejected", "message": str(e)}
    req_hash = hashlib.sha256(json.dumps([kg, at, text], sort_keys=True).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        if idempotency_key:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (idempotency_key,))   # serialise same-key calls
            cur.execute("SELECT request_hash, response FROM idempotency_keys WHERE key=%s", (idempotency_key,))
            row = cur.fetchone()
            if row:
                if row["request_hash"] != req_hash:
                    _audit(cur, "record_weight", "rejected", {"why": "key reused"})
                    return {"outcome": "rejected", "message": "This idempotency key was already used for a different request."}
                _audit(cur, "record_weight", "replayed", {})
                return {**row["response"], "replayed": True}
        wt = _weight_type(cur)
        reasons = _reasons(cur, wt, kg, moment, known, now)
        if reasons:
            pid = "p" + secrets.token_hex(4)
            cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s,'record_weight',%s,%s,%s)",
                        (pid, Jsonb({"kg": kg, "at": moment.isoformat(), "known": known, "text": text}), "; ".join(reasons),
                         now + timedelta(hours=PENDING_HOURS)))
            resp = {"outcome": "needs_confirmation", "pending_id": pid, "reasons": reasons,
                    "message": "Not saved yet: " + "; ".join(reasons) + ". Ask the user, then call resolve with this pending_id (confirm true or false)."}
            detail = {"pending_id": pid}
        else:
            resp = _save(cur, wt, kg, moment, known, text)
            detail = {"receipt": resp["receipt"]["id"]}
        if idempotency_key:
            cur.execute("INSERT INTO idempotency_keys (key, tool, request_hash, response) VALUES (%s,'record_weight',%s,%s)",
                        (idempotency_key, req_hash, Jsonb(resp)))
        _audit(cur, "record_weight", resp["outcome"], detail)
        return resp


def resolve(pending_id: str, confirm: bool) -> dict[str, Any]:
    now = _now()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM pending WHERE id=%s FOR UPDATE", (pending_id,))
        p = cur.fetchone()
        if not p or p["resolved_at"] is not None or p["expires_at"] < now:
            _audit(cur, "resolve", "rejected", {"why": "unknown, resolved or expired"})
            return {"outcome": "rejected", "message": "That question does not exist, was already answered, or has expired. Log the entry again."}
        if not confirm:
            cur.execute("UPDATE pending SET resolved_at=now(), resolution='cancelled' WHERE id=%s", (pending_id,))
            _audit(cur, "resolve", "cancelled", {"pending_id": pending_id})
            return {"outcome": "cancelled", "message": "Cancelled. Nothing was saved."}
        d = p["payload"]
        at = datetime.fromisoformat(d["at"])
        resp = _save(cur, _weight_type(cur), float(d["kg"]), at, bool(d["known"]), d.get("text"))
        cur.execute("UPDATE pending SET resolved_at=now(), resolution='confirmed' WHERE id=%s", (pending_id,))
        _audit(cur, "resolve", "saved", {"pending_id": pending_id, "receipt": resp["receipt"]["id"]})
        return resp


def recent_weights(days: int = 14) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        wt = _weight_type(cur)
        cur.execute("""SELECT id, value1, event_at, time_known, day FROM measurements
                       WHERE type_id=%s AND day >= %s - %s ORDER BY event_at DESC""", (wt["id"], today, days))
        rows = [{"id": f"m{r['id']}", "kg": float(r["value1"]), "at": rules.fmt(r["event_at"], r["time_known"]), "day": r["day"].isoformat()}
                for r in cur.fetchall()]
        _audit(cur, "recent_weights", "ok", {"count": len(rows)})
    return {"outcome": "ok", "weights": rows, "message": f"{len(rows)} weight(s) in the last {days} days."}


def undo(receipt_id: str) -> dict[str, Any]:
    if not (receipt_id.startswith("m") and receipt_id[1:].isdigit()):
        return {"outcome": "rejected", "message": "A receipt id looks like m123."}
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM measurements WHERE id=%s AND extractor_version=%s RETURNING id", (int(receipt_id[1:]), EXTRACTOR))
        if not cur.fetchone():
            _audit(cur, "undo", "rejected", {"receipt": receipt_id})
            return {"outcome": "rejected", "message": f"{receipt_id} was not found, or was not logged by this service."}
        _audit(cur, "undo", "saved", {"receipt": receipt_id})
        return {"outcome": "saved", "message": f"Removed {receipt_id}. The original message stays in the log."}
