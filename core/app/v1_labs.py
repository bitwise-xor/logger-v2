"""Lab reports and medication courses: saving them. Copied unchanged from logger-app connector/server.py (v1), in v1's
order; helpers v2 already has come from the other v1 modules (look-ups and changes are in v1_commands.py). v2 code
calls these from labs.py."""
import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .v1_log import ReadBack, Reject, parse_when
from .v1_thoughts import _short
from .v1_commands import LAB_VERSION, _close_tests, _course_text, _find_test, _lab_tests, _lab_value, _parse_value, _ref_text, _report_time, _tkey, _ukey, _val_text


class LabValue(BaseModel):
    test: str = Field(description="standard English name if obvious (LDL cholesterol), else the printed name")
    value: str = Field(description="exactly as printed: '12,5', '< 0.10', 'Negative'")
    unit: Optional[str] = Field(None, description="as printed")
    ref: Optional[str] = Field(None, description="reference range as printed")
    ref_low: Optional[float] = Field(None, description="only for multi-band ranges: normal band, printed unit")
    ref_high: Optional[float] = None
    printed_name: Optional[str] = None
    panel: Optional[str] = None
    site: Optional[str] = Field(None, description="imaging/DEXA region")
    side: Optional[Literal["left", "right", "both"]] = None
    specimen: Optional[str] = None
    method: Optional[str] = None
    calculated: Optional[bool] = None
    abnormal: Optional[bool] = Field(None, description="text findings only")
    note: Optional[str] = None


class Report(BaseModel):
    title: str
    provider: str = Field(description="lab or hospital")
    collected: str = Field(description="sample/scan date and time as printed")
    type: Literal["lab", "imaging"] = "lab"
    city: Optional[str] = None
    country: Optional[str] = None
    department: Optional[str] = None
    accession: Optional[str] = None
    ordered_by: Optional[str] = None
    signed_by: Optional[str] = None
    reported: Optional[str] = None
    fasting: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    tz: Optional[str] = Field(None, description="IANA zone, only outside Germany/India")
    source: Literal["image", "document"] = "image"
    values: list[LabValue]
    confirm: Optional[str] = Field(None, description="code from the READ BACK, only after the user confirmed it")


class Med(BaseModel):
    action: Literal["start", "stop"]
    name: str = Field(description="as on the pack (brand), or the ingredient")
    ingredient: Optional[str] = None
    dose: Optional[str] = None
    frequency: Optional[str] = None
    drug_class: Optional[str] = None
    indication: Optional[str] = None
    when: Optional[str] = Field(None, description="user's words; omit = today; 'unknown' if not known")
    reason: Optional[str] = Field(None, description="stop reason")
    note: Optional[str] = None
    confirmed: bool = False


_TZ_BY_COUNTRY = {"india": "Asia/Kolkata", "germany": "Europe/Berlin", "deutschland": "Europe/Berlin"}


def _prepare_report(cur, rep: Report, now: datetime) -> dict:
    if not rep.values:
        raise Reject("a report needs at least one value")
    if not rep.title.strip() or not rep.provider.strip():
        raise Reject("a report needs title and provider")
    warns: list[str] = []
    tz = rep.tz or _TZ_BY_COUNTRY.get(_tkey(rep.country or ""))
    if not tz:
        tz = "Europe/Berlin"
        warns.append(f"time zone unknown for country '{rep.country or '-'}': Europe/Berlin assumed (pass tz if wrong)")
    try:
        ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        raise Reject(f"unknown time zone '{tz}'")
    c_at, c_day, c_known = _report_time(rep.collected, tz, now, "collection date", hard=True)
    if c_day > now.astimezone(ZoneInfo(tz)).date():
        raise Reject(f"the collection date {c_day.strftime('%d.%m.%Y')} lies in the future")
    rep_t = _report_time(rep.reported, tz, now, "report date", hard=False) if rep.reported else None
    if rep.reported and not rep_t:
        warns.append(f"could not read the report date '{rep.reported}', left empty")
    rows, exact, loose = _lab_tests(cur)
    new_tests: dict = {}
    vals: list[dict] = []
    lines: list[str] = []
    for i, v in enumerate(rep.values, 1):
        if not v.test.strip() or not v.value.strip():
            raise Reject(f"value {i}: test and value are required")
        t = _find_test(exact, loose, v.test) or (_find_test(exact, loose, v.printed_name) if v.printed_name else None)
        is_new = t is None
        if is_new:
            key = _tkey(v.test)
            t = new_tests.get(key)
            if t is None:
                parsed = _parse_value(v.value, _ukey(v.unit))
                t = {"id": None, "name": v.test.strip(), "unit": _ukey(v.unit) if parsed else "", "category": rep.type,
                     "close": _close_tests(rows, v.test)}
                new_tests[key] = t
        norm = _lab_value(t, v.value, v.unit, v.ref, v.ref_low, v.ref_high, v.abnormal)
        vals.append({"src": v, "test": t, "new": is_new, **norm})
        line = f"{i:>2}. {'NEW TEST ' if is_new else ''}{t['name']}: {_val_text(norm)}"
        if v.site or v.side:
            line += f" ({', '.join(x for x in (v.site, v.side) if x)})"
        if norm["value"] is not None or norm["ref_low"] is not None or norm["ref_high"] is not None:
            line += f" [{_ref_text(norm['ref_low'], norm['ref_high'])}]"
        line += f" {norm['flag']}" if norm["flag"] else ""
        extra = []
        if norm["conversion"]:
            extra.append(f"from {v.value.strip()} {v.unit}: {norm['conversion']}")
        if is_new and t.get("close"):
            extra.append("closest existing: " + ", ".join(t["close"]))
        extra += norm["warn"]
        if extra:
            line += "  (" + "; ".join(extra) + ")"
        lines.append(line)
    # same report already stored?
    cur.execute("SELECT id, accession_no FROM lab_reports WHERE lower(provider)=lower(%s) AND collected_date=%s",
                (rep.provider.strip(), c_day))
    for r in cur.fetchall():
        same_acc = rep.accession and r["accession_no"] and rep.accession.strip() == r["accession_no"].strip()
        if same_acc or not (rep.accession and r["accession_no"]):
            warns.append(f"looks like R{r['id']} (same provider and date{', same accession' if same_acc else ''}): saving it again would duplicate it")
    payload = json.dumps([rep.model_dump(exclude={"confirm"}), [(x["test"]["name"], str(x["value"]), x["value_text"], x["flag"]) for x in vals]],
                         sort_keys=True, default=str)
    code = hashlib.sha256(payload.encode()).hexdigest()[:6].upper()
    prep = {"rep": rep, "tz": tz, "c_at": c_at, "c_day": c_day, "c_known": c_known, "rep_t": rep_t, "vals": vals,
            "new_tests": new_tests, "warns": warns, "code": code, "preview": None}
    if (rep.confirm or "").strip().upper() != code:
        flagged = [x for x in vals if x["flag"] in ("L", "H", "A")]
        head = ("READ BACK, nothing saved yet" + (" (the code did not match: the content changed or the code is wrong)" if rep.confirm else "")
                + f". Show the user this table and ask them to check it against the report. If they confirm, resend the same call with "
                  f"report.confirm=\"{code}\". If they correct something, resend with the correction and no code (a new read-back follows).")
        hdr = (f"Report: {rep.type} | {rep.title} | {rep.provider}" + (f", {rep.city}" if rep.city else "") + (f", {rep.country}" if rep.country else "")
               + f" | collected {c_day.strftime('%d.%m.%Y')}" + (c_at.strftime(" %H:%M") if c_known else " (date only)") + f" {tz}"
               + (f" | accession {rep.accession}" if rep.accession else "") + (f" | fasting: {rep.fasting}" if rep.fasting else ""))
        body = [head, hdr, f"{len(vals)} value(s), {len(flagged)} flagged, {len(new_tests)} new test name(s):"] + lines
        if new_tests:
            body.append("NEW TEST lines create a new test name. If one is really an existing test (see 'closest existing'), "
                        "resend with that standard name as test.")
        if warns:
            body += ["Warnings:"] + [f" - {w}" for w in warns]
        prep["preview"] = "\n".join(body)
    return prep


def _insert_report(cur, raw_id: int, p: dict, out: list[str], notes: list[str]) -> None:
    rep: Report = p["rep"]
    created = []
    for key, t in p["new_tests"].items():
        cur.execute("""INSERT INTO lab_tests (name, category, unit, aliases, created_by) VALUES (%s,%s,%s,%s,'claude')
                       ON CONFLICT (lower(name)) DO NOTHING RETURNING id""", (t["name"], t["category"], t["unit"], []))
        r = cur.fetchone()
        if r is None:
            cur.execute("SELECT id FROM lab_tests WHERE lower(name)=lower(%s)", (t["name"],))
            r = cur.fetchone()
        else:
            created.append(t["name"])
        t["id"] = r["id"]
    review = any(x["review"] for x in p["vals"])
    rt = p["rep_t"]
    cur.execute(
        """INSERT INTO lab_reports (raw_id, report_type, title, provider, department, city, country, tz, accession_no, ordering_doctor,
               signing_doctors, collected_date, collected_at, collected_time_known, reported_at, reported_time_known, report_status,
               fasting_status, source, notes, extractor_version, needs_review, review_reason)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (raw_id, rep.type, rep.title.strip(), rep.provider.strip(), rep.department, rep.city, rep.country, p["tz"], rep.accession,
         rep.ordered_by, rep.signed_by, p["c_day"], p["c_at"], p["c_known"], rt[0] if rt else None, bool(rt and rt[2]),
         rep.status, rep.fasting, rep.source, rep.notes, LAB_VERSION, review,
         "some values could not be converted to the standard unit" if review else None))
    rid = cur.fetchone()["id"]
    first = last = None
    for x in p["vals"]:
        v: LabValue = x["src"]
        cur.execute(
            """INSERT INTO lab_results (report_id, test_id, name_printed, panel, body_site, side, specimen, method, is_calculated,
                   qualifier, value, unit, value_text, value_raw, unit_raw, conversion, ref_range_raw, ref_low, ref_high, flag, note,
                   extractor_version, needs_review, review_reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (rid, x["test"]["id"], v.printed_name or v.test, v.panel, v.site, v.side, v.specimen, v.method, v.calculated,
             x["qualifier"], x["value"], x["unit"], x["value_text"], x["value_raw"], x["unit_raw"], x["conversion"],
             x["ref_range_raw"], x["ref_low"], x["ref_high"], x["flag"], v.note, LAB_VERSION, bool(x["review"]), x["review"]))
        vid = cur.fetchone()["id"]
        first = first or vid
        last = vid
    flagged = [f"{x['test']['name']} {x['flag']}" for x in p["vals"] if x["flag"] in ("L", "H", "A")]
    out.append(f"+ R{rid} {rep.type} report {p['c_day'].strftime('%d.%m.%Y')} {_short(rep.provider, 40)}: {len(p['vals'])} value(s) "
               f"V{first}-V{last}, {len(flagged)} flagged" + (f" ({', '.join(flagged[:12])}{', ...' if len(flagged) > 12 else ''})" if flagged else ""))
    if created:
        out.append(f"  new test name(s): {', '.join(created)}")
    if review:
        notes.append("some values could not be converted to the standard unit; they are stored as printed and flagged for review")


def _med_date(text: Optional[str], now: datetime, what: str) -> tuple[Optional[date], bool]:
    """Returns (date, known). Omitted = today; 'unknown' = (None, False)."""
    if text is None or not str(text).strip():
        return now.date(), True
    if _tkey(str(text)) in ("unknown", "not known", "unbekannt", "?"):
        return None, False
    w = parse_when(str(text), now)
    if not w:
        raise Reject(f"could not read the {what} date '{text}'")
    return w[0].date(), True


def _active_courses(cur, name: str) -> list[dict]:
    k = _tkey(name)
    cur.execute("SELECT * FROM medications WHERE stop_date IS NULL OR stop_date > current_date ORDER BY id")
    rows = cur.fetchall()
    hit = [r for r in rows if k in (_tkey(r["name"]), _tkey(r["ingredient"] or ""))]
    if not hit:
        hit = [r for r in rows if k and (k in _tkey(r["name"]) or k in _tkey(r["ingredient"] or ""))]
    return hit


def _prepare_meds(cur, meds: list[Med], now: datetime) -> list[dict]:
    out, soft = [], []
    stopping: set = set()
    order = sorted(enumerate(meds, 1), key=lambda x: x[1].action != "stop")   # stops first: a dose change is stop + start
    for i, m in order:
        if not m.name.strip():
            raise Reject(f"medication {i}: name is required")
        d, known = _med_date(m.when, now, "start" if m.action == "start" else "stop")
        if d and d > now.date() + timedelta(days=1):
            raise Reject(f"medication {i}: the date {d.strftime('%d.%m.%Y')} lies in the future")
        if m.action == "start":
            act = _active_courses(cur, m.ingredient or m.name) or _active_courses(cur, m.name)
            act = [r for r in act if r["id"] not in stopping]
            if act and not m.confirmed:
                soft.append(f"{i}) {m.name}: already active as {', '.join(_course_text(r) for r in act)}. For a dose change, stop the old "
                            f"course and start the new one in the same call; to keep both, resend with confirmed=true.")
            out.append({"m": m, "start": d, "known": known})
        else:
            act = _active_courses(cur, m.name)
            if not act:
                cur.execute("SELECT * FROM medications WHERE stop_date IS NULL OR stop_date > current_date ORDER BY id")
                cur_list = ", ".join(_course_text(r) for r in cur.fetchall()) or "none"
                raise Reject(f"no active medication matches '{m.name}' (active: {cur_list})")
            if len(act) > 1:
                raise Reject(f"'{m.name}' matches several active courses ({', '.join(_course_text(r) for r in act)}): use the full name")
            if not known:
                raise Reject("a stop needs a date (omit when for today)")
            if act[0]["start_date"] and d < act[0]["start_date"]:
                raise Reject(f"stop date {d.strftime('%d.%m.%Y')} lies before the start {act[0]['start_date'].strftime('%d.%m.%Y')}")
            if act[0]["id"] in stopping:
                raise Reject(f"{_course_text(act[0])} is stopped twice in this call")
            stopping.add(act[0]["id"])
            out.append({"m": m, "stop": d, "row": act[0]})
    if soft:
        raise ReadBack("READ BACK, nothing saved (nothing else in this message was saved either):\n" + "\n".join(soft))
    return out


def _insert_meds(cur, raw_id: int, prepared: list[dict], out: list[str], notes: list[str]) -> None:
    for p in prepared:
        m: Med = p["m"]
        if "row" in p:
            cur.execute("UPDATE medications SET stop_date=%s, stop_reason=%s WHERE id=%s", (p["stop"], m.reason, p["row"]["id"]))
            out.append(f"~ {_course_text(p['row'])}: stopped {p['stop'].strftime('%d.%m.%Y')}" + (f" ({m.reason})" if m.reason else ""))
            continue
        cur.execute(
            """INSERT INTO medications (raw_id, name, ingredient, dose, frequency, drug_class, indication, start_date, note,
                   extractor_version, needs_review, review_reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (raw_id, m.name.strip(), m.ingredient, m.dose, m.frequency, m.drug_class, m.indication, p["start"], m.note,
             LAB_VERSION, not p["known"], None if p["known"] else "start date not known"))
        r = cur.fetchone()
        out.append(f"+ {_course_text(r)}, " + (f"from {p['start'].strftime('%d.%m.%Y')}" if p["known"] else "start unknown (flagged)"))

