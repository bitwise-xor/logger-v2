"""The core's operations for the pilot: weight, blood pressure, wake-up, amend. Every write returns exactly one outcome:
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


# One entry per kind of log. A wake-up is an event; weight and blood pressure are measurements.
KINDS = {
    "weight": {"tool": "record_weight", "table": "measurements", "prefix": "m", "label": "weight"},
    "bp": {"tool": "record_bp", "table": "measurements", "prefix": "m", "label": "blood pressure"},
    "wake": {"tool": "record_wakeup", "table": "events", "prefix": "e", "label": "wake-up"},
}
TYPE_NAME = {"weight": "weight", "bp": "bp", "wake": "wake"}
PERIODS = ("morning", "evening")


def _type(cur, kind: str) -> dict:
    cur.execute("SELECT id, min1, max1, min2, max2 FROM log_types WHERE name=%s", (TYPE_NAME[kind],))
    return cur.fetchone()


def _day(cur, kind: str, at: datetime):
    """Health day: local time minus 5 hours. A wake-up belongs to its own calendar date."""
    if kind == "wake":
        cur.execute("SELECT (%s AT TIME ZONE 'Europe/Berlin')::date AS d", (at,))
    else:
        cur.execute("SELECT log_day(%s) AS d", (at,))
    return cur.fetchone()["d"]


def _bp_text(v: dict) -> str:
    return f"{_num(v['value1'])}/{_num(v['value2'])} mmHg" + (f" ({v['period']})" if v.get("period") else "")


def _reasons(cur, kind: str, t: dict, vals: dict, at: datetime, known: bool, now: datetime, exclude: Optional[int] = None) -> list[str]:
    out: list[str] = []
    if kind == "weight":
        if not (float(t["min1"]) <= vals["value1"] <= float(t["max1"])):
            out.append(f"{_num(vals['value1'])} kg is outside the plausible range {_num(t['min1'])} to {_num(t['max1'])} kg")
    elif kind == "bp":
        if not (float(t["min1"]) <= vals["value1"] <= float(t["max1"])):
            out.append(f"systolic {_num(vals['value1'])} is outside the plausible range {_num(t['min1'])} to {_num(t['max1'])}")
        if not (float(t["min2"]) <= vals["value2"] <= float(t["max2"])):
            out.append(f"diastolic {_num(vals['value2'])} is outside the plausible range {_num(t['min2'])} to {_num(t['max2'])}")
        if vals["value1"] <= vals["value2"]:
            out.append("systolic is not higher than diastolic (the two numbers may be swapped)")
    if rules.is_future(at, now):
        out.append(f"the time {rules.fmt(at)} is in the future")
    day = _day(cur, kind, at)
    table = KINDS[kind]["table"]
    cols = "value1, value2, period, event_at, time_known" if table == "measurements" else "event_at, time_known"
    cur.execute(f"SELECT {cols} FROM {table} WHERE type_id=%s AND day=%s AND id IS DISTINCT FROM %s ORDER BY event_at", (t["id"], day, exclude))
    for r in cur.fetchall():
        when = rules.fmt(r["event_at"], r["time_known"])
        if kind == "weight":
            out.append(f"there is already a weight for {day.strftime('%d.%m.')}: {_num(r['value1'])} kg ({when})")
        elif kind == "bp" and vals.get("period") and r["period"] == vals["period"]:
            out.append(f"there is already a {vals['period']} blood pressure for {day.strftime('%d.%m.')}: {_bp_text(r)} ({when})")
        elif kind == "wake":
            out.append(f"there is already a wake-up for {day.strftime('%d.%m.')} ({when})")
    return out


def _receipt(kind: str, rid: int, vals: dict, day, at: datetime, known: bool) -> dict:
    base = {"id": f"{KINDS[kind]['prefix']}{rid}", "type": kind, "day": day.isoformat(), "at": rules.fmt(at, known), "time_known": known}
    if kind == "weight":
        return {**base, "value": vals["value1"], "unit": "kg"}
    if kind == "bp":
        return {**base, "value": f"{_num(vals['value1'])}/{_num(vals['value2'])}", "systolic": vals["value1"], "diastolic": vals["value2"],
                "unit": "mmHg", "period": vals.get("period")}
    return base


def _summary(kind: str, vals: dict, at: datetime, known: bool) -> str:
    if kind == "weight":
        return f"weight {_num(vals['value1'])} kg, {rules.fmt(at, known)}"
    if kind == "bp":
        return f"blood pressure {_bp_text(vals)}, {rules.fmt(at, known)}"
    return f"wake-up, {rules.fmt(at, known)}"


def _save(cur, kind: str, vals: dict, at: datetime, known: bool, text: Optional[str]) -> dict:
    t = _type(cur, kind)
    tool = KINDS[kind]["tool"]
    synthesized = not (text and text.strip())
    content = text.strip() if not synthesized else _summary(kind, vals, at, known)
    cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES ('mcp', %s, %s) RETURNING id",
                (content, Jsonb({"tool": tool, "synthesized": synthesized})))
    raw_id = cur.fetchone()["id"]
    day = _day(cur, kind, at)
    if KINDS[kind]["table"] == "measurements":
        cur.execute("""INSERT INTO measurements (raw_id, type_id, day, event_at, time_known, value1, value2, period, extractor_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (raw_id, t["id"], day, at, known, vals["value1"], vals.get("value2"), vals.get("period"), EXTRACTOR))
    else:
        cur.execute("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""", (raw_id, t["id"], day, at, known, EXTRACTOR))
    rid = cur.fetchone()["id"]
    receipt = _receipt(kind, rid, vals, day, at, known)
    return {"outcome": "saved", "receipt": receipt, "message": f"Saved {_summary(kind, vals, at, known)} ({receipt['id']})."}


def _ask(cur, tool: str, payload: dict, reasons: list[str], now: datetime) -> dict:
    pid = "p" + secrets.token_hex(4)
    cur.execute("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES (%s,%s,%s,%s,%s)",
                (pid, tool, Jsonb(payload), "; ".join(reasons), now + timedelta(hours=PENDING_HOURS)))
    return {"outcome": "needs_confirmation", "pending_id": pid, "reasons": reasons,
            "message": "Not saved yet: " + "; ".join(reasons) + ". Ask the user, then call resolve with this pending_id (confirm true or false)."}


def _record(kind: str, vals: dict, at: Optional[str], text: Optional[str], idempotency_key: Optional[str]) -> dict[str, Any]:
    tool = KINDS[kind]["tool"]
    now = _now()
    try:
        moment, known = rules.parse_at(at, now)
        if kind == "wake" and (at is None or not at.strip()):
            known = True   # "up" without a time means now
    except rules.BadInput as e:
        with connect() as conn, conn.cursor() as cur:
            _audit(cur, tool, "rejected", {"why": "bad input"})
        return {"outcome": "rejected", "message": str(e)}
    req_hash = hashlib.sha256(json.dumps([kind, vals, at, text], sort_keys=True).encode()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        if idempotency_key:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (idempotency_key,))   # serialise same-key calls
            cur.execute("SELECT request_hash, response FROM idempotency_keys WHERE key=%s", (idempotency_key,))
            row = cur.fetchone()
            if row:
                if row["request_hash"] != req_hash:
                    _audit(cur, tool, "rejected", {"why": "key reused"})
                    return {"outcome": "rejected", "message": "This idempotency key was already used for a different request."}
                _audit(cur, tool, "replayed", {})
                return {**row["response"], "replayed": True}
        reasons = _reasons(cur, kind, _type(cur, kind), vals, moment, known, now)
        if reasons:
            resp = _ask(cur, tool, {"kind": kind, "vals": vals, "at": moment.isoformat(), "known": known, "text": text}, reasons, now)
            detail = {"pending_id": resp["pending_id"]}
        else:
            resp = _save(cur, kind, vals, moment, known, text)
            detail = {"receipt": resp["receipt"]["id"]}
        if idempotency_key:
            cur.execute("INSERT INTO idempotency_keys (key, tool, request_hash, response) VALUES (%s,%s,%s,%s)",
                        (idempotency_key, tool, req_hash, Jsonb(resp)))
        _audit(cur, tool, resp["outcome"], detail)
        return resp


def _rejected(tool: str, e: Exception) -> dict:
    with connect() as conn, conn.cursor() as cur:
        _audit(cur, tool, "rejected", {"why": "bad input"})
    return {"outcome": "rejected", "message": str(e)}


def _period(period: Optional[str]) -> Optional[str]:
    if period is None:
        return None
    p = period.strip().lower()
    if p not in PERIODS:
        raise rules.BadInput("period must be morning or evening")
    return p


def record_weight(kg: float, at: Optional[str] = None, text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    try:
        kg = rules.check_number(kg, "kg")
    except rules.BadInput as e:
        return _rejected("record_weight", e)
    return _record("weight", {"value1": kg}, at, text, idempotency_key)


def record_bp(systolic: float, diastolic: float, period: Optional[str] = None, at: Optional[str] = None,
              text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    try:
        vals = {"value1": rules.check_number(systolic, "systolic"), "value2": rules.check_number(diastolic, "diastolic")}
        per = _period(period)
        if per:
            vals["period"] = per
    except rules.BadInput as e:
        return _rejected("record_bp", e)
    return _record("bp", vals, at, text, idempotency_key)


def record_wakeup(at: Optional[str] = None, text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    return _record("wake", {}, at, text, idempotency_key)


def _payload_kind(p: dict) -> tuple[str, dict]:
    d = p["payload"]
    if "kind" in d:
        return d["kind"], d["vals"]
    return "weight", {"value1": float(d["kg"])}   # pending questions from the first pilot version


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
        if p["tool"] == "amend":
            resp = _apply_amend(cur, d["receipt"], d["changes"])
        else:
            kind, vals = _payload_kind(p)
            resp = _save(cur, kind, vals, datetime.fromisoformat(d["at"]), bool(d["known"]), d.get("text"))
        if resp["outcome"] != "saved":
            _audit(cur, "resolve", "rejected", {"pending_id": pending_id})
            return resp
        cur.execute("UPDATE pending SET resolved_at=now(), resolution='confirmed' WHERE id=%s", (pending_id,))
        _audit(cur, "resolve", "saved", {"pending_id": pending_id, "receipt": resp["receipt"]["id"]})
        return resp


def _recent(kind: str, days: int) -> list[dict]:
    days = max(1, min(int(days), 365))
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT log_day(now()) AS d")
        today = cur.fetchone()["d"]
        table = KINDS[kind]["table"]
        cols = "id, value1, value2, period, event_at, time_known, day" if table == "measurements" else "id, event_at, time_known, day"
        cur.execute(f"SELECT {cols} FROM {table} WHERE type_id=%s AND day >= %s - %s ORDER BY event_at DESC", (_type(cur, kind)["id"], today, days))
        rows = []
        for r in cur.fetchall():
            rec = _receipt(kind, r["id"], {"value1": float(r["value1"]) if r.get("value1") is not None else None,
                                          "value2": float(r["value2"]) if r.get("value2") is not None else None,
                                          "period": r.get("period")}, r["day"], r["event_at"], r["time_known"])
            rows.append(rec)
        _audit(cur, f"recent_{kind}", "ok", {"count": len(rows)})
    return rows


def recent_weights(days: int = 14) -> dict[str, Any]:
    rows = [{"id": r["id"], "kg": r["value"], "at": r["at"], "day": r["day"]} for r in _recent("weight", days)]
    return {"outcome": "ok", "weights": rows, "message": f"{len(rows)} weight(s) in the last {max(1, min(int(days), 365))} days."}


def recent_bp(days: int = 14) -> dict[str, Any]:
    rows = [{k: r[k] for k in ("id", "value", "period", "at", "day")} for r in _recent("bp", days)]
    return {"outcome": "ok", "blood_pressures": rows, "message": f"{len(rows)} blood pressure reading(s) in the last {max(1, min(int(days), 365))} days."}


def recent_wakeups(days: int = 14) -> dict[str, Any]:
    rows = [{k: r[k] for k in ("id", "at", "day")} for r in _recent("wake", days)]
    return {"outcome": "ok", "wakeups": rows, "message": f"{len(rows)} wake-up(s) in the last {max(1, min(int(days), 365))} days."}


def _parse_receipt(receipt_id: str) -> Optional[tuple[str, int]]:
    if len(receipt_id) > 1 and receipt_id[0] in "me" and receipt_id[1:].isdigit():
        return receipt_id[0], int(receipt_id[1:])
    return None


def undo(receipt_id: str) -> dict[str, Any]:
    pr = _parse_receipt(receipt_id)
    if not pr:
        return {"outcome": "rejected", "message": "A receipt id looks like m123 or e45."}
    table = "measurements" if pr[0] == "m" else "events"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE id=%s AND extractor_version=%s RETURNING id", (pr[1], EXTRACTOR))
        if not cur.fetchone():
            _audit(cur, "undo", "rejected", {"receipt": receipt_id})
            return {"outcome": "rejected", "message": f"{receipt_id} was not found, or was not logged by this service."}
        _audit(cur, "undo", "saved", {"receipt": receipt_id})
        return {"outcome": "saved", "message": f"Removed {receipt_id}. The original message stays in the log."}


# ---- amend: correct an entry in place (the old values are kept in the row's details) ----------------

def _load_own(cur, receipt_id: str, lock: bool = False) -> Optional[tuple[str, dict]]:
    pr = _parse_receipt(receipt_id)
    if not pr:
        return None
    table = "measurements" if pr[0] == "m" else "events"
    cur.execute(f"""SELECT x.*, t.name AS type_name FROM {table} x JOIN log_types t ON t.id = x.type_id
                    WHERE x.id=%s AND x.extractor_version=%s {'FOR UPDATE OF x' if lock else ''}""", (pr[1], EXTRACTOR))
    row = cur.fetchone()
    if not row or row["type_name"] not in KINDS:
        return None
    return row["type_name"], row


def _merged(kind: str, row: dict, changes: dict) -> tuple[dict, datetime, bool]:
    vals = {"value1": float(row["value1"]) if row.get("value1") is not None else None,
            "value2": float(row["value2"]) if row.get("value2") is not None else None, "period": row.get("period")}
    for k in ("value1", "value2", "period"):
        if k in changes:
            vals[k] = changes[k]
    at, known = row["event_at"], row["time_known"]
    if "at" in changes:
        at, known = datetime.fromisoformat(changes["at"]), bool(changes["known"])
    return vals, at, known


def _apply_amend(cur, receipt_id: str, changes: dict) -> dict:
    found = _load_own(cur, receipt_id, lock=True)
    if not found:
        return {"outcome": "rejected", "message": f"{receipt_id} was not found, or was not logged by this service."}
    kind, row = found
    vals, at, known = _merged(kind, row, changes)
    day = _day(cur, kind, at)
    old = {"event_at": row["event_at"].isoformat(), "time_known": row["time_known"], "amended_at": _now().isoformat()}
    table = KINDS[kind]["table"]
    if table == "measurements":
        old.update({"value1": float(row["value1"]), "value2": float(row["value2"]) if row["value2"] is not None else None, "period": row["period"]})
        cur.execute("""UPDATE measurements SET value1=%s, value2=%s, period=%s, event_at=%s, time_known=%s, day=%s,
                       details = jsonb_set(details, '{amended}', coalesce(details->'amended','[]'::jsonb) || %s::jsonb) WHERE id=%s""",
                    (vals["value1"], vals["value2"], vals["period"], at, known, day, json.dumps([old]), row["id"]))
    else:
        cur.execute("""UPDATE events SET event_at=%s, time_known=%s, day=%s,
                       details = jsonb_set(details, '{amended}', coalesce(details->'amended','[]'::jsonb) || %s::jsonb) WHERE id=%s""",
                    (at, known, day, json.dumps([old]), row["id"]))
    receipt = _receipt(kind, row["id"], vals, day, at, known)
    return {"outcome": "saved", "receipt": receipt, "message": f"Changed {receipt_id} to {_summary(kind, vals, at, known)}."}


def amend(receipt_id: str, kg: Optional[float] = None, systolic: Optional[float] = None, diastolic: Optional[float] = None,
          period: Optional[str] = None, at: Optional[str] = None) -> dict[str, Any]:
    now = _now()
    with connect() as conn, conn.cursor() as cur:
        found = _load_own(cur, receipt_id)
        if not found:
            _audit(cur, "amend", "rejected", {"receipt": receipt_id})
            return {"outcome": "rejected", "message": f"{receipt_id} was not found, or was not logged by this service."}
        kind, row = found
        allowed = {"weight": {"kg", "at"}, "bp": {"systolic", "diastolic", "period", "at"}, "wake": {"at"}}[kind]
        given = {k: v for k, v in (("kg", kg), ("systolic", systolic), ("diastolic", diastolic), ("period", period), ("at", at)) if v is not None}
        try:
            if not given:
                raise rules.BadInput("Nothing to change. Give the new value(s) or time.")
            bad = sorted(set(given) - allowed)
            if bad:
                raise rules.BadInput(f"A {KINDS[kind]['label']} entry cannot take: {', '.join(bad)}.")
            changes: dict[str, Any] = {}
            if kg is not None:
                changes["value1"] = rules.check_number(kg, "kg")
            if systolic is not None:
                changes["value1"] = rules.check_number(systolic, "systolic")
            if diastolic is not None:
                changes["value2"] = rules.check_number(diastolic, "diastolic")
            if period is not None:
                changes["period"] = _period(period)
            if at is not None:
                moment, known = rules.parse_at(at, now)
                changes["at"], changes["known"] = moment.isoformat(), known
        except rules.BadInput as e:
            _audit(cur, "amend", "rejected", {"receipt": receipt_id, "why": "bad input"})
            return {"outcome": "rejected", "message": str(e)}
        vals, moment, known = _merged(kind, row, changes)
        reasons = _reasons(cur, kind, _type(cur, kind), vals, moment, known, now, exclude=row["id"])
        if reasons:
            resp = _ask(cur, "amend", {"receipt": receipt_id, "changes": changes}, reasons, now)
            _audit(cur, "amend", "needs_confirmation", {"receipt": receipt_id, "pending_id": resp["pending_id"]})
            return resp
        resp = _apply_amend(cur, receipt_id, changes)
        _audit(cur, "amend", resp["outcome"], {"receipt": receipt_id})
        return resp
