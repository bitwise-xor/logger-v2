"""What the Labs and medication page's forms do. Every correction of a lab value, a report or a medication course goes
through the logger's change function and is shown first (v1's own preview, made in a savepoint and rolled back), then
made after the Yes (decided 26.10.2026). Only changed fields are sent, so v1 checks exactly what the user changed."""
import re
from datetime import date, datetime
from typing import Optional

from . import commands, labspage
from .db import connect
from .v1_log import Reject
from .webactions import _change, _rejected
from .webfood import _again
from .when import BERLIN


def _ask_first(steps: list[dict], what: str, ok: str, form, action: str, label: str = "Yes, change") -> dict:
    if form.get("confirmed") == "1":
        return _change(steps, what, ok)
    with connect() as conn, conn.cursor() as cur:
        try:
            lines = commands._preview(cur, steps, datetime.now(BERLIN))
        except Reject as e:
            return _rejected(str(e))
    return {"outcome": "needs_confirmation", "read_back": "\n".join(lines) if isinstance(lines, list) else str(lines),
            "confirm": {"action": action, "label": label, "no": labspage.safe_back(form.get("back")), "fields": _again(form) | {"confirmed": "1"}}}


def _text(form, k: str, n: int) -> str:
    return " ".join(str(form.get(k, "")).split())[:n]


def _date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def value(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    if not re.fullmatch(r"V\d{1,9}", ref):
        return _rejected("Unknown lab value.")
    with connect() as c:
        r = c.execute(f"SELECT {labspage.RESULT_COLS} FROM lab_results v JOIN lab_tests t ON t.id = v.test_id WHERE v.id = %s",
                      (int(ref[1:]),)).fetchone()
    if not r:
        return _rejected("That value is not there any more.")
    old_v, old_u, old_r = labspage.printed(r)
    new_v, new_u, new_r = _text(form, "value", 60), _text(form, "unit", 30), _text(form, "ref", 60)
    if not new_v:
        return _rejected("The value cannot be empty.")
    fields: dict = {}
    if new_v != old_v:
        fields["value"] = new_v
    if new_u != old_u:
        fields["unit"] = new_u or None
    if new_r != old_r:
        fields["ref"] = new_r or None
    if not fields:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    return _ask_first([{"action": "change", "args": {"id": ref, "fields": fields}}], "lab value", f"Changed {r['test']}.", form, "/web/labs/value")


def report(form, key: Optional[str]) -> dict:
    ref = str(form.get("id", ""))
    if not re.fullmatch(r"R\d{1,9}", ref):
        return _rejected("Unknown report.")
    with connect() as c:
        p = c.execute("SELECT id, collected_date, title, provider, notes FROM lab_reports WHERE id = %s", (int(ref[1:]),)).fetchone()
    if not p:
        return _rejected("That report is not there any more.")
    fields: dict = {}
    for k, n in (("title", 200), ("provider", 200)):
        v = _text(form, k, n)
        if not v:
            return _rejected(f"The {k if k == 'title' else 'lab'} cannot be empty.")
        if v != (p[k] or ""):
            fields[k] = v
    notes = str(form.get("notes", "")).strip()[:2000]
    if notes != (p["notes"] or ""):
        fields["notes"] = notes or None
    d = _date(str(form.get("collected", "")))
    if not d:
        return _rejected("Give the report's date.")
    if d != p["collected_date"]:
        fields["collected"] = f"{d:%d.%m.%Y}"
    if not fields:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    return _ask_first([{"action": "change", "args": {"id": ref, "fields": fields}}], "lab report", "Changed the report.", form, "/web/labs/report")


def med(form, key: Optional[str]) -> dict:
    ref, act = str(form.get("id", "")), form.get("act")
    if not re.fullmatch(r"D\d{1,9}", ref):
        return _rejected("Unknown medication course.")
    with connect() as c:
        m = c.execute("SELECT * FROM medications WHERE id = %s", (int(ref[1:]),)).fetchone()
        today = c.execute("SELECT log_day(now()) AS d").fetchone()["d"]
    if not m:
        return _rejected("That course is not there any more.")
    if act == "end":
        if m["stop_date"]:
            return {"outcome": "unchanged", "reason": "That course has a stop date already."}
        return _ask_first([{"action": "change", "args": {"id": ref, "fields": {"stop": f"{today:%d.%m.%Y}"}}}], "medication end",
                          f"{m['name']} ended today.", form, "/web/labs/med", "Yes, end it today")
    if act != "save":
        return _rejected("Unknown action.")
    fields: dict = {}
    for k, n in (("dose", 60), ("frequency", 60), ("indication", 120)):
        v = _text(form, k, n)
        if v != (m[k] or ""):
            fields[k] = v or None
    for k in ("start", "stop"):
        s = str(form.get(k, "")).strip()
        d = _date(s) if s else None
        if s and not d:
            return _rejected(f"Give the {k} as a date.")
        if d != m[f"{k}_date"]:
            fields[k] = f"{d:%d.%m.%Y}" if d else None
    if not fields:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    return _ask_first([{"action": "change", "args": {"id": ref, "fields": fields}}], "medication change", f"Changed {m['name']}.", form, "/web/labs/med")


# Every form of the page: path under /web/labs/, the function
ACTIONS = {"value": value, "report": report, "med": med}
