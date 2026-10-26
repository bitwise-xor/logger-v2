"""Look-ups and changes (v1's find and change tools). Copied unchanged from logger-app connector/server.py (v1), in
v1's order, with the helpers they need; helpers v2 already has come from the other v1 modules. v2 code calls these from
commands.py, which reads the user's words into v1's find and change arguments."""
import difflib
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from .v1_food import _find_foods, _fkey, _food_cat, _norm, _num, _resolve_type, _to_amount, _types
from .v1_log import (BERLIN, FOOD_VERSION, Reject, _NUT, _NUT_LABEL, _day_line, _day_of, _dm, _fdesc, _food_line100, _food_today,
                     _meal_spec_items, _n, _range_problems, fmt, parse_when)
from .v1_thoughts import Area, NextStep, Source, Stage, ThoughtType, _add_link, _match_project, _short, _thought_row


LAB_VERSION = "chat-lab-v1"


THOUGHT_TYPES = set(ThoughtType.__args__)


NEXT_STEPS = set(NextStep.__args__)


AREAS = set(Area.__args__)


STAGES = set(Stage.__args__)


SOURCES = set(Source.__args__)


def _hm(r: dict) -> str:
    return r["event_at"].astimezone(BERLIN).strftime("%H:%M") if r["time_known"] else "--:--"


def _find_health(cur, what: str, since, until, limit) -> str:
    limit = max(1, min(int(limit or 20), 50))
    now = datetime.now(BERLIN)
    today = _day_of(now, "")
    key = _norm(what)
    lo_b, hi_b = _bound(since, False), _bound(until, True)
    lo, hi = (lo_b.date() if lo_b else None), (hi_b.date() if hi_b else None)
    rule = " (before 05:00 still counts as the previous day)" if now.hour < 5 else ""
    lab = _find_lab(cur, key, what, lo, hi, limit)
    if lab is not None:
        return lab

    if key in ("left", "checklist"):
        day = lo or today
        cur.execute("SELECT * FROM checklist_status(%s)", (day,))
        rows = cur.fetchall()
        if not rows:
            return "The checklist is empty (no type has on_checklist set)."
        left = [r["label"] for r in rows if not r["complete"]]
        head = f"Checklist {day.strftime('%d.%m.%Y')}{rule}: " + ("all done" if not left else "left: " + ", ".join(left))
        return head + "\n" + "\n".join(f"[{'x' if r['complete'] else ' '}] {r['label']} {r['done']}/{r['target']}" for r in rows)

    if key == "day":
        day = lo or today
        cur.execute("SELECT id, type, period, value1, value2, unit, event_at, time_known, note, needs_review FROM v_measurements WHERE day=%s", (day,))
        items = [(r["event_at"], f"m{r['id']} {_hm(r)} {r['type']} {_num(r['value1'])}"
                  + (f"/{_num(r['value2'])}" if r["value2"] is not None else "") + f" {r['unit'] or ''}".rstrip()
                  + (f" ({r['period']})" if r["period"] else "") + (f" - {r['note']}" if r["note"] else "")
                  + ("  !" if r["needs_review"] else "")) for r in cur.fetchall()]
        cur.execute("SELECT id, type, details, event_at, time_known, note, needs_review FROM v_events WHERE day=%s", (day,))
        items += [(r["event_at"], f"e{r['id']} {_hm(r)} {r['type']}" + (f" #{r['details']['session']}" if r["details"].get("session") else "")
                   + (f" - {r['note']}" if r["note"] else "") + ("  !" if r["needs_review"] else "")) for r in cur.fetchall()]
        if not items:
            return f"Nothing logged for {day.strftime('%d.%m.%Y')}{rule}."
        items.sort(key=lambda x: x[0])
        return f"{len(items)} entr{'y' if len(items) == 1 else 'ies'} for {day.strftime('%d.%m.%Y')}{rule}:\n" + "\n".join(x[1] for x in items)

    if key == "review":
        cur.execute("SELECT id, day, type, value1, value2, review_reason FROM v_measurements WHERE needs_review ORDER BY day DESC LIMIT %s", (limit,))
        items = [f"m{r['id']} {_dm(r['day'])} {r['type']} {_num(r['value1'])}" + (f"/{_num(r['value2'])}" if r["value2"] is not None else "")
                 + f"  ! {r['review_reason'] or 'flagged'}" for r in cur.fetchall()]
        cur.execute("SELECT id, day, type, review_reason FROM v_events WHERE needs_review ORDER BY day DESC LIMIT %s", (limit,))
        items += [f"e{r['id']} {_dm(r['day'])} {r['type']}  ! {r['review_reason'] or 'flagged'}" for r in cur.fetchall()]
        cur.execute("SELECT id, collected_date, title, review_reason FROM lab_reports WHERE needs_review ORDER BY collected_date DESC LIMIT %s", (limit,))
        items += [f"R{r['id']} {r['collected_date'].strftime('%d.%m.%Y')} {_short(r['title'], 50)}  ! {r['review_reason'] or 'flagged'}"
                  for r in cur.fetchall()]
        cur.execute("""SELECT r.id, p.collected_date, t.name, r.value, r.unit, r.value_raw, r.unit_raw, r.value_text, r.qualifier, r.review_reason
                       FROM lab_results r JOIN lab_reports p ON p.id=r.report_id JOIN lab_tests t ON t.id=r.test_id
                       WHERE r.needs_review ORDER BY p.collected_date DESC, r.id LIMIT %s""", (limit,))
        items += [f"V{r['id']} {r['collected_date'].strftime('%d.%m.%Y')} {r['name']} {_val_text(r)}  ! {r['review_reason'] or 'flagged'}"
                  for r in cur.fetchall()]
        cur.execute("SELECT * FROM medications WHERE needs_review ORDER BY id LIMIT %s", (limit,))
        items += [f"{_course_text(r)}  ! {r['review_reason'] or 'flagged'}" for r in cur.fetchall()]
        return (f"{len(items)} flagged health row(s) (change with the m/e/R/V/D id; fields needs_review=false clears):\n" + "\n".join(items)) \
            if items else "No flagged health rows."

    lo_d, hi_d = lo or today - timedelta(days=13), hi or today
    span = f"{_dm(lo_d)}-{hi_d.strftime('%d.%m.%Y')}"
    ndays = (hi_d - lo_d).days + 1

    if key == "sleep":
        cur.execute("SELECT wake_day, bed_at, wake_at, hours FROM v_sleep WHERE wake_day BETWEEN %s AND %s ORDER BY wake_at DESC", (lo_d, hi_d))
        rows = cur.fetchall()
        if not rows:
            return f"No sleep pairs (bed + wake-up) for {span}."
        hs = [float(r["hours"]) for r in rows]
        stats = f"{len(hs)} nights {span}: mean {sum(hs) / len(hs):.1f} h, shortest {min(hs):.1f}, longest {max(hs):.1f}"
        lines = [f"{_dm(r['wake_day'])} bed {r['bed_at'].astimezone(BERLIN).strftime('%d.%m. %H:%M')} - wake "
                 f"{r['wake_at'].astimezone(BERLIN).strftime('%H:%M')} = {_num(r['hours'])} h" for r in rows[:limit]]
        return stats + (f" (newest {limit} listed)" if len(rows) > limit else "") + "\n" + "\n".join(lines)

    _, idx = _types(cur)
    t = idx.get(key)
    if not t:
        raise Reject(f"unknown health selector '{what}'. Use left, sleep, day, review, or a type name/alias such as bp, weight, sugar, shoonya")

    if t["kind"] == "measurement":
        cur.execute("""SELECT period, count(*) AS n, avg(value1) AS a1, min(value1) AS lo1, max(value1) AS hi1,
                              avg(value2) AS a2, min(value2) AS lo2, max(value2) AS hi2
                       FROM measurements WHERE type_id=%s AND day BETWEEN %s AND %s GROUP BY period ORDER BY period""", (t["id"], lo_d, hi_d))
        groups = cur.fetchall()
        if not groups:
            return f"No {t['name']} entries for {span}."
        unit = t["unit"] or ""
        lines = [f"{t['label']} {span} (stats cover the whole window):"]
        for g in groups:
            if g["a2"] is not None:
                lines.append(f"  {g['period'] or 'all'}: n={g['n']}, mean {float(g['a1']):.0f}/{float(g['a2']):.0f} {unit}, "
                             f"systolic {_num(g['lo1'])}-{_num(g['hi1'])}, diastolic {_num(g['lo2'])}-{_num(g['hi2'])}")
            else:
                lines.append(f"  {g['period'] or 'all'}: n={g['n']}, mean {float(g['a1']):.1f} {unit}, range {_num(g['lo1'])}-{_num(g['hi1'])}")
        cur.execute("""SELECT id, day, period, value1, value2, event_at, time_known, needs_review FROM measurements
                       WHERE type_id=%s AND day BETWEEN %s AND %s ORDER BY day DESC, event_at DESC LIMIT %s""", (t["id"], lo_d, hi_d, limit))
        lines.append("newest first:")
        lines += [f"  m{r['id']} {_dm(r['day'])} {_hm(r)} {_num(r['value1'])}" + (f"/{_num(r['value2'])}" if r["value2"] is not None else "")
                  + (f" ({r['period']})" if r["period"] else "") + ("  !" if r["needs_review"] else "") for r in cur.fetchall()]
        return "\n".join(lines)

    cur.execute("SELECT count(*) AS n, count(DISTINCT day) AS d, max(day) AS last FROM events WHERE type_id=%s AND day BETWEEN %s AND %s",
                (t["id"], lo_d, hi_d))
    s = cur.fetchone()
    if not s["n"]:
        return f"No {t['name']} events for {span}."
    cur.execute("SELECT DISTINCT day FROM events WHERE type_id=%s AND day BETWEEN %s AND %s ORDER BY day", (t["id"], lo_d, hi_d))
    days = [_dm(r["day"]) for r in cur.fetchall()]
    return (f"{t['label']} {span}: {s['n']} event(s) on {s['d']} of {ndays} days, last {_dm(s['last'])}.\n"
            f"Days: {', '.join(days[:45])}" + (" ..." if len(days) > 45 else ""))


_UNIT_SPELL = {}
for _canon, _keys in {
    "mg/dL": "mg/dl mg% mg/100ml", "g/dL": "g/dl g% gm/dl gm% g/100ml", "mg/L": "mg/l", "g/L": "g/l",
    "mmol/L": "mmol/l", "µmol/L": "umol/l", "nmol/L": "nmol/l", "pmol/L": "pmol/l", "mmol/mol": "mmol/mol",
    "mEq/L": "meq/l mval/l", "U/L": "u/l iu/l ie/l units/l", "mU/L": "mu/l miu/l uiu/ml uu/ml mie/l uie/ml",
    "ng/mL": "ng/ml ug/l", "pg/mL": "pg/ml ng/l", "µg/dL": "ug/dl", "ng/dL": "ng/dl", "µg/mL": "ug/ml",
    "10^3/µL": "10^3/ul 10^9/l thou/ul thou/mm^3 k/ul tsd/ul tsd./ul /nl 10^3/mm^3 1000/ul",
    "10^6/µL": "10^6/ul 10^12/l mio/ul mio./ul mill/ul mill/mm^3 million/ul million/mm^3 m/ul /pl 10^6/mm^3",
    "/µL": "/ul /mm^3 /cumm", "lakh/µL": "lakh/ul lakhs/ul lakh/cumm lakhs/cumm lakh/mm^3",
    "%": "%", "fL": "fl um^3", "pg": "pg", "fmol": "fmol", "mm/h": "mm/h mm/hr mm/1h mm/1sthr mm/1sthour",
    "mL/min/1.73m²": "ml/min/1.73m^2 ml/min/1,73m^2 ml/min/1.73qm", "kg": "kg", "g": "g", "lb": "lb lbs",
    "mm": "mm", "cm": "cm", "cm/s": "cm/s", "kg/m²": "kg/m^2", "g/cm²": "g/cm^2", "SD": "sd", "ratio": "ratio",
    "score": "score", "kcal/day": "kcal/day", "mL": "ml", "/hpf": "/hpf", "L/L": "l/l",
}.items():
    for _k in _keys.split():
        _UNIT_SPELL[_k] = _canon


def _ukey(u: Optional[str]) -> str:
    """Canonical spelling of a unit ('' for none). Case matters only for G/l and T/l (Giga/Tera per litre)."""
    s = (u or "").strip().rstrip(".").strip()
    if not s:
        return ""
    if re.fullmatch(r"G\s*/\s*[lL]", s):
        return "10^3/µL"
    if re.fullmatch(r"T\s*/\s*[lL]", s):
        return "10^6/µL"
    k = s.lower().replace("µ", "u").replace("μ", "u").replace("³", "^3").replace("²", "^2").replace("×", "x")
    k = re.sub(r"\s+", "", k).replace("cells", "").replace("mcg", "ug")
    k = re.sub(r"^x(?=10)", "", k)
    k = re.sub(r"10e(\d+)", r"10^\1", k)
    k = re.sub(r"(?<=m)([23])$", r"^\1", k)          # m2 -> m^2, mm3 -> mm^3
    return _UNIT_SPELL.get(k, k)


def _lin(f: float, label: str):
    return (lambda x: x * f), label


_GLU = {"mmol/L": _lin(18.016, "mmol/L × 18.016 → mg/dL")}


_CHOL = {"mmol/L": _lin(38.67, "mmol/L × 38.67 → mg/dL")}


_G_L = {"g/L": _lin(0.1, "g/L ÷ 10 → g/dL")}


_G_L100 = {"g/L": _lin(100, "g/L × 100 → mg/dL")}


_BILI = {"µmol/L": _lin(1 / 17.1, "µmol/L ÷ 17.1 → mg/dL")}


_LB = {"lb": _lin(0.4536, "lb × 0.4536 → kg"), "g": _lin(0.001, "g ÷ 1000 → kg")}


_MEQ1 = {"mEq/L": _lin(1, "mEq/L = mmol/L")}


_CONV: dict = {  # test name -> {canonical printed unit: (function to the standard unit, label)}
    **{n: _GLU for n in ("Glucose, fasting", "Glucose, 2 h postprandial", "Estimated average glucose")},
    **{n: _CHOL for n in ("Cholesterol, total", "HDL cholesterol", "LDL cholesterol", "VLDL cholesterol",
                          "Non-HDL cholesterol", "Cholesterol, esterified", "Cholesterol, free")},
    "Triglycerides": {"mmol/L": _lin(88.57, "mmol/L × 88.57 → mg/dL")},
    "Creatinine": {"µmol/L": _lin(1 / 88.42, "µmol/L ÷ 88.42 → mg/dL")},
    "Urea": {"mmol/L": _lin(6.006, "mmol/L × 6.006 → mg/dL")},
    "BUN": {"mmol/L": _lin(2.801, "mmol/L × 2.801 → mg/dL")},
    "Uric acid": {"µmol/L": _lin(1 / 59.48, "µmol/L ÷ 59.48 → mg/dL")},
    "Calcium, total": {"mg/dL": _lin(1 / 4.008, "mg/dL ÷ 4.008 → mmol/L"), "mEq/L": _lin(0.5, "mEq/L ÷ 2 → mmol/L")},
    "Phosphorus (phosphate)": {"mg/dL": _lin(0.3229, "mg/dL × 0.3229 → mmol/L")},
    "Magnesium": {"mg/dL": _lin(1 / 2.431, "mg/dL ÷ 2.431 → mmol/L"), "mEq/L": _lin(0.5, "mEq/L ÷ 2 → mmol/L")},
    "Sodium": _MEQ1, "Potassium": _MEQ1, "Chloride": _MEQ1,
    "Vitamin D, 25-OH": {"nmol/L": _lin(1 / 2.496, "nmol/L ÷ 2.496 → ng/mL")},
    "T3, total": {"ng/dL": _lin(0.01, "ng/dL ÷ 100 → ng/mL"), "nmol/L": _lin(0.651, "nmol/L × 0.651 → ng/mL")},
    "T4, total": {"nmol/L": _lin(1 / 12.87, "nmol/L ÷ 12.87 → µg/dL")},
    "Free T4": {"pmol/L": _lin(1 / 12.87, "pmol/L ÷ 12.87 → ng/dL")},
    "Free T3": {"pmol/L": _lin(1 / 1.536, "pmol/L ÷ 1.536 → pg/mL")},
    "Hemoglobin": {**_G_L, "mmol/L": _lin(1.611, "mmol/L × 1.611 → g/dL")},
    "MCHC": {**_G_L, "mmol/L": _lin(1.611, "mmol/L × 1.611 → g/dL")},
    "MCH": {"fmol": _lin(16.11, "fmol × 16.11 → pg")},
    "Hematocrit": {"L/L": _lin(100, "L/L × 100 → %")},
    "Total protein": _G_L, "Albumin": _G_L, "Globulin": _G_L,
    "Bilirubin, total": _BILI, "Bilirubin, direct": _BILI, "Bilirubin, indirect": _BILI,
    "Vitamin B12": {"pmol/L": _lin(1.355, "pmol/L × 1.355 → pg/mL")},
    "Iron": {"µmol/L": _lin(5.585, "µmol/L × 5.585 → µg/dL")}, "TIBC": {"µmol/L": _lin(5.585, "µmol/L × 5.585 → µg/dL")},
    "Transferrin": _G_L100, "Apolipoprotein A1": _G_L100, "Apolipoprotein B": _G_L100, "Ceruloplasmin": _G_L100,
    "CRP": {"mg/dL": _lin(10, "mg/dL × 10 → mg/L")},
    "HbA1c": {"mmol/mol": ((lambda x: 0.09148 * x + 2.152), "mmol/mol × 0.09148 + 2.152 → % (NGSP)")},
    **{n: _LB for n in ("Body mass (DEXA)", "Fat mass", "Lean mass", "Bone mineral content")},
}


_CONV_BY_STD = {"10^3/µL": {"/µL": _lin(0.001, "/µL ÷ 1000 → 10^3/µL"), "lakh/µL": _lin(100, "lakh/µL × 100 → 10^3/µL")}}


_NUMRX = r"([-−]?\d+(?:[.,]\d+)?)"


_QUAL = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">=", "less than": "<", "greater than": ">",
         "more than": ">", "unter": "<", "über": ">", "below": "<", "above": ">"}


def _to_float(s: str, thousands: bool = False) -> float:
    s = s.replace("−", "-")
    if thousands:
        return float(s.replace(",", ""))
    return float(s.replace(",", "."))


def _sig_round(x: float, printed: str) -> Decimal:
    """Round a converted value to the precision printed (at least 3 significant digits)."""
    digits = len(re.sub(r"^0+", "", re.sub(r"\D", "", printed or ""))) or 3
    return Decimal(f"{x:.{max(3, digits)}g}")


def _dec(x) -> Decimal:
    return Decimal(repr(float(x))) if isinstance(x, float) else Decimal(str(x))


def _lnum(x) -> str:
    """Lab number for display: exact, no trailing zeros, no exponent."""
    if x is None:
        return "-"
    s = format(_dec(x).normalize(), "f")
    return "0" if s in ("-0", "") else s


def _parse_value(text: str, unit_key: str):
    """'12,5' / '< 0.10' / 'less than 3.0' -> (qualifier, Decimal, warning); text results -> None."""
    t = text.strip()
    m = re.fullmatch(r"(<=|>=|≤|≥|<|>|less than|greater than|more than|unter|über|below|above)?\s*" + _NUMRX, t, re.I)
    if not m:
        return None
    qual = _QUAL[m[1].lower()] if m[1] else None
    num, warn = m[2].replace("−", "-"), None
    if re.fullmatch(r"\d{1,3},\d{3}", num):                 # 7,500 cells or 1,021 specific gravity?
        thousands = unit_key in ("/µL", "lakh/µL")
        warn = f"read '{num}' as {'thousands separator' if thousands else 'decimal comma'}"
        return qual, Decimal(num.replace(",", "") if thousands else num.replace(",", ".")), warn
    return qual, Decimal(num.replace(",", ".")), warn


def _parse_ref(text: Optional[str]):
    """Single-band printed range -> (low, high); several bands or words -> None."""
    if not text or not text.strip():
        return None
    s = text.strip().lower().replace("–", "-").replace("—", "-").replace("≤", "<=").replace("≥", ">=")
    tail = r"(?:\s*[a-zµ/%^\d.²³]+)?"
    m = re.fullmatch(_NUMRX + r"\s*(?:-|to|bis)\s*" + _NUMRX + tail, s)
    if m:
        return _to_float(m[1]), _to_float(m[2])
    m = re.fullmatch(r"(?:<=|<|less than|up to|bis|unter|below)\s*" + _NUMRX + tail, s)
    if m:
        return None, _to_float(m[1])
    m = re.fullmatch(r"(?:>=|>|more than|greater than|above|über)\s*" + _NUMRX + tail, s)
    if m:
        return _to_float(m[1]), None
    return None


def _lab_flag(v: Optional[float], qual: Optional[str], lo: Optional[float], hi: Optional[float]) -> Optional[str]:
    if v is None or (lo is None and hi is None):
        return None
    if qual in ("<", "<="):          # true value lies below v
        if lo is not None and v <= lo:
            return "L"
        return "N" if lo is None and (hi is None or v <= hi) else None
    if qual in (">", ">="):          # true value lies above v
        if hi is not None and v >= hi:
            return "H"
        return "N" if hi is None and (lo is None or v >= lo) else None
    if lo is not None and v < lo:
        return "L"
    if hi is not None and v > hi:
        return "H"
    return "N"


def _lab_value(test: dict, value: str, unit: Optional[str], ref: Optional[str], low: Optional[float], high: Optional[float],
               abnormal: Optional[bool], std_bounds: Optional[tuple] = None) -> dict:
    """Normalise one printed value against the test's standard unit. Returns the columns plus warnings/review."""
    std = _ukey(test.get("unit") or "")
    cu = _ukey(unit)
    out = {"value": None, "unit": test.get("unit") or "", "value_text": None, "value_raw": None, "unit_raw": (unit or "").strip() or None,
           "qualifier": None, "conversion": None, "ref_range_raw": (ref or "").strip() or None, "ref_low": None, "ref_high": None,
           "flag": None, "warn": [], "review": None}
    parsed = _parse_value(value, cu)
    if parsed is None:                                       # text result or finding
        out["value_text"] = value.strip()
        out["flag"] = "A" if abnormal else ("N" if abnormal is False else None)
        return out
    qual, num, warn = parsed
    out["value_raw"], out["qualifier"] = value.strip().lstrip("<>=≤≥ ").strip() if qual else value.strip(), qual
    m = re.search(_NUMRX, out["value_raw"])
    printed_num = m[1] if m else value
    if warn:
        out["warn"].append(warn)
    fn = None
    if not std:                                             # unitless or unknown test: keep the printed unit
        out["unit"] = cu if cu else ""
    elif not cu:
        out["warn"].append(f"no unit printed; {test.get('unit')} assumed")
    elif cu != std:
        conv = _CONV.get(test.get("name"), {}).get(cu) or _CONV_BY_STD.get(std, {}).get(cu)
        if not conv:
            out["warn"].append(f"unit {unit} cannot be converted to {test.get('unit')}: stored as printed, flagged for review")
            out["review"] = f"unit {unit} not convertible to {test.get('unit')}"
            out["value_raw"] = value.strip()
            out["qualifier"] = None
            return out
        fn, out["conversion"] = conv
    out["value"] = _sig_round(fn(float(num)), printed_num) if fn else num
    if out["unit"] == "" and std == "" and cu:
        out["unit"] = cu
    # reference range -> standard unit
    lo = hi = None
    if low is not None or high is not None:
        lo, hi = low, high
    elif out["ref_range_raw"]:
        pr = _parse_ref(out["ref_range_raw"])
        if pr:
            lo, hi = pr
        elif std_bounds:
            lo = hi = None
        else:
            out["warn"].append("reference range has several bands or words: pass ref_low/ref_high for the normal band")
    if std_bounds and lo is None and hi is None and low is None and high is None:
        out["ref_low"], out["ref_high"] = std_bounds
    else:
        conv_b = (lambda b: None if b is None else _sig_round(fn(float(b)), _lnum(b))) if fn else \
                 (lambda b: None if b is None else _dec(b))
        out["ref_low"], out["ref_high"] = conv_b(lo), conv_b(hi)
    out["flag"] = _lab_flag(float(out["value"]), qual,
                            None if out["ref_low"] is None else float(out["ref_low"]),
                            None if out["ref_high"] is None else float(out["ref_high"]))
    return out


def _tkey(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tloose(s: str) -> str:
    return re.sub(r"[^0-9a-zäöüßµ%]+", "", (s or "").lower())


def _lab_tests(cur) -> tuple[list[dict], dict, dict]:
    cur.execute("SELECT id, name, category, unit, aliases, compare_note FROM lab_tests ORDER BY id")
    rows = cur.fetchall()
    exact: dict = {}
    loose: dict = {}
    for r in rows:
        for k in [r["name"]] + list(r["aliases"] or []):
            exact.setdefault(_tkey(k), set()).add(r["id"])
            loose.setdefault(_tloose(k), set()).add(r["id"])
    by_id = {r["id"]: r for r in rows}
    for r in rows:   # a test's own name always wins
        exact[_tkey(r["name"])] = {r["id"]}
    pick = lambda d: {k: by_id[next(iter(v))] for k, v in d.items() if len(v) == 1}  # noqa: E731
    return rows, pick(exact), pick(loose)


def _find_test(exact: dict, loose: dict, text: str) -> Optional[dict]:
    return exact.get(_tkey(text)) or loose.get(_tloose(text))


def _close_tests(rows: list[dict], text: str, n: int = 3) -> list[str]:
    names = {}
    for r in rows:
        for k in [r["name"]] + list(r["aliases"] or []):
            names.setdefault(_tkey(k), r["name"])
    hits = difflib.get_close_matches(_tkey(text), list(names), n=8, cutoff=0.6)
    out = []
    for h in hits:
        if names[h] not in out:
            out.append(names[h])
    return out[:n]


def _report_time(text: Optional[str], tz: str, now: datetime, what: str, hard: bool):
    """Printed date/time -> (aware datetime in the lab's zone, date, time_known). Unreadable: Reject if hard, else None."""
    if not text or not text.strip():
        return None
    w = parse_when(text, now)
    if not w:
        if hard:
            raise Reject(f"could not read the {what} '{text}' (use the printed date, e.g. 02.10.2026 08:15)")
        return None
    dt, date_only = w
    local = datetime.combine(dt.date(), time(12, 0) if date_only else dt.time().replace(tzinfo=None), ZoneInfo(tz))
    return local, local.date(), not date_only


def _ref_text(lo, hi) -> str:
    if lo is None and hi is None:
        return "-"
    if lo is None:
        return f"<{_lnum(hi)}"
    if hi is None:
        return f">{_lnum(lo)}"
    return f"{_lnum(lo)}-{_lnum(hi)}"


def _val_text(r: dict) -> str:
    if r.get("value") is None:
        return r.get("value_text") or f"{r.get('value_raw')} {r.get('unit_raw') or ''}".strip()
    return f"{r.get('qualifier') or ''}{_lnum(r['value'])} {r.get('unit') or ''}".strip()


def _course_text(r: dict) -> str:
    s = f"D{r['id']} {r['name']}" + (f" ({r['ingredient']})" if r.get("ingredient") else "")
    s += "".join(f" {x}" for x in (r.get("dose"), r.get("frequency")) if x)
    return s


def _med_footer(cur, first: Optional[date]) -> str:
    cur.execute("""SELECT * FROM medications WHERE stop_date IS NULL OR %s::date IS NULL OR stop_date >= %s::date
                   ORDER BY start_date NULLS LAST, id""", (first, first))
    rows = cur.fetchall()
    if not rows:
        return ""
    parts = []
    for r in rows:
        span = (f"from {r['start_date'].strftime('%d.%m.%Y')}" if r["start_date"] else "start unknown") + \
               (f" to {r['stop_date'].strftime('%d.%m.%Y')}" if r["stop_date"] else "")
        parts.append(f"{r['name']}" + (f" {r['dose']}" if r["dose"] else "") + f" {span} (D{r['id']})")
    return "Medication in this period: " + "; ".join(parts)


def _report_line(r: dict) -> str:
    return (f"R{r['id']} {r['collected_date'].strftime('%d.%m.%Y')} {r['report_type']} | {_short(r['title'], 60)} | "
            f"{_short(r['provider'], 40)}" + (f", {r['city']}" if r.get("city") else "")
            + (f" | {r['n']} value(s), {r['nf']} flagged" if "n" in r else "") + ("  !" if r.get("needs_review") else ""))


def _result_line(r: dict, with_date: bool = False) -> str:
    s = (r["collected_date"].strftime("%d.%m.%Y") + " " if with_date else "") + f"V{r['id']} "
    s += r["test"] if "test" in r else ""
    if r.get("body_site") or r.get("side"):
        s += f" ({', '.join(x for x in (r.get('body_site'), r.get('side')) if x)})"
    s += f": {_val_text(r)}"
    if r.get("value") is not None or r.get("ref_low") is not None or r.get("ref_high") is not None:
        s += f" [{_ref_text(r.get('ref_low'), r.get('ref_high'))}]"
    s += f" {r['flag']}" if r.get("flag") else ""
    if r.get("method"):
        s += f" | {r['method']}"
    if r.get("note"):
        s += f" | note: {_short(r['note'], 110)}"
    if r.get("needs_review"):
        s += f"  ! {r.get('review_reason') or 'flagged'}"
    return s


def _find_lab(cur, key: str, what: str, lo: Optional[date], hi: Optional[date], limit: int) -> Optional[str]:
    """Lab and medication selectors of find(health=...). Returns None if `what` is not one of them."""
    raw = what.strip()
    if key in ("reports", "lab reports", "labs"):
        where = "(%s::date IS NULL OR p.collected_date >= %s::date) AND (%s::date IS NULL OR p.collected_date <= %s::date)"
        cur.execute(f"SELECT count(*) AS n FROM lab_reports p WHERE {where}", (lo, lo, hi, hi))
        total = cur.fetchone()["n"]
        cur.execute(f"""SELECT p.*, count(r.id) AS n, count(r.id) FILTER (WHERE r.flag IN ('L','H','A')) AS nf
                        FROM lab_reports p LEFT JOIN lab_results r ON r.report_id = p.id WHERE {where}
                        GROUP BY p.id ORDER BY p.collected_date DESC, p.id DESC LIMIT 50""", (lo, lo, hi, hi))
        rows = cur.fetchall()
        return (f"{len(rows)} of {total} report(s), newest first (show one with 'R<n>'):\n" + "\n".join(_report_line(r) for r in rows)) \
            if rows else "No lab or imaging reports" + (" in that period." if lo or hi else " stored yet.")
    m = re.fullmatch(r"r(\d+)", key)
    if m:
        cur.execute("SELECT * FROM lab_reports WHERE id=%s", (int(m[1]),))
        p = cur.fetchone()
        if not p:
            return f"No report R{m[1]}."
        cur.execute("""SELECT r.*, t.name AS test FROM lab_results r JOIN lab_tests t ON t.id=r.test_id
                       WHERE r.report_id=%s ORDER BY r.id""", (p["id"],))
        res = cur.fetchall()
        z = ZoneInfo(p["tz"])
        lines = [f"R{p['id']} {p['report_type']} | {p['title']} | {p['provider']}" + "".join(f", {x}" for x in (p["department"], p["city"], p["country"]) if x),
                 f"Collected {p['collected_date'].strftime('%d.%m.%Y')}" + (p["collected_at"].astimezone(z).strftime(" %H:%M") if p["collected_time_known"] else " (date only)")
                 + f" {p['tz']}" + (f" | reported {p['reported_at'].astimezone(z).strftime('%d.%m.%Y %H:%M' if p['reported_time_known'] else '%d.%m.%Y')}"
                                    if p["reported_at"] else "")
                 + (f" | fasting: {p['fasting_status']}" if p["fasting_status"] else "") + (f" | status: {p['report_status']}" if p["report_status"] else ""),
                 " | ".join(x for x in (f"ordered by {p['ordering_doctor']}" if p["ordering_doctor"] else "",
                                        f"signed by {p['signing_doctors']}" if p["signing_doctors"] else "",
                                        f"accession {p['accession_no']}" if p["accession_no"] else "") if x) or "-",
                 f"Source: {p['source']}" + (f" ({p['source_detail']})" if p["source_detail"] else "")
                 + (f", {p['document_role']}" if p["document_role"] else "") + f" | raw #{p['raw_id']}"]
        if p["notes"]:
            lines.append(f"Notes: {p['notes']}")
        if p["needs_review"]:
            lines.append(f"! needs review: {p['review_reason'] or 'flagged'}")
        nf = sum(1 for r in res if r["flag"] in ("L", "H", "A"))
        lines.append(f"{len(res)} value(s), {nf} flagged:")
        lines += [_result_line(r) for r in res]
        return "\n".join(lines)
    if key in ("lab flags", "flags", "lab flagged"):
        if not (lo or hi):
            cur.execute("SELECT max(collected_date) AS d FROM lab_reports WHERE report_type='lab'")
            d = cur.fetchone()["d"]
            if not d:
                return "No lab reports stored yet."
            lo = hi = d
        cur.execute("""SELECT r.*, t.name AS test, p.collected_date FROM lab_results r JOIN lab_tests t ON t.id=r.test_id
                       JOIN lab_reports p ON p.id=r.report_id
                       WHERE r.flag IN ('L','H','A') AND p.collected_date BETWEEN %s AND %s
                       ORDER BY p.collected_date DESC, r.id LIMIT %s""", (lo or date(1900, 1, 1), hi or date(2999, 1, 1), max(limit, 50)))
        rows = cur.fetchall()
        span = lo.strftime("%d.%m.%Y") if lo == hi else f"{lo.strftime('%d.%m.%Y') if lo else '...'}-{hi.strftime('%d.%m.%Y') if hi else '...'}"
        return (f"{len(rows)} flagged value(s) {span} (against each lab's own range):\n"
                + "\n".join(_result_line(r, with_date=lo != hi) for r in rows)) if rows else f"No flagged lab values {span}."
    if key in ("lab tests", "tests"):
        rows, _, _ = _lab_tests(cur)
        out = []
        for cat in ("lab", "imaging"):
            out.append(f"{cat}: " + "; ".join(r["name"] + (f" ({r['unit']})" if r["unit"] else "") for r in rows if r["category"] == cat))
        return "\n".join(out)
    if key in ("med list", "medication list", "medications", "medications list", "drugs", "drug list"):
        cur.execute("SELECT * FROM medications ORDER BY (stop_date IS NOT NULL AND stop_date <= current_date), start_date NULLS LAST, id")
        rows = cur.fetchall()
        if not rows:
            return "No medication courses stored."
        out = ["Medication courses (change with D<n>):"]
        for r in rows:
            active = r["stop_date"] is None or r["stop_date"] > date.today()
            s = f"{'active ' if active else 'stopped'} {_course_text(r)}"
            s += "".join(f" | {x}" for x in (r["drug_class"], f"for {r['indication']}" if r["indication"] else None) if x)
            s += " | " + (f"from {r['start_date'].strftime('%d.%m.%Y')}" if r["start_date"] else "start unknown")
            s += f" to {r['stop_date'].strftime('%d.%m.%Y')}" if r["stop_date"] else ""
            s += f" ({r['stop_reason']})" if r["stop_reason"] else ""
            s += f"  ! {r['review_reason'] or 'flagged'}" if r["needs_review"] else ""
            out.append(s)
        return "\n".join(out)
    m = re.fullmatch(r"(?:lab|labs|test)\s+(.+)", raw, re.I)
    if not m:
        return None
    q = m[1].strip()
    rows, exact, loose = _lab_tests(cur)
    t = _find_test(exact, loose, q)
    if not t:
        qk = _tkey(q)
        hits = [r for r in rows if qk in _tkey(r["name"]) or any(qk in _tkey(a) for a in (r["aliases"] or []))]
        if len(hits) == 1:
            t = hits[0]
        elif hits:
            return f"Several tests match '{q}': " + "; ".join(r["name"] for r in hits[:15]) + ". Ask which one, then use 'lab <name>'."
        else:
            close = _close_tests(rows, q, 5)
            return f"No lab test matches '{q}'." + (f" Closest: {', '.join(close)}." if close else "") + " 'lab tests' lists all names."
    cur.execute("""SELECT r.*, p.collected_date, p.provider, p.city, p.id AS rid FROM lab_results r JOIN lab_reports p ON p.id=r.report_id
                   WHERE r.test_id=%s AND (%s::date IS NULL OR p.collected_date >= %s::date) AND (%s::date IS NULL OR p.collected_date <= %s::date)
                   ORDER BY p.collected_date, r.id""", (t["id"], lo, lo, hi, hi))
    res = cur.fetchall()
    if not res:
        return f"No values for {t['name']}" + (" in that period." if lo or hi else ".")
    shown = res[-limit:] if len(res) > limit else res
    head = (f"{t['name']}" + (f" ({t['unit']})" if t["unit"] else "") + f": {len(res)} value(s) {res[0]['collected_date'].strftime('%d.%m.%Y')}"
            f"-{res[-1]['collected_date'].strftime('%d.%m.%Y')}" + (f", newest {limit} shown" if len(res) > limit else "")
            + ". Flags are against each lab's own range.")
    lines = [head]
    if t["compare_note"]:
        lines.append(f"Comparability: {t['compare_note']}")
    for r in shown:
        s = f"{r['collected_date'].strftime('%d.%m.%Y')} {_val_text(r)}"
        if r["body_site"] or r["side"]:
            s += f" ({', '.join(x for x in (r['body_site'], r['side']) if x)})"
        if r["value"] is not None or r["ref_low"] is not None or r["ref_high"] is not None:
            s += f" [{_ref_text(r['ref_low'], r['ref_high'])}]"
        s += f" {r['flag']}" if r["flag"] else ""
        s += f" | {_short(r['provider'], 28)}" + (f", {r['city']}" if r["city"] else "") + f" | V{r['id']} R{r['rid']}"
        if r["method"]:
            s += f" | {r['method']}"
        if r["note"]:
            s += f" | note: {_short(r['note'], 110)}"
        if r["needs_review"]:
            s += f"  ! {r['review_reason'] or 'flagged'}"
        lines.append(s)
    foot = _med_footer(cur, res[0]["collected_date"])
    if foot:
        lines.append(foot)
    return "\n".join(lines)


_REPORT_FIELDS = {"title": "title", "provider": "provider", "department": "department", "city": "city", "country": "country",
                  "accession": "accession_no", "ordered_by": "ordering_doctor", "signed_by": "signing_doctors",
                  "fasting": "fasting_status", "status": "report_status", "notes": "notes", "type": "report_type",
                  "needs_review": "needs_review", "review_reason": "review_reason"}


_RESULT_TEXT = {"panel": "panel", "site": "body_site", "side": "side", "specimen": "specimen", "method": "method",
                "note": "note", "printed_name": "name_printed"}


_MED_FIELDS = {"name", "ingredient", "dose", "frequency", "drug_class", "indication", "reason", "note", "start", "stop",
               "needs_review", "review_reason"}


def _set_clause(cols: dict) -> tuple[str, list]:
    return ", ".join(f"{k} = %s" for k in cols), list(cols.values())   # keys come from the fixed maps above


def _change_report(cur, rid: int, fields: dict, delete: bool, now: datetime) -> str:
    cur.execute("SELECT * FROM lab_reports WHERE id=%s", (rid,))
    p = cur.fetchone()
    if not p:
        raise Reject(f"report R{rid} does not exist")
    if delete:
        if fields:
            raise Reject("delete and fields cannot be combined")
        if p["extractor_version"] != LAB_VERSION or p["logged_at"] < now - timedelta(hours=24):
            raise Reject(f"R{rid} is imported or older than 24 hours; delete it in NocoDB if it really must go (its values go with it)")
        cur.execute("SELECT count(*) AS n FROM lab_results WHERE report_id=%s", (rid,))
        n = cur.fetchone()["n"]
        cur.execute("DELETE FROM lab_reports WHERE id=%s", (rid,))
        return f"OK deleted R{rid} and its {n} value(s). The raw message stays."
    cols: dict = {}
    for k, v in fields.items():
        if k in ("collected", "reported"):
            continue
        if k not in _REPORT_FIELDS:
            raise Reject(f"field '{k}' cannot be changed on R{rid} (allowed: {', '.join(sorted(list(_REPORT_FIELDS) + ['collected', 'reported']))})")
        col = _REPORT_FIELDS[k]
        if k == "type" and v not in ("lab", "imaging"):
            raise Reject("type must be lab or imaging")
        if k == "needs_review":
            if not isinstance(v, bool):
                raise Reject("'needs_review' must be true or false")
            if v is False and "review_reason" not in fields:
                cols["review_reason"] = None
        elif k in ("title", "provider") and (not isinstance(v, str) or not v.strip()):
            raise Reject(f"'{k}' must be a non-empty text")
        elif v is not None and not isinstance(v, str):
            raise Reject(f"'{k}' must be text or null")
        cols[col] = v
    notes = []
    if "collected" in fields:
        c = _report_time(str(fields["collected"] or ""), p["tz"], now, "collection date", hard=True)
        if not c:
            raise Reject("the collection date cannot be empty")
        cols.update(collected_at=c[0], collected_date=c[1], collected_time_known=c[2])
        notes.append(f"collected set to {c[1].strftime('%d.%m.%Y')}" + (c[0].strftime(" %H:%M") if c[2] else " (date only)"))
    if "reported" in fields:
        r = _report_time(str(fields["reported"] or ""), p["tz"], now, "report date", hard=True) if fields["reported"] else None
        cols.update(reported_at=r[0] if r else None, reported_time_known=bool(r and r[2]))
    if not cols:
        raise Reject("nothing to change")
    sets, vals = _set_clause(cols)
    cur.execute(f"UPDATE lab_reports SET {sets} WHERE id=%s", vals + [rid])
    return f"OK changed R{rid}: {', '.join(sorted(cols))}" + "".join(f"\nnote: {n}" for n in notes)


def _change_result(cur, vid: int, fields: dict, delete: bool) -> str:
    cur.execute("SELECT r.*, t.name AS test, t.unit AS test_unit FROM lab_results r JOIN lab_tests t ON t.id=r.test_id WHERE r.id=%s", (vid,))
    r = cur.fetchone()
    if not r:
        raise Reject(f"lab value V{vid} does not exist")
    if delete:
        if fields:
            raise Reject("delete and fields cannot be combined")
        cur.execute("DELETE FROM lab_results WHERE id=%s", (vid,))
        return f"OK deleted V{vid} ({r['test']} {_val_text(r)}). The report R{r['report_id']} and the raw message stay."
    allowed = {"test", "value", "unit", "ref", "ref_low", "ref_high", "abnormal", "calculated", "needs_review", "review_reason"} | set(_RESULT_TEXT)
    for k in fields:
        if k not in allowed:
            raise Reject(f"field '{k}' cannot be changed on V{vid} (allowed: {', '.join(sorted(allowed))})")
    cols: dict = {}
    test = {"id": r["test_id"], "name": r["test"], "unit": r["test_unit"]}
    if "test" in fields:
        rows, exact, loose = _lab_tests(cur)
        t = _find_test(exact, loose, str(fields["test"] or ""))
        if not t:
            close = _close_tests(rows, str(fields["test"] or ""))
            raise Reject(f"unknown test '{fields['test']}'" + (f" (closest: {', '.join(close)})" if close else "") + "; use an existing name")
        test = {"id": t["id"], "name": t["name"], "unit": t["unit"]}
        cols["test_id"] = t["id"]
    for k, col in _RESULT_TEXT.items():
        if k in fields:
            v = fields[k]
            if v is not None and not isinstance(v, str):
                raise Reject(f"'{k}' must be text or null")
            if k == "side" and v not in (None, "left", "right", "both"):
                raise Reject("side must be left, right, both or null")
            cols[col] = v
    if "calculated" in fields:
        if fields["calculated"] not in (True, False, None):
            raise Reject("'calculated' must be true, false or null")
        cols["is_calculated"] = fields["calculated"]
    renorm = {"test", "value", "unit", "ref", "ref_low", "ref_high", "abnormal"} & set(fields)
    norm = None
    if renorm:
        for k in ("ref_low", "ref_high"):
            if fields.get(k) is not None and (isinstance(fields[k], bool) or not isinstance(fields[k], (int, float))):
                raise Reject(f"'{k}' must be a number")
        if "value" in fields and (fields["value"] is None or not str(fields["value"]).strip()):
            raise Reject("'value' cannot be empty")
        value = str(fields["value"]) if "value" in fields else \
            ((r["qualifier"] or "") + r["value_raw"] if r["value_raw"] is not None else (r["value_text"] or _lnum(r["value"])))
        unit = fields["unit"] if "unit" in fields else (r["unit_raw"] if r["unit_raw"] is not None else r["unit"])
        ref = fields["ref"] if "ref" in fields else r["ref_range_raw"]
        lo = fields.get("ref_low")
        hi = fields.get("ref_high")
        keep = None
        if lo is None and hi is None and "ref" not in fields and "unit" not in fields and "test" not in fields:
            keep = (r["ref_low"], r["ref_high"])           # same unit: the stored normal band stays valid
        abnormal = fields["abnormal"] if "abnormal" in fields else (True if r["flag"] == "A" else (False if r["value_text"] and r["flag"] == "N" else None))
        norm = _lab_value(test, value, unit, ref, lo, hi, abnormal, std_bounds=keep)
        for c in ("value", "unit", "value_text", "value_raw", "unit_raw", "qualifier", "conversion", "ref_range_raw", "ref_low", "ref_high", "flag"):
            cols[c] = norm[c]
        if norm["review"]:
            cols.update(needs_review=True, review_reason=norm["review"])
    if "needs_review" in fields:
        if not isinstance(fields["needs_review"], bool):
            raise Reject("'needs_review' must be true or false")
        cols["needs_review"] = fields["needs_review"]
        if not fields["needs_review"] and "review_reason" not in fields:
            cols["review_reason"] = None
    if "review_reason" in fields:
        cols["review_reason"] = fields["review_reason"]
    if not cols:
        raise Reject("nothing to change")
    sets, vals = _set_clause(cols)
    cur.execute(f"UPDATE lab_results SET {sets} WHERE id=%s", vals + [vid])
    msg = f"OK changed V{vid}: {', '.join(sorted(cols))}"
    if norm:
        msg += f"\nnow: {test['name']} {_val_text(norm)} [{_ref_text(norm['ref_low'], norm['ref_high'])}] {norm['flag'] or ''}".rstrip()
        msg += "".join(f"\nnote: {w}" for w in norm["warn"]) + (f"\nnote: converted {norm['conversion']}" if norm["conversion"] else "")
    return msg


def _change_med(cur, did: int, fields: dict, delete: bool, now: datetime) -> str:
    cur.execute("SELECT * FROM medications WHERE id=%s", (did,))
    r = cur.fetchone()
    if not r:
        raise Reject(f"medication course D{did} does not exist")
    if delete:
        if fields:
            raise Reject("delete and fields cannot be combined")
        cur.execute("DELETE FROM medications WHERE id=%s", (did,))
        return f"OK deleted {_course_text(r)}. The raw message stays."
    cols: dict = {}
    for k, v in fields.items():
        if k not in _MED_FIELDS:
            raise Reject(f"field '{k}' cannot be changed on D{did} (allowed: {', '.join(sorted(_MED_FIELDS))})")
        if k in ("start", "stop"):
            if v is None or _tkey(str(v)) in ("unknown", "none", "null", ""):
                cols[f"{k}_date"] = None
            else:
                w = parse_when(str(v), now)
                if not w:
                    raise Reject(f"could not read the {k} date '{v}'")
                cols[f"{k}_date"] = w[0].date()
        elif k == "needs_review":
            if not isinstance(v, bool):
                raise Reject("'needs_review' must be true or false")
            cols[k] = v
            if v is False and "review_reason" not in fields:
                cols["review_reason"] = None
        elif k == "name" and (not isinstance(v, str) or not v.strip()):
            raise Reject("'name' must be a non-empty text")
        elif v is not None and not isinstance(v, str):
            raise Reject(f"'{k}' must be text or null")
        else:
            cols["stop_reason" if k == "reason" else k] = v
    if not cols:
        raise Reject("nothing to change")
    st = cols.get("start_date", r["start_date"])
    sp = cols.get("stop_date", r["stop_date"])
    if st and sp and sp < st:
        raise Reject(f"stop {sp.strftime('%d.%m.%Y')} lies before start {st.strftime('%d.%m.%Y')}")
    if cols.get("start_date") and r["needs_review"] and (r["review_reason"] or "").startswith("start date not") and "needs_review" not in fields:
        cols.update(needs_review=False, review_reason=None)
    sets, vals = _set_clause(cols)
    cur.execute(f"UPDATE medications SET {sets} WHERE id=%s RETURNING *", vals + [did])
    n = cur.fetchone()
    return (f"OK changed D{did}: {', '.join(sorted(cols))}\nnow: {_course_text(n)}, "
            + (f"from {n['start_date'].strftime('%d.%m.%Y')}" if n["start_date"] else "start unknown")
            + (f" to {n['stop_date'].strftime('%d.%m.%Y')}" if n["stop_date"] else "") + ("  ! " + (n["review_reason"] or "flagged") if n["needs_review"] else ""))


def _bound(text: Optional[str], end: bool) -> Optional[datetime]:
    if not text:
        return None
    w = parse_when(text, datetime.now(BERLIN))
    if not w:
        raise Reject(f"could not read the date '{text}'")
    dt, date_only = w
    if date_only:
        d = dt.date()
        return datetime.combine(d, time(23, 59, 59) if end else time(0, 0), tzinfo=BERLIN)
    return dt


def _line(r: dict) -> str:
    kind = (r["thought_type"] or "unsorted") + (f"/{r['next_step']}" if r.get("next_step") else "")
    s = f"#{r['id']} [{kind} | {r['area'] or '-'} | {r['stage']}] {_short(r['summary'], 110)}"
    if r.get("tags"):
        s += f"  ({', '.join(r['tags'])})"
    if r.get("needs_review"):
        s += f"  ! {r.get('review_reason') or 'needs review'}"
    return s


def _find(cur, thought_id, query, tag, area, thought_type, stage, project, needs_review, unreviewed_links,
          since, until, limit) -> str:
    limit = max(1, min(int(limit or 20), 50))
    if thought_id is not None:
        s = str(thought_id).strip().lstrip("#")
        if s[:1].lower() == "p":
            raise Reject("private entries are only searched with private=true")
        if not s.isdigit():
            raise Reject(f"bad thought id '{thought_id}'")
        return _detail(cur, int(s))

    if unreviewed_links:
        cur.execute(
            """SELECT l.id, l.from_id, l.to_id, l.link_type, l.added_by, a.summary AS fs, b.summary AS ts
               FROM thought_links l JOIN thoughts a ON a.id=l.from_id JOIN thoughts b ON b.id=l.to_id
               WHERE NOT l.reviewed ORDER BY l.id LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
        if not rows:
            return "No unreviewed links."
        lines = [f"l{r['id']}: #{r['from_id']} {r['link_type']} #{r['to_id']} [{r['added_by']}]  "
                 f"{_short(r['fs'], 40)}  ->  {_short(r['ts'], 40)}" for r in rows]
        return f"{len(rows)} unreviewed link(s) (confirm_links in change marks them checked):\n" + "\n".join(lines)

    where, params = ["TRUE"], []
    if query:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            """(to_tsvector('english', coalesce(t.summary,'')||' '||coalesce(t.original_text,'')||' '||coalesce(array_to_string(t.tags,' '),''))
                  @@ websearch_to_tsquery('english', %s)
               OR t.summary ILIKE %s OR t.original_text ILIKE %s OR %s = ANY(t.tags)
               OR EXISTS (SELECT 1 FROM thought_updates u WHERE u.thought_id=t.id AND u.update_text ILIKE %s))"""
        )
        params += [query, like, like, query, like]
    if tag:
        where.append("%s = ANY(t.tags)"); params.append(tag)
    if area:
        where.append("t.area = %s"); params.append(area)
    if thought_type:
        where.append("t.thought_type = %s"); params.append(thought_type)
    if stage:
        if stage == "open":
            where.append("t.stage IN ('inbox','active')")
        elif stage in STAGES:
            where.append("t.stage = %s"); params.append(stage)
        else:
            raise Reject(f"unknown stage '{stage}'")
    else:
        where.append("t.stage <> 'dropped'")
    if needs_review is not None:
        where.append("t.needs_review = %s"); params.append(needs_review)
    if project:
        p = _match_project(cur, project)
        if not p:
            return f"No project matches '{project}'."
        where.append("(t.id = %s OR t.id IN (SELECT from_id FROM thought_links WHERE to_id=%s AND link_type='part_of'))")
        params += [p["id"], p["id"]]
    lo, hi = _bound(since, False), _bound(until, True)
    if lo:
        where.append("coalesce(t.event_at, t.logged_at) >= %s"); params.append(lo)
    if hi:
        where.append("coalesce(t.event_at, t.logged_at) <= %s"); params.append(hi)

    wsql = " AND ".join(where)
    cur.execute(f"SELECT count(*) AS n FROM thoughts t WHERE {wsql}", params)
    total = cur.fetchone()["n"]
    order = "coalesce(t.event_at, t.logged_at) ASC, t.id ASC" if (lo or hi) else "t.id DESC"
    cur.execute(
        f"""SELECT t.id, t.summary, t.thought_type, t.next_step, t.area, t.stage, t.tags, t.needs_review,
                   t.review_reason, coalesce(t.event_at, t.logged_at) AS at
            FROM thoughts t WHERE {wsql} ORDER BY {order} LIMIT %s""",
        params + [limit],
    )
    rows = cur.fetchall()
    if not rows:
        return "No matching thoughts."
    show_date = bool(lo or hi)
    lines = [(fmt(r["at"]) + "  " if show_date else "") + _line(r) for r in rows]
    return f"{len(rows)} of {total} thought(s):\n" + "\n".join(lines)


def _detail(cur, tid: int) -> str:
    cur.execute("SELECT * FROM thoughts WHERE id=%s", (tid,))
    t = cur.fetchone()
    if not t:
        return f"No thought #{tid}."
    kind = (t["thought_type"] or "unsorted") + (f"/{t['next_step']}" if t["next_step"] else "")
    lines = [f"#{tid} {kind} | {t['area'] or 'no area'} | stage {t['stage']}",
             f"Summary: {t['summary']}",
             f"Original: {t['original_text']}"]
    if t["tags"]:
        lines.append("Tags: " + ", ".join(t["tags"]))
    if t["aliases"]:
        lines.append("Aliases: " + ", ".join(t["aliases"]))
    lines.append(f"Event: {fmt(t['event_at'])} | Logged: {fmt(t['logged_at'])} | Source: {t['source']}"
                 + (f" ({t['source_detail']})" if t["source_detail"] else "")
                 + (f" | confidence {t['confidence']:.2f}" if t["confidence"] is not None else "")
                 + f" | raw #{t['raw_id']}")
    if t["needs_review"]:
        lines.append(f"! needs review: {t['review_reason'] or 'flagged'}")
    cur.execute("SELECT id, update_text, event_at, logged_at FROM thought_updates WHERE thought_id=%s ORDER BY coalesce(event_at, logged_at), id", (tid,))
    ups = cur.fetchall()
    if ups:
        lines.append("Updates:")
        lines += [f"  u{u['id']} {fmt(u['event_at'] or u['logged_at'])}: {u['update_text']}" for u in ups]
    cur.execute(
        """SELECT l.id, l.link_type, l.added_by, l.reviewed, l.to_id AS other, b.summary AS s, 'out' AS dir
           FROM thought_links l JOIN thoughts b ON b.id=l.to_id WHERE l.from_id=%s
           UNION ALL
           SELECT l.id, l.link_type, l.added_by, l.reviewed, l.from_id, b.summary, 'in'
           FROM thought_links l JOIN thoughts b ON b.id=l.from_id WHERE l.to_id=%s ORDER BY 1""",
        (tid, tid),
    )
    for r in cur.fetchall():
        arrow = f"{r['link_type']} -> #{r['other']}" if r["dir"] == "out" else f"#{r['other']} {r['link_type']} -> this"
        lines.append(f"Link l{r['id']}: {arrow} ({_short(r['s'], 45)}) [{r['added_by']}{'' if r['reviewed'] else ', unreviewed'}]")
    return "\n".join(lines)


def _find_private(cur, thought_id, query, tag, area, thought_type, limit) -> str:
    limit = max(1, min(int(limit or 20), 50))
    where, params = ["TRUE"], []
    if thought_id is not None:
        s = str(thought_id).strip().lstrip("#")
        s = s[1:] if s[:1].lower() == "p" else s
        if not s.isdigit():
            raise Reject(f"bad private id '{thought_id}'")
        where.append("id = %s"); params.append(int(s))
    if query:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(content ILIKE %s OR summary ILIKE %s OR %s = ANY(tags))"); params += [like, like, query]
    if tag:
        where.append("%s = ANY(tags)"); params.append(tag)
    if area:
        where.append("area = %s"); params.append(area)
    if thought_type:
        where.append("thought_type = %s"); params.append(thought_type)
    cur.execute(
        f"""SELECT id, received_at, label, content, summary, thought_type, area, tags, event_at
            FROM private.entries WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s""",
        params + [limit],
    )
    rows = cur.fetchall()
    if not rows:
        return "No matching private entries."
    out = []
    for r in rows:
        out.append(f"P{r['id']} [{r['label']} | {r['thought_type'] or '-'} | {r['area'] or '-'}] "
                   f"logged {fmt(r['received_at'])}" + (f", event {fmt(r['event_at'])}" if r["event_at"] else "")
                   + (f"\n  summary: {r['summary']}" if r["summary"] else "")
                   + (f"\n  tags: {', '.join(r['tags'])}" if r["tags"] else "")
                   + f"\n  full text: {r['content']}")
    return (f"{len(rows)} private entr{'y' if len(rows) == 1 else 'ies'}:\n" + "\n".join(out)
            + "\n(Shown in full because the user asked. Suggest doing this in a separate chat that can be deleted afterwards.)")


_EDITABLE = {"summary", "thought_type", "next_step", "area", "tags", "aliases", "stage", "when", "source",
             "source_detail", "needs_review", "review_reason", "confidence"}


_PRIVATE_EDITABLE = {"summary", "thought_type", "area", "tags", "when"}


_NULLABLE = {"thought_type", "next_step", "area", "source_detail", "review_reason", "confidence", "when"}


_ENUMS = {"thought_type": THOUGHT_TYPES, "next_step": NEXT_STEPS, "area": AREAS, "stage": STAGES, "source": SOURCES}


def _clean_fields(fields: dict, allowed: set, now: datetime, notes: list[str]) -> dict:
    cols: dict = {}
    for k, v in fields.items():
        if k not in allowed:
            raise Reject(f"field '{k}' cannot be changed (allowed: {', '.join(sorted(allowed))})")
        if v is None:
            if k not in _NULLABLE:
                raise Reject(f"field '{k}' cannot be cleared")
            cols["event_at" if k == "when" else k] = None
            continue
        if k in _ENUMS and v not in _ENUMS[k]:
            raise Reject(f"'{v}' is not a valid {k} (use: {', '.join(sorted(_ENUMS[k]))})")
        if k in ("tags", "aliases"):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise Reject(f"'{k}' must be a list of strings")
        elif k == "needs_review":
            if not isinstance(v, bool):
                raise Reject("'needs_review' must be true or false")
        elif k == "confidence":
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1:
                raise Reject("'confidence' must be a number from 0 to 1")
        elif k == "when":
            w = parse_when(str(v), now)
            if not w:
                raise Reject(f"could not read the time '{v}'")
            cols["event_at"] = w[0]
            notes.append(f"event time set to {fmt(*w)}{' (date only)' if w[1] else ''}")
            continue
        elif not isinstance(v, str) or (k == "summary" and not v.strip()):
            raise Reject(f"'{k}' must be a non-empty text" if k == "summary" else f"'{k}' must be text")
        cols[k] = v
    if cols.get("needs_review") is False and "review_reason" not in cols:
        cols["review_reason"] = None
    return cols


def _change(cur, thought_id, fields, add_links, remove_links, confirm_links, delete, past=None) -> str:
    now = datetime.now(BERLIN)
    out: list[str] = []
    notes: list[str] = []
    if not (fields or add_links or remove_links or confirm_links or delete):
        raise Reject("nothing to do")

    pid = tid = None
    if thought_id is not None:
        s = str(thought_id).strip().lstrip("#")
        sl = s.lower()
        if sl in ("last", "t") or re.fullmatch(r"[fncut]\d+", sl):
            if add_links or remove_links or confirm_links:
                raise Reject("food rows take no links")
            return _change_food(cur, sl, fields, delete, past, now)
        if s[:1].lower() in ("m", "e", "r", "v", "d") and s[1:].isdigit():
            if add_links or remove_links or confirm_links:
                raise Reject("health, lab and medication rows take no links")
            k, n = s[0].lower(), int(s[1:])
            if k == "r":
                return _change_report(cur, n, fields, delete, now)
            if k == "v":
                return _change_result(cur, n, fields, delete)
            if k == "d":
                return _change_med(cur, n, fields, delete, now)
            return _change_health(cur, k, n, fields, delete, now)
        if s[:1].lower() == "p":
            if not s[1:].isdigit():
                raise Reject(f"bad id '{thought_id}'")
            pid = int(s[1:])
        elif s.isdigit():
            tid = int(s)
        else:
            raise Reject(f"bad id '{thought_id}'")
    elif fields or delete:
        raise Reject("thought_id is required to edit fields or delete")

    if pid is not None:
        if add_links or remove_links or confirm_links:
            raise Reject("private entries take no links")
        cur.execute("SELECT id FROM private.entries WHERE id=%s", (pid,))
        if not cur.fetchone():
            raise Reject(f"private entry P{pid} does not exist")
        if delete:
            if fields:
                raise Reject("delete and fields cannot be combined")
            cur.execute("DELETE FROM private.entries WHERE id=%s", (pid,))
            return f"OK deleted P{pid}."
        cols = _clean_fields(fields, _PRIVATE_EDITABLE, now, notes)
        _update(cur, "private.entries", pid, cols)
        return f"OK changed P{pid}: {', '.join(sorted(cols))}." + "".join(f"\nnote: {n}" for n in notes)

    if tid is not None:
        row = _thought_row(cur, tid)
        if not row:
            raise Reject(f"thought #{tid} does not exist")
        if delete:
            if fields:
                raise Reject("delete and fields cannot be combined")
            return _delete_thought(cur, row)
        if fields:
            cols = _clean_fields(fields, _EDITABLE, now, notes)
            _update(cur, "thoughts", tid, cols)
            out.append(f"OK changed #{tid}: {', '.join(sorted(cols))}")

    for lk in add_links:
        if lk.from_id is None:
            raise Reject("add_links needs from_id and to_id")
        lid = _add_link(cur, lk.from_id, lk.to_id, lk.link_type, "user" if lk.by_user else "claude", notes)
        if lid:
            out.append(f"OK link l{lid}: #{lk.from_id} {lk.link_type} #{lk.to_id}")
    for lk in remove_links:
        if lk.from_id is None:
            raise Reject("remove_links needs from_id and to_id")
        a, b = (lk.to_id, lk.from_id) if lk.link_type == "related" and lk.from_id > lk.to_id else (lk.from_id, lk.to_id)
        cur.execute("DELETE FROM thought_links WHERE from_id=%s AND to_id=%s AND link_type=%s RETURNING id", (a, b, lk.link_type))
        out.append(f"OK removed link #{a} {lk.link_type} #{b}" if cur.fetchone() else f"no such link #{a} {lk.link_type} #{b}")
    if confirm_links:
        cur.execute("UPDATE thought_links SET reviewed=true WHERE id = ANY(%s::bigint[]) RETURNING id", (confirm_links,))
        done = sorted(r["id"] for r in cur.fetchall())
        out.append(f"OK confirmed links: {', '.join('l' + str(i) for i in done) or 'none found'}")
    return "\n".join(out + [f"note: {n}" for n in notes])


_HEALTH_FIELDS = {"m": {"type", "value", "value2", "period", "note", "when", "needs_review", "review_reason", "confirmed"},
                  "e": {"type", "session", "note", "when", "needs_review", "review_reason", "confirmed"}}


def _change_health(cur, kind: str, hid: int, fields: dict, delete: bool, now: datetime) -> str:
    table = "measurements" if kind == "m" else "events"
    ref = f"{kind}{hid}"
    cur.execute(f"SELECT x.*, t.name AS tname FROM {table} x JOIN log_types t ON t.id = x.type_id WHERE x.id = %s", (hid,))
    row = cur.fetchone()
    if not row:
        raise Reject(f"health row {ref} does not exist")
    if delete:
        if fields:
            raise Reject("delete and fields cannot be combined")
        cur.execute(f"DELETE FROM {table} WHERE id = %s", (hid,))
        return f"OK deleted {ref} ({row['tname']}, {fmt(row['event_at'], not row['time_known'])}). The raw message stays."
    f = dict(fields)
    confirmed = bool(f.pop("confirmed", False))
    for k in f:
        if k not in _HEALTH_FIELDS[kind]:
            raise Reject(f"field '{k}' cannot be changed on {ref} (allowed: {', '.join(sorted(_HEALTH_FIELDS[kind]))})")
    _, idx = _types(cur)
    t = idx[_norm(row["tname"])]
    sets: list[str] = []
    params: list = []
    notes: list[str] = []
    if "type" in f:
        nt, _s = _resolve_type(idx, str(f["type"]))
        if not nt:
            raise Reject(f"unknown health type '{f['type']}'")
        if nt["kind"] != t["kind"]:
            raise Reject("a measurement cannot become an event or the other way round")
        t = nt
        sets.append("type_id = %s"); params.append(t["id"])
    ts = row["event_at"]
    if "when" in f:
        w = parse_when(str(f["when"]), now) if f["when"] else None
        if not w:
            raise Reject(f"could not read the time '{f['when']}'")
        ts, date_only = w
        sets += ["event_at = %s", "time_known = %s"]; params += [ts, not date_only]
        notes.append(f"time set to {fmt(*w)}{' (date only)' if w[1] else ''}")
    if "when" in f or "type" in f:
        sets.append("day = %s"); params.append(_day_of(ts, t["name"]))
    problems: list[str] = []
    if kind == "m":
        for k in ("value", "value2"):
            if f.get(k) is not None and (isinstance(f[k], bool) or not isinstance(f[k], (int, float))):
                raise Reject(f"'{k}' must be a number")
        if f.get("value", 0) is None:
            raise Reject("'value' cannot be cleared")
        v1 = float(f["value"]) if "value" in f else float(row["value1"])
        if "value2" in f:
            v2 = None if f["value2"] is None else float(f["value2"])
        else:
            v2 = None if row["value2"] is None else float(row["value2"])
        if {"value", "value2", "type"} & set(f):
            if t["name"] == "bp" and v2 is None:
                raise Reject("bp needs two values (systolic and diastolic)")
            if t["name"] != "bp" and v2 is not None:
                raise Reject(f"{t['name']} takes one value")
            problems = _range_problems(t, v1, v2)
            sets += ["value1 = %s", "value2 = %s"]; params += [Decimal(_num(v1)), None if v2 is None else Decimal(_num(v2))]
        if "period" in f:
            if f["period"] not in (None, "morning", "evening", "fasted"):
                raise Reject("period must be morning, evening, fasted or null")
            sets.append("period = %s"); params.append(f["period"])
    elif "session" in f:
        if t["name"] != "shoonya" or not isinstance(f["session"], int) or not 1 <= f["session"] <= 9:
            raise Reject("session (1-9) applies to shoonya events only")
        sets.append("details = details || %s::jsonb"); params.append(json.dumps({"session": f["session"]}))
    if "note" in f:
        if f["note"] is not None and not isinstance(f["note"], str):
            raise Reject("'note' must be text or null")
        sets.append("note = %s"); params.append(f["note"])
    if "needs_review" in f:
        if not isinstance(f["needs_review"], bool):
            raise Reject("'needs_review' must be true or false")
        sets.append("needs_review = %s"); params.append(f["needs_review"])
        if not f["needs_review"] and "review_reason" not in f:
            sets.append("review_reason = NULL")
    if "review_reason" in f:
        sets.append("review_reason = %s"); params.append(f["review_reason"])
    if problems and not confirmed:
        raise Reject("READ BACK, nothing changed: " + "; ".join(problems)
                     + ". Ask the user, then resend with the corrected value or fields.confirmed=true")
    if problems:
        sets.append("details = details || '{\"confirmed\": \"user\"}'::jsonb")
    if not sets:
        raise Reject("nothing to change")
    cur.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = %s", params + [hid])  # column names above are fixed strings
    cols = sorted({s.split(" = ")[0].split(" ")[0] for s in sets})
    return f"OK changed {ref}: {', '.join(cols)}" + "".join(f"\nnote: {n}" for n in notes)


def _update(cur, table: str, rid: int, cols: dict) -> None:
    if not cols:
        raise Reject("nothing to change")
    sets = ", ".join(f"{k} = %s" for k in cols)  # keys are validated against fixed whitelists
    cur.execute(f"UPDATE {table} SET {sets} WHERE id = %s", list(cols.values()) + [rid])


def _delete_thought(cur, row: dict) -> str:
    tid = row["id"]
    cur.execute("SELECT to_id FROM thought_links WHERE from_id=%s AND link_type='completes'", (tid,))
    targets = [r["to_id"] for r in cur.fetchall()]
    cur.execute("SELECT count(*) AS n FROM thought_links WHERE from_id=%s OR to_id=%s", (tid, tid))
    nlinks = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM thought_updates WHERE thought_id=%s", (tid,))
    nups = cur.fetchone()["n"]
    members = 0
    if row["thought_type"] == "project":
        cur.execute("SELECT count(*) AS n FROM thought_links WHERE to_id=%s AND link_type='part_of'", (tid,))
        members = cur.fetchone()["n"]
    cur.execute("DELETE FROM thoughts WHERE id=%s", (tid,))  # links and updates cascade
    msg = [f"OK deleted #{tid} ({_short(row['summary'], 60)}); {nlinks} link(s) and {nups} update(s) went with it. The raw message stays."]
    for t in targets:
        cur.execute(
            """UPDATE thoughts SET stage='active' WHERE id=%s AND stage='done'
               AND NOT EXISTS (SELECT 1 FROM thought_links WHERE to_id=%s AND link_type='completes') RETURNING id""",
            (t, t),
        )
        if cur.fetchone():
            msg.append(f"reopened #{t} (it had been closed by the deleted entry)")
    if members:
        msg.append(f"{members} thought(s) were part of this project and are no longer linked to it")
    return "\n".join(msg)


class Ask(Reject):
    """The user must decide first (for example: only from now, or also the past). Nothing is changed."""


_ENTRY_FIELDS = {"amount", "unit", "unit_g", "food", "when", "day", "meal", "note", "estimated", "needs_review", "review_reason", "drink"}


_FOOD_TEXT = {"source_note", "brand", "barcode", "category"}


_FOOD_FIELDS = set(_NUT) | _FOOD_TEXT | {"name", "aliases", "add_aliases", "remove_aliases", "source", "state", "variant_of", "carbs_include_fibre", "active",
                                         "needs_review", "review_reason", "default", "units", "drink"}


_TARGET_COLS = ("kcal", "protein", "fat", "sat_fat", "carbs", "fibre", "sugar_added", "sodium_mg")


def _fnum(v, what: str, lo: float = 0, hi: Optional[float] = None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Reject(f"'{what}' must be a number")
    if v < lo or (hi is not None and v > hi):
        raise Reject(f"'{what}' must be between {lo:g} and {hi:g}" if hi is not None else f"'{what}' must be {lo:g} or more")
    return float(v)


def _fbool(v, what: str) -> bool:
    if not isinstance(v, bool):
        raise Reject(f"'{what}' must be true or false")
    return v


def _ftext(v, what: str, nullable: bool = True) -> Optional[str]:
    if v is None:
        if not nullable:
            raise Reject(f"'{what}' cannot be cleared")
        return None
    if not isinstance(v, str) or (not v.strip() and not nullable):
        raise Reject(f"'{what}' must be a non-empty text")
    return v.strip() or None


def _exact_food(cat: dict, term, what: str) -> dict:
    ms, how = _find_foods(cat, str(term))
    if how not in ("id", "name", "alias", "default") or len(ms) != 1 or not ms[0]["active"]:
        cand = "; ".join(_fdesc(m) for m in ms[:5])
        raise Reject(f"{what} '{term}' is not an exact catalogue food" + (f" (candidates: {cand})" if cand else "") + ". Ask, then use the exact name or N-id")
    return ms[0]


def _change_food(cur, sl: str, fields: dict, delete: bool, past: Optional[str], now: datetime) -> str:
    if past not in (None, "yes", "no"):
        raise Reject("past must be 'yes' or 'no'")
    if sl == "last":
        return _cf_last(cur, fields, delete)
    kind, num = sl[0], (int(sl[1:]) if sl[1:].isdigit() else None)
    if kind == "f":
        return _cf_entry(cur, num, fields, delete, now)
    if kind == "n":
        return _cf_food(cur, num, fields, delete, past)
    if kind == "c":
        return _cf_recipe(cur, num, fields, delete, past)
    if kind == "u":
        return _cf_usual(cur, num, fields, delete)
    return _cf_target(cur, num, fields, delete, now)


def _cf_last(cur, fields: dict, delete: bool) -> str:
    if not delete or fields:
        raise Reject("'last' only works with delete=true: it removes all food entries of the newest food message")
    cur.execute("SELECT raw_id FROM food_entries WHERE extractor_version=%s ORDER BY raw_id DESC LIMIT 1", (FOOD_VERSION,))
    r = cur.fetchone()
    if not r:
        raise Reject("there is no food message from the app to undo")
    cur.execute("DELETE FROM food_entries WHERE raw_id=%s RETURNING id, day", (r["raw_id"],))
    rows = cur.fetchall()
    days = sorted({x["day"] for x in rows})
    for d in days:
        cur.execute("SELECT assign_meals(%s)", (d,))
    return (f"OK removed {len(rows)} food entr{'y' if len(rows) == 1 else 'ies'} of raw #{r['raw_id']} "
            f"({', '.join('f' + str(x['id']) for x in rows)}). The message stays in the inbox; other things logged in it (thoughts, health, new foods) stay too.\n"
            + "\n".join(_day_line(cur, d) for d in days))


def _cf_entry(cur, eid: Optional[int], fields: dict, delete: bool, now: datetime) -> str:
    cur.execute("""SELECT e.*, f.name AS fname, f.basis FROM food_entries e JOIN foods f ON f.id=e.food_id WHERE e.id=%s""", (eid,))
    row = cur.fetchone()
    if not row:
        raise Reject(f"food entry f{eid} does not exist")
    if delete:
        if fields:
            raise Reject("delete and fields cannot be combined")
        cur.execute("DELETE FROM food_entries WHERE id=%s", (eid,))
        cur.execute("SELECT assign_meals(%s)", (row["day"],))
        return (f"OK deleted f{eid} ({row['fname']} {_num(row['amount'])} {row['basis']}, {_dm(row['day'])}). The message stays in the inbox.\n"
                + _day_line(cur, row["day"]))
    bad = sorted(set(fields) - _ENTRY_FIELDS)
    if bad:
        raise Reject(f"field(s) {', '.join(bad)} cannot be changed on a food entry (allowed: {', '.join(sorted(_ENTRY_FIELDS))})")
    sets: dict = {}
    notes: list[str] = []
    food = {"id": row["food_id"], "name": row["fname"], "basis": row["basis"]}
    if "food" in fields:
        food = _exact_food(_food_cat(cur), fields["food"], "food")
        sets["food_id"] = food["id"]
    if "amount" in fields:
        qty = _fnum(fields["amount"], "amount", 0)
        amount, portion, est, prob, _nu = _to_amount(cur, food, qty, fields.get("unit"), fields.get("unit_g"))
        if prob:
            raise Reject(prob)
        if not 0 < amount <= 5000:
            raise Reject("the amount must be above 0 and at most 5000")
        sets["amount"] = Decimal(_num(amount))
        sets["portion_text"] = portion or None
        if est:
            sets["estimated"] = True
    elif "unit" in fields or "unit_g" in fields:
        raise Reject("unit goes together with amount")
    if "when" in fields:
        w = parse_when(str(fields["when"]), now)
        if not w:
            raise Reject(f"could not read the time '{fields['when']}'")
        if w[1]:
            sets.update(eaten_at=datetime.combine(w[0].date(), time(12, 0), tzinfo=BERLIN), time_known=False, day=w[0].date(), day_rule="asked")
        else:
            cur.execute("SELECT food_day(%s) AS d", (w[0],))
            d = cur.fetchone()["d"]
            sets.update(eaten_at=w[0], time_known=True)
            if d:
                sets.update(day=d, day_rule="wake")
            else:
                notes.append("no wake-up found before that time, so the day stays " + _dm(row["day"]) + " (set fields.day to move it)")
    if "day" in fields:
        w = parse_when(str(fields["day"]), now)
        if not w:
            raise Reject(f"could not read the day '{fields['day']}'")
        sets.update(day=w[0].date(), day_rule="asked")
    if "drink" in fields:
        sets["is_drink"] = _fbool(fields["drink"], "drink")
        if sets["is_drink"] is False and row["is_drink"] and not row["time_known"]:
            notes.append("no time on this entry: it is meal 1 for now (set fields.meal to move it)")
    if "meal" in fields:
        if sets.get("is_drink") or (row["is_drink"] and sets.get("is_drink") is not False):
            raise Reject("a drink has no meal number (set fields.drink=false first, or change the time)")
        m = int(_fnum(fields["meal"], "meal", 1, 20))
        if row["time_known"] and row["day_rule"] != "import" and "when" not in fields:
            raise Reject("meal numbers of entries with a time follow the 2-hour rule; change the time instead")
        sets["meal"] = m
    for k in ("note", "review_reason"):
        if k in fields:
            sets[k] = _ftext(fields[k], k)
    for k in ("estimated", "needs_review"):
        if k in fields:
            sets[k] = _fbool(fields[k], k)
    if sets.get("needs_review") is False and "review_reason" not in fields:
        sets["review_reason"] = None
    if not sets:
        raise Reject("nothing to change")
    cols = ", ".join(f"{k} = %s" for k in sets)
    cur.execute(f"UPDATE food_entries SET {cols} WHERE id = %s", list(sets.values()) + [eid])
    for d in sorted({row["day"], sets.get("day", row["day"])}):
        cur.execute("SELECT assign_meals(%s)", (d,))
    cur.execute("SELECT * FROM v_food_log WHERE id=%s", (eid,))
    r = cur.fetchone()
    return ("OK changed f%d: %s\n%s\n%s" % (eid, ", ".join(sorted(sets)), _entry_line(r), _day_line(cur, r["day"]))
            + "".join(f"\nnote: {n}" for n in notes))


def _cmp(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-9


def _apply_past(cur, food_id: int, only_unknown: bool, note: str) -> int:
    cur.execute("SELECT apply_food_to_past(%s,%s,%s) AS n", (food_id, only_unknown, note))
    return cur.fetchone()["n"]


def _cf_food(cur, nid: Optional[int], fields: dict, delete: bool, past: Optional[str]) -> str:
    cur.execute("SELECT * FROM foods WHERE id=%s", (nid,))
    row = cur.fetchone()
    if not row:
        raise Reject(f"food N{nid} does not exist")
    if delete:
        raise Reject("foods are never deleted: retire one with fields {active: false} (old entries keep working)")
    bad = sorted(set(fields) - _FOOD_FIELDS)
    if bad:
        raise Reject(f"field(s) {', '.join(bad)} cannot be changed on a food (allowed: {', '.join(sorted(_FOOD_FIELDS))})")
    cur.execute("SELECT id FROM recipes WHERE food_id=%s", (nid,))
    is_recipe = cur.fetchone()
    upd: dict = {}
    for k in _NUT:
        if k in fields:
            if fields[k] is None:
                if k in ("kcal", "protein", "fat", "carbs"):
                    raise Reject(f"{_NUT_LABEL[k]} cannot be cleared")
                upd[k] = None
            else:
                upd[k] = _fnum(fields[k], k, 0)
    if is_recipe and set(upd) & set(_NUT):
        raise Reject(f"N{nid} is calculated from recipe C{is_recipe['id']}: change the recipe (C{is_recipe['id']}) instead")
    if "name" in fields:
        name = _ftext(fields["name"], "name", False)
        if len(name) > 120:
            raise Reject("name is longer than 120 characters")
        cat = _food_cat(cur)
        if any(f["id"] != nid for f in cat["names"].get(_fkey(name), [])):
            raise Reject(f"a food called '{name}' already exists")
        upd["name"] = name
    if "aliases" in fields:
        al = fields["aliases"]
        if not isinstance(al, list) or not all(isinstance(x, str) and x.strip() for x in al):
            raise Reject("aliases must be a list of texts")
        upd["aliases"] = [x.strip() for x in al]
    alias_notes: list[str] = []
    if "add_aliases" in fields or "remove_aliases" in fields:
        if "aliases" in fields:
            raise Reject("use either aliases (replaces all) or add_aliases / remove_aliases, not both")
        upd["aliases"] = _alias_edit(cur, row, fields.get("add_aliases"), fields.get("remove_aliases"), alias_notes)
    for k in _FOOD_TEXT:
        if k in fields:
            upd[k] = _ftext(fields[k], k)
    if "source" in fields:
        if fields["source"] not in ("label", "database", "estimate"):
            raise Reject("source must be label, database or estimate")
        if is_recipe:
            raise Reject("the source of a recipe food stays 'calculated'")
        upd["source"] = fields["source"]
    if "state" in fields:
        if fields["state"] not in (None, "raw", "cooked", "dry"):
            raise Reject("state must be raw, cooked, dry or null")
        upd["state"] = fields["state"]
    if "variant_of" in fields:
        v = fields["variant_of"]
        if v is None:
            upd["variant_of"] = None
        else:
            m = re.fullmatch(r"[nN]?#?(\d+)", str(v).strip())
            if not m or int(m[1]) == nid:
                raise Reject("variant_of must be the N-id of the other variant (raw <-> cooked)")
            cur.execute("SELECT 1 FROM foods WHERE id=%s", (int(m[1]),))
            if not cur.fetchone():
                raise Reject(f"N{m[1]} does not exist")
            upd["variant_of"] = int(m[1])
    for k in ("carbs_include_fibre",):
        if k in fields:
            upd[k] = None if fields[k] is None else _fbool(fields[k], k)
    for k in ("active", "needs_review"):
        if k in fields:
            upd[k] = _fbool(fields[k], k)
    if "drink" in fields:
        upd["is_drink"] = _fbool(fields["drink"], "drink")
    if "review_reason" in fields:
        upd["review_reason"] = _ftext(fields["review_reason"], "review_reason")
    if upd.get("needs_review") is False and "review_reason" not in fields:
        upd["review_reason"] = None
    merged = {**row, **upd}
    if merged["sat_fat"] is not None and float(merged["sat_fat"]) > float(merged["fat"]) + 0.05:
        raise Reject(f"saturated fat {_num(merged['sat_fat'])} would be higher than fat {_num(merged['fat'])}")
    if merged["sugar_total"] is not None and float(merged["sugar_total"]) > float(merged["carbs"]) + 0.05:
        raise Reject(f"total sugar {_num(merged['sugar_total'])} would be higher than carbs {_num(merged['carbs'])}")
    if merged["sugar_added"] is not None and merged["sugar_total"] is not None and float(merged["sugar_added"]) > float(merged["sugar_total"]) + 0.05:
        raise Reject(f"added sugar {_num(merged['sugar_added'])} would be higher than total sugar {_num(merged['sugar_total'])}")
    if "default" in fields:
        terms = fields["default"] if isinstance(fields["default"], list) else [fields["default"]]
        if not all(isinstance(t, str) and _fkey(t) for t in terms):
            raise Reject("default must be a term (or a list of terms) such as 'skyr'")
    if "units" in fields:
        us = fields["units"]
        if not isinstance(us, list) or not all(isinstance(u, dict) and u.get("unit") and isinstance(u.get("grams"), (int, float))
                                               and not isinstance(u.get("grams"), bool) and u["grams"] > 0 for u in us):
            raise Reject("units must be a list like [{\"unit\": \"slice\", \"grams\": 30}]")
    if not upd and "default" not in fields and "units" not in fields:
        raise Reject("nothing to change")

    changed = [k for k in _NUT if k in upd and not _cmp(upd[k], row[k])]
    fills = [k for k in changed if row[k] is None and upd[k] is not None]
    corr = [k for k in changed if k not in fills]
    cur.execute("SELECT count(*) AS n, min(day) AS a, max(day) AS b FROM food_entries WHERE food_id=%s", (nid,))
    use = cur.fetchone()
    if corr and use["n"] and past is None:
        diff = ", ".join(f"{_NUT_LABEL[k]} {_n(row[k], 1)} -> {_n(upd[k], 1)}" for k in corr)
        raise Ask(f"ASK, nothing changed: N{nid} {row['name']} is used by {use['n']} past entr{'y' if use['n'] == 1 else 'ies'} "
                  f"({_dm(use['a'])}-{use['b'].strftime('%d.%m.%Y')}). Change: {diff}. Ask the user: only from now on, or also rewrite the past "
                  "entries (all their nutrient values are recalculated from the food)? Then resend the same call with past=\"no\" or past=\"yes\"")
    out: list[str] = []
    notes: list[str] = []
    real = {k: v for k, v in upd.items() if not (k in row and _cmp_any(row[k], v))}
    if real:
        cols = ", ".join(f"{k} = %s" for k in real)
        cur.execute(f"UPDATE foods SET {cols} WHERE id = %s", list(real.values()) + [nid])
        desc = ", ".join(f"{_NUT_LABEL.get(k, k)} {_show(row[k])} -> {_show(v)}" for k, v in real.items())
        out.append(f"OK changed N{nid} {row['name']}: {desc}")
    if "default" in fields:
        terms = fields["default"] if isinstance(fields["default"], list) else [fields["default"]]
        for t in terms:
            cur.execute("""INSERT INTO food_defaults (term, food_id) VALUES (%s,%s)
                           ON CONFLICT (term) DO UPDATE SET food_id = EXCLUDED.food_id""", (_fkey(t), nid))
        out.append(f"OK default product for {', '.join(repr(_fkey(t)) for t in terms)}: N{nid} {row['name']}")
    if "units" in fields:
        for u in fields["units"]:
            cur.execute("""INSERT INTO food_units (food_id, unit, grams, source) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (food_id, unit) DO UPDATE SET grams = EXCLUDED.grams, source = EXCLUDED.source""",
                        (nid, str(u["unit"]).strip().lower(), Decimal(_num(u["grams"])), "label" if u.get("source") == "label" else "estimate"))
        out.append("OK units: " + ", ".join(f"1 {u['unit']} = {_num(u['grams'])} {row['basis']}" for u in fields["units"]))
    if changed:
        note = "correction applied to the past" if corr and past == "yes" else "unknown values filled"
        if past == "yes":
            n = _apply_past(cur, nid, False, note)
            out.append(f"past entries rewritten from the food: {n}")
        elif fills:
            n = _apply_past(cur, nid, True, note)
            out.append(f"past entries completed (unknown values only): {n}")
            notes.append("completing unknown values also fills other unknown values of this food on those entries (for example caffeine)")
        if corr and past == "no":
            out.append("past entries keep their old values (only from now on)")
        cur.execute("SELECT r.id, r.food_id, r.name FROM recipe_items i JOIN recipes r ON r.id=i.recipe_id WHERE i.food_id=%s GROUP BY r.id", (nid,))
        for rc in cur.fetchall():
            cur.execute("SELECT recalc_recipe(%s)", (rc["id"],))
            out.append(f"recipe C{rc['id']} {rc['name']} recalculated" + ("" if past == "yes" else " (its past entries keep their values)"))
            if past == "yes":
                _apply_past(cur, rc["food_id"], False, "recipe ingredient corrected")
    notes = alias_notes + notes
    if not out and notes:
        out.append(f"No change to N{nid} {row['name']}")
    return "\n".join(out + [f"note: {n}" for n in notes]) or f"No change: N{nid} {row['name']} already has these values."


def _alias_edit(cur, row: dict, adds, rems, notes: list[str]) -> list[str]:
    """New alias list of one food. Nothing is written here; any problem rejects the whole call."""
    def clean(v, what):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise Reject(f"{what} must be a list of texts")
        if any(len(x.strip()) > 60 for x in v):
            raise Reject(f"{what}: an alias is at most 60 characters")
        return [x.strip() for x in v]
    adds, rems = clean(adds, "add_aliases"), clean(rems, "remove_aliases")
    cat = _food_cat(cur)
    new = list(row["aliases"] or [])
    for a in rems:
        k = _fkey(a)
        keep = [x for x in new if _fkey(x) != k]
        if len(keep) == len(new):
            notes.append(f"'{a}' was not an alias of N{row['id']}")
        new = keep
    own = _fkey(row["name"])
    for a in adds:
        k = _fkey(a)
        if not k:
            raise Reject(f"'{a}' has no letters or digits")
        if k == own or k in {_fkey(x) for x in new}:
            notes.append(f"'{a}' is already a name or alias of N{row['id']}")
            continue
        other = [f for f in cat["names"].get(k, []) if f["id"] != row["id"] and f["active"]]
        if other:
            raise Reject(f"'{a}' is the name of N{other[0]['id']} {other[0]['name']}: an alias must not shadow another food")
        other = [f for f in cat["aliases"].get(k, []) if f["id"] != row["id"] and f["active"]]
        if other:
            raise Reject(f"'{a}' is already an alias of N{other[0]['id']} {other[0]['name']}: remove it there first, or use a more specific word")
        d = cat["defaults"].get(k)
        if d and d["id"] != row["id"]:
            raise Reject(f"'{a}' is the default term for N{d['id']} {d['name']}: change that default first")
        new.append(a)
    if len(new) > 20:
        raise Reject(f"N{row['id']} would have {len(new)} aliases (maximum 20)")
    return new


def _cmp_any(a, b) -> bool:
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)) and not isinstance(a, bool) and not isinstance(b, bool):
        return _cmp(a, b)
    return a == b


def _show(v) -> str:
    if v is None:
        return "unknown"
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return _num(v)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) or "none"
    return str(v)


def _items_from_fields(cur, fields: dict, kind: str) -> Optional[list[tuple[int, float]]]:
    if "from_meal" in fields:
        if kind != "usual" or "items" in fields:
            raise Reject("from_meal works for usual meals only, and not together with items")
        its = _meal_spec_items(cur, str(fields["from_meal"]), datetime.now(BERLIN))
        cat = _food_cat(cur)
        for fid, _g in its:
            f = cat["by_id"].get(fid)
            if not f or not f["active"]:
                raise Reject(f"from_meal: {f['name'] if f else 'N' + str(fid)} is retired; give items instead")
        return its
    if "items" not in fields:
        return None
    its = fields["items"]
    if not isinstance(its, list) or not its or not all(isinstance(x, dict) and x.get("food") and isinstance(x.get("grams"), (int, float))
                                                       and not isinstance(x.get("grams"), bool) and x["grams"] > 0 for x in its):
        raise Reject("items must be a non-empty list like [{\"food\": \"Quark\", \"grams\": 250}] (this REPLACES all items)")
    cat = _food_cat(cur)
    out = []
    for x in its:
        f = _exact_food(cat, x["food"], "item")
        if kind == "recipe" and f["id"] in cat["recipes"]:
            raise Reject(f"a recipe cannot contain another recipe ({f['name']})")
        out.append((f["id"], float(x["grams"])))
    return out


def _cf_recipe(cur, cid: Optional[int], fields: dict, delete: bool, past: Optional[str]) -> str:
    cur.execute("SELECT r.*, f.name AS fname FROM recipes r JOIN foods f ON f.id=r.food_id WHERE r.id=%s", (cid,))
    row = cur.fetchone()
    if not row:
        raise Reject(f"recipe C{cid} does not exist")
    if delete:
        if fields:
            raise Reject("delete and fields cannot be combined")
        cur.execute("UPDATE food_entries SET recipe_id=NULL WHERE recipe_id=%s", (cid,))
        cur.execute("DELETE FROM recipes WHERE id=%s", (cid,))
        cur.execute("UPDATE foods SET active=false WHERE id=%s", (row["food_id"],))
        return f"OK deleted recipe C{cid} {row['name']}; its food N{row['food_id']} is retired (old entries keep working)."
    allowed = {"name", "finished_weight_g", "finished_weight", "weight_measured", "note", "items"}
    bad = sorted(set(fields) - allowed)
    if bad:
        raise Reject(f"field(s) {', '.join(bad)} cannot be changed on a recipe (allowed: {', '.join(sorted(allowed))})")
    its = _items_from_fields(cur, fields, "recipe")
    cur.execute("SELECT * FROM foods WHERE id=%s", (row["food_id"],))
    before = cur.fetchone()
    sets: dict = {}
    fw = fields.get("finished_weight_g", fields.get("finished_weight"))
    if fw is not None:
        sets["finished_weight_g"] = Decimal(_num(_fnum(fw, "finished_weight_g", 0.01)))
    if "weight_measured" in fields:
        sets["weight_measured"] = _fbool(fields["weight_measured"], "weight_measured")
    if "note" in fields:
        sets["note"] = _ftext(fields["note"], "note")
    if "name" in fields:
        name = _ftext(fields["name"], "name", False)
        cat = _food_cat(cur)
        if any(f["id"] != row["food_id"] for f in cat["names"].get(_fkey(name), [])):
            raise Reject(f"a food called '{name}' already exists")
        sets["name"] = name
        cur.execute("UPDATE foods SET name=%s WHERE id=%s", (name, row["food_id"]))
    if its is not None:
        cur.execute("DELETE FROM recipe_items WHERE recipe_id=%s", (cid,))
        for n, (fid, g) in enumerate(its, 1):
            cur.execute("INSERT INTO recipe_items (recipe_id, food_id, grams, sort_order) VALUES (%s,%s,%s,%s)", (cid, fid, Decimal(_num(g)), n * 10))
    if sets:
        cols = ", ".join(f"{k} = %s" for k in sets)
        cur.execute(f"UPDATE recipes SET {cols} WHERE id = %s", list(sets.values()) + [cid])
    if not sets and its is None:
        raise Reject("nothing to change")
    cur.execute("SELECT * FROM foods WHERE id=%s", (row["food_id"],))
    after = cur.fetchone()
    diff = [k for k in _NUT if not _cmp(before[k], after[k])]
    cur.execute("SELECT count(*) AS n, min(day) AS a, max(day) AS b FROM food_entries WHERE food_id=%s", (row["food_id"],))
    use = cur.fetchone()
    if diff and use["n"] and past is None:
        raise Ask(f"ASK, nothing changed: recipe C{cid} {row['name']} is used by {use['n']} past entr{'y' if use['n'] == 1 else 'ies'} "
                  f"({_dm(use['a'])}-{use['b'].strftime('%d.%m.%Y')}) and the change alters its values per 100 g ("
                  + ", ".join(f"{_NUT_LABEL[k]} {_n(before[k], 1)} -> {_n(after[k], 1)}" for k in diff)
                  + "). Ask the user: only from now on, or also rewrite the past entries? Then resend with past=\"no\" or past=\"yes\"")
    out = [f"OK changed recipe C{cid} {sets.get('name', row['name'])}" + (f": {', '.join(sorted(sets))}" if sets else "")
           + (f"; {len(its)} items replaced" if its is not None else "")]
    if diff:
        out.append("values per 100 g now: " + _food_line100(after).split(": ", 1)[1])
        if past == "yes":
            out.append(f"past entries rewritten from the recipe: {_apply_past(cur, row['food_id'], False, 'recipe changed')}")
        elif use["n"]:
            out.append("past entries keep their old values (only from now on)")
    return "\n".join(out)


def _cf_usual(cur, uid: Optional[int], fields: dict, delete: bool) -> str:
    cur.execute("SELECT * FROM usual_meals WHERE id=%s", (uid,))
    row = cur.fetchone()
    if not row:
        raise Reject(f"usual meal U{uid} does not exist")
    if delete:
        if fields:
            raise Reject("delete and fields cannot be combined")
        cur.execute("UPDATE food_entries SET usual_meal_id=NULL WHERE usual_meal_id=%s", (uid,))
        cur.execute("DELETE FROM usual_meals WHERE id=%s", (uid,))
        return f"OK deleted usual meal U{uid} {row['name']} (entries logged with it keep their values)."
    bad = sorted(set(fields) - {"name", "aliases", "note", "items", "from_meal", "drink"})
    if bad:
        raise Reject(f"field(s) {', '.join(bad)} cannot be changed on a usual meal (allowed: name, aliases, note, items, from_meal, drink)")
    its = _items_from_fields(cur, fields, "usual")
    sets: dict = {}
    if "name" in fields:
        sets["name"] = _ftext(fields["name"], "name", False)
    if "aliases" in fields:
        al = fields["aliases"]
        if not isinstance(al, list) or not all(isinstance(x, str) and x.strip() for x in al):
            raise Reject("aliases must be a list of texts")
        sets["aliases"] = [x.strip() for x in al]
    if "note" in fields:
        sets["note"] = _ftext(fields["note"], "note")
    if "drink" in fields:
        sets["is_drink"] = _fbool(fields["drink"], "drink")
    if not sets and its is None:
        raise Reject("nothing to change")
    if sets:
        cols = ", ".join(f"{k} = %s" for k in sets)
        cur.execute(f"UPDATE usual_meals SET {cols} WHERE id = %s", list(sets.values()) + [uid])
    if its is not None:
        cur.execute("DELETE FROM usual_meal_items WHERE meal_id=%s", (uid,))
        for n, (fid, g) in enumerate(its, 1):
            cur.execute("INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order) VALUES (%s,%s,%s,%s)", (uid, fid, Decimal(_num(g)), n * 10))
    return f"OK changed usual meal U{uid} {sets.get('name', row['name'])}" + (f": {', '.join(sorted(sets))}" if sets else "") \
        + (f"; {len(its)} items replaced" if its is not None else "")


def _cf_target(cur, tid: Optional[int], fields: dict, delete: bool, now: datetime) -> str:
    row = None
    if tid is not None:
        cur.execute("SELECT * FROM food_targets WHERE id=%s", (tid,))
        row = cur.fetchone()
        if not row:
            raise Reject(f"target period T{tid} does not exist")
    if delete:
        if row is None or fields:
            raise Reject("delete needs an existing period (T<id>) and no fields")
        cur.execute("DELETE FROM food_targets WHERE id=%s", (tid,))
        return f"OK deleted target period T{tid} (from {row['valid_from'].strftime('%d.%m.%Y')})."
    bad = sorted(set(fields) - set(_TARGET_COLS) - {"valid_from", "note"})
    if bad:
        raise Reject(f"field(s) {', '.join(bad)} cannot be set on a target period (allowed: valid_from, note, {', '.join(_TARGET_COLS)})")
    vals: dict = {}
    for k in _TARGET_COLS:
        if k in fields:
            vals[k] = None if fields[k] is None else _fnum(fields[k], k, 0)
    if "note" in fields:
        vals["note"] = _ftext(fields["note"], "note")
    if "valid_from" in fields:
        w = parse_when(str(fields["valid_from"]), now)
        if not w:
            raise Reject(f"could not read the date '{fields['valid_from']}'")
        vals["valid_from"] = w[0].date()
    if row is None:
        if "valid_from" not in vals:
            raise Reject("a new target period needs valid_from (use thought_id 'T')")
        cur.execute("SELECT id FROM food_targets WHERE valid_from = %s", (vals["valid_from"],))
        dup = cur.fetchone()
        if dup:
            raise Reject(f"target period T{dup['id']} already starts on {vals['valid_from'].strftime('%d.%m.%Y')}: change T{dup['id']} instead")
        cur.execute("SELECT * FROM food_targets WHERE valid_from <= %s ORDER BY valid_from DESC LIMIT 1", (vals["valid_from"],))
        prev = cur.fetchone()
        base = {k: (prev[k] if prev else None) for k in _TARGET_COLS}
        base.update(vals)
        base.setdefault("note", None)
        cur.execute(f"INSERT INTO food_targets ({', '.join(base)}) VALUES ({', '.join(['%s'] * len(base))}) RETURNING id", list(base.values()))
        nid = cur.fetchone()["id"]
        return (f"OK new target period T{nid} from {vals['valid_from'].strftime('%d.%m.%Y')}"
                + (" (values you did not give were copied from the period in force then)" if prev else "") + ":\n"
                + _target_line({**base, "id": nid}))
    if not vals:
        raise Reject("nothing to change")
    if "valid_from" in vals:
        cur.execute("SELECT id FROM food_targets WHERE valid_from = %s AND id <> %s", (vals["valid_from"], tid))
        dup = cur.fetchone()
        if dup:
            raise Reject(f"target period T{dup['id']} already starts on {vals['valid_from'].strftime('%d.%m.%Y')}")
    cols = ", ".join(f"{k} = %s" for k in vals)
    cur.execute(f"UPDATE food_targets SET {cols} WHERE id = %s RETURNING *", list(vals.values()) + [tid])
    return f"OK changed target period T{tid}:\n" + _target_line(cur.fetchone())


def _target_line(t: dict) -> str:
    vf = t["valid_from"]
    return (f"T{t['id']} from {vf.strftime('%d.%m.%Y') if hasattr(vf, 'strftime') else vf}: kcal {_n(t['kcal'])}, protein {_n(t['protein'])}, fat {_n(t['fat'])}, "
            f"sat fat {_n(t['sat_fat']) if t['sat_fat'] is not None else '-'}, carbs {_n(t['carbs'])}, fibre {_n(t['fibre'])}, "
            f"added sugar {_n(t['sugar_added'])}, sodium {_n(t['sodium_mg'])} mg" + (f" ({t['note']})" if t.get("note") else ""))


_TOP = {"sodium": "sodium_mg", "salt": "sodium_mg", "sugar": "sugar_added", "added sugar": "sugar_added", "total sugar": "sugar_total",
        "sat fat": "sat_fat", "satfat": "sat_fat", "saturated fat": "sat_fat", "protein": "protein", "kcal": "kcal", "calories": "kcal",
        "fat": "fat", "carbs": "carbs", "fibre": "fibre", "fiber": "fibre", "caffeine": "caffeine_mg"}


def _entry_line(r: dict) -> str:
    unit = r.get("unit") or "g"
    return (f"f{r['id']} {r['time'].strftime('%H:%M') if r['time'] else '--:--'} {r['food']} {_num(r['amount'])} {unit}"
            + (f" ({r['portion_text']})" if r.get("portion_text") else "") + f" | {_n(r['kcal'])} kcal, P {_n(r['protein'], 1)}"
            + (" [est]" if r.get("estimated") else "") + (f" - {r['note']}" if r.get("note") else "") + ("  !" if r.get("needs_review") else ""))


def _food_day_view(cur, day: date, now: datetime) -> str:
    cur.execute("SELECT * FROM v_food_log WHERE day=%s ORDER BY is_drink, meal, time NULLS LAST, id", (day,))
    rows = cur.fetchall()
    if not rows:
        extra = " (no wake-up logged in the last 18 h, so this is the 05:00 day)" if day == (now - timedelta(hours=5)).date() and not _food_today_wake(cur, now) else ""
        return f"No food logged for {day.strftime('%d.%m.%Y')}.{extra}"
    cur.execute("SELECT meal, items, kcal, protein FROM v_food_day WHERE day=%s AND NOT is_total ORDER BY meal", (day,))
    mt = {r["meal"]: r for r in cur.fetchall()}
    out = [f"Food {day.strftime('%d.%m.%Y %a')}:"]
    cur_meal = None
    for r in rows:
        if r["meal"] != cur_meal:
            if cur_meal is not None:
                t = mt[cur_meal]
                out.append(f"  {'drinks' if cur_meal == 0 else 'meal ' + str(cur_meal)}: {_n(t['kcal'])} kcal, P {_n(t['protein'])} g")
            cur_meal = r["meal"]
            out.append("Drinks:" if cur_meal == 0 else f"Meal {cur_meal}:")
        out.append("  " + _entry_line(r))
    t = mt[cur_meal]
    out.append(f"  {'drinks' if cur_meal == 0 else 'meal ' + str(cur_meal)}: {_n(t['kcal'])} kcal, P {_n(t['protein'])} g")
    out.append(_day_line(cur, day))
    return "\n".join(out)


def _food_today_wake(cur, now: datetime) -> bool:
    cur.execute("SELECT food_day(%s) AS d", (now,))
    return cur.fetchone()["d"] is not None


def _food_line_full(f: dict) -> str:
    return (f"N{f['id']} {f['name']}" + (f" [{f['state']}]" if f.get("state") else "") + (" [drink]" if f.get("is_drink") else "") + (" (retired)" if not f["active"] else "")
            + (" !" if f.get("needs_review") else "") + f" | {f['source']} | " + _food_line100(f))


def _food_family(name: str) -> str:
    """Rough 'same kind of food' key: the first word of the catalogue name ('Skyr, unsweetened (Vogtlandmilch)' and
    'Skyr natural high protein (Milsani)' -> 'skyr'). Used only to judge whether two meals are similar; what gets saved is
    always the exact foods of a real logged meal."""
    w = re.split(r"[\s,(]+", name.strip().lower())[0] if name.strip() else ""
    return w or name.lower()


def _food_repeats(cur, lo: Optional[date], hi: Optional[date], now: datetime, min_n: int = 3, top: int = 6) -> str:
    """Meals you keep eating (similar meals on several days), with typical amounts, so one can be saved as a usual meal.
    Default window: the last 30 days. Similar = at least half of the kinds of food in common (Jaccard >= 0.5) with the kinds
    most of the group's meals contain; brands of the same food count as one kind. Groups are ranked by recency (a meal's weight
    halves every 7 days), so what you eat now comes before what you ate a month ago."""
    lo = lo or (now - timedelta(days=30)).date()
    today = now.date()
    cur.execute("""SELECT e.day, e.meal, e.food_id, f.name, e.amount FROM food_entries e JOIN foods f ON f.id=e.food_id
                   WHERE NOT e.is_drink AND e.meal > 0 AND e.day >= %s AND (%s::date IS NULL OR e.day <= %s)
                   ORDER BY e.day, e.meal, e.eaten_at, e.id""", (lo, hi, hi))
    meals: dict[tuple, dict] = {}
    names: dict[str, dict] = {}            # family -> food name -> times used in the window
    for r in cur.fetchall():
        fam = _food_family(r["name"])
        names.setdefault(fam, {})
        names[fam][r["name"]] = names[fam].get(r["name"], 0) + 1
        m = meals.setdefault((r["day"], r["meal"]), {})
        m[fam] = m.get(fam, 0.0) + float(r["amount"])
    groups: list[dict] = []
    for key, items in meals.items():
        if len(items) < 2:
            continue
        a = set(items)
        best, best_s = None, 0.0
        for g in groups:
            sim = len(a & g["core"]) / len(a | g["core"])
            if sim > best_s:
                best, best_s = g, sim
        if best is not None and best_s >= 0.5:
            best["members"].append((key, items))
            cnt: dict[str, int] = {}
            for _k, it in best["members"]:
                for fam in it:
                    cnt[fam] = cnt.get(fam, 0) + 1
            best["core"] = {fam for fam, c in cnt.items() if c * 2 >= len(best["members"])} or a
        else:
            groups.append({"core": set(a), "members": [(key, items)]})
    groups = [g for g in groups if len(g["members"]) >= min_n]
    for g in groups:
        g["score"] = sum(0.5 ** (max((today - k[0]).days, 0) / 7) for k, _it in g["members"])
    groups.sort(key=lambda g: (-g["score"], -len(g["members"])))
    if not groups:
        return f"No repeating meals found since {lo.strftime('%d.%m.%Y')} (a meal repeats when at least {min_n} days have a similar one)."
    cur.execute("""SELECT u.id, u.name, array_agg(f.name) AS fnames FROM usual_meals u
                   JOIN usual_meal_items i ON i.meal_id=u.id JOIN foods f ON f.id=i.food_id GROUP BY u.id""")
    usual = [(r["id"], r["name"], {_food_family(x) for x in r["fnames"]}) for r in cur.fetchall()]
    out = [f"Repeating meals since {lo.strftime('%d.%m.%Y')} (similar meals on {min_n}+ days, most recent first):"]
    for n, g in enumerate(groups[:top], 1):
        mem = g["members"]
        cnt: dict[str, list] = {}
        for _k, it in mem:
            for fam, gr in it.items():
                cnt.setdefault(fam, []).append(gr)
        usual_parts, extra_parts = [], []
        for fam, grs in sorted(cnt.items(), key=lambda kv: -len(kv[1])):
            grs = sorted(grs)
            med = grs[len(grs) // 2]
            label = max(names[fam].items(), key=lambda kv: kv[1])[0]
            if len(grs) * 2 > len(mem):
                usual_parts.append(f"{label} {_num(med)}")
            else:
                extra_parts.append(f"{label} {_num(med)} ({len(grs)}x)")
        last_day, last_meal = mem[-1][0]
        match = next((f"U{uid} '{nm}'" for uid, nm, fs in usual if len(fs & g["core"]) / len(fs | g["core"]) >= 0.7), None)
        out.append(f"R{n} {len(mem)}x, last {_dm(last_day)} meal {last_meal}: " + ", ".join(usual_parts)
                   + (f" | sometimes: {', '.join(extra_parts)}" if extra_parts else "")
                   + (f" | already saved as {match}" if match else f" | save: recipes kind=usual from_meal=\"{last_day.strftime('%d.%m.%Y')} {last_meal}\""))
    return "\n".join(out)


def _find_food(cur, what: str, since, until, limit) -> str:
    limit = max(1, min(int(limit or 20), 50))
    now = datetime.now(BERLIN)
    key = _fkey(what)
    lo_b, hi_b = _bound(since, False), _bound(until, True)
    lo, hi = (lo_b.date() if lo_b else None), (hi_b.date() if hi_b else None)
    m = re.fullmatch(r"([nfcut])\s*#?(\d+)", key)
    if m:
        return _food_detail(cur, m[1], int(m[2]), now)
    if key in ("", "today", "day"):
        return _food_day_view(cur, lo or _food_today(cur, now), now)
    if key in ("targets", "target"):
        cur.execute("SELECT * FROM food_targets ORDER BY valid_from")
        rows = cur.fetchall()
        return "\n".join(_target_line(r) for r in rows) if rows else "No target periods."
    if key in ("recipes", "recipe"):
        cur.execute("""SELECT r.id, r.name, r.finished_weight_g, r.weight_measured, f.id AS fid, count(i.id) AS n FROM recipes r
                       JOIN foods f ON f.id=r.food_id LEFT JOIN recipe_items i ON i.recipe_id=r.id GROUP BY r.id, f.id ORDER BY r.id""")
        return "\n".join(f"C{r['id']} {r['name']} (N{r['fid']}): {r['n']} items, finished {_num(r['finished_weight_g'])} g"
                         + ("" if r["weight_measured"] else " (estimated)") for r in cur.fetchall()) or "No recipes."
    if key in ("repeats", "repeat", "patterns"):
        return _food_repeats(cur, lo, hi, now)
    if key in ("usual", "usual meals", "meals"):
        cur.execute("""SELECT u.id, u.name, array_to_string(u.aliases, ', ') AS al, count(i.id) AS n FROM usual_meals u
                       LEFT JOIN usual_meal_items i ON i.meal_id=u.id GROUP BY u.id ORDER BY u.id""")
        return "\n".join(f"U{r['id']} {r['name']}: {r['n']} items" + (f" (also: {r['al']})" if r["al"] else "") for r in cur.fetchall()) or "No usual meals."
    if key in ("review", "flagged"):
        cur.execute("""SELECT f.id, f.name, f.review_reason, (SELECT count(*) FROM food_entries e WHERE e.food_id=f.id) AS uses
                       FROM foods f WHERE f.needs_review ORDER BY uses DESC, f.id LIMIT %s""", (limit,))
        items = [f"N{r['id']} {r['name']}  ! {r['review_reason'] or 'flagged'} ({r['uses']} entries)" for r in cur.fetchall()]
        cur.execute("""SELECT id, day, food, amount, review_reason FROM v_food_log WHERE needs_review ORDER BY day DESC, id DESC LIMIT %s""", (limit,))
        items += [f"f{r['id']} {_dm(r['day'])} {r['food']} {_num(r['amount'])}  ! {r['review_reason'] or 'flagged'}" for r in cur.fetchall()]
        return (f"{len(items)} flagged food row(s) (change with the N/f id; fields needs_review=false clears):\n" + "\n".join(items)) if items else "No flagged foods."
    if key in ("unknown", "gaps"):
        cur.execute("""SELECT f.id, f.name, count(e.id) AS uses, f.sat_fat IS NULL AS s, f.sugar_total IS NULL AS t, f.sugar_added IS NULL AS a
                       FROM foods f JOIN food_entries e ON e.food_id=f.id
                       WHERE f.active AND (f.sat_fat IS NULL OR f.sugar_total IS NULL OR f.sugar_added IS NULL)
                       GROUP BY f.id ORDER BY uses DESC, f.id LIMIT %s""", (limit,))
        rows = cur.fetchall()
        return (f"{len(rows)} used food(s) with unknown values (most used first):\n"
                + "\n".join(f"N{r['id']} {r['name']} ({r['uses']} entries): unknown " + ", ".join(
                    n for n, f in (("sat fat", r["s"]), ("total sugar", r["t"]), ("added sugar", r["a"])) if f) for r in rows)) if rows else "No used food has unknown sat fat or sugar."
    m = re.fullmatch(r"(?:changes|history)\s*n?\s*#?(\d+)", key)
    if m:
        cur.execute("SELECT * FROM food_changes WHERE food_id=%s ORDER BY id DESC LIMIT %s", (int(m[1]), limit))
        rows = cur.fetchall()
        if not rows:
            return f"No recorded changes for N{m[1]}."
        out = []
        for r in rows:
            if r["op"] == "update":
                diff = ", ".join(f"{k}: {_show(r['old_values'].get(k))} -> {_show(v)}" for k, v in r["new_values"].items())
                out.append(f"{fmt(r['changed_at'])} {r['changed_by']}: {_short(diff, 200)}")
            else:
                out.append(f"{fmt(r['changed_at'])} {r['changed_by']}: {r['op']} {r['note'] or ''}".rstrip())
        return f"Changes of N{m[1]} {rows[0]['food_name']}:\n" + "\n".join(out)
    if key.startswith("top "):
        col = _TOP.get(key[4:].strip())
        if not col:
            return "top <" + ", ".join(sorted({k for k in _TOP})) + ">"
        hi_d = hi or _food_today(cur, now)
        lo_d = lo or hi_d - timedelta(days=13)
        cur.execute(f"""SELECT f.id, f.name, round(sum(e.{col}), 1) AS total, count(*) AS n, count(*) FILTER (WHERE e.{col} IS NULL) AS unk
                        FROM food_entries e JOIN foods f ON f.id=e.food_id WHERE e.day BETWEEN %s AND %s
                        GROUP BY f.id ORDER BY total DESC NULLS LAST LIMIT %s""", (lo_d, hi_d, limit))
        rows = cur.fetchall()
        cur.execute(f"SELECT round(sum({col}), 1) AS t, count(*) FILTER (WHERE {col} IS NULL) AS u, count(*) AS n FROM food_entries WHERE day BETWEEN %s AND %s", (lo_d, hi_d))
        t = cur.fetchone()
        unit = "mg" if col in ("sodium_mg", "caffeine_mg") else ("kcal" if col == "kcal" else "g")
        return (f"Top sources of {_NUT_LABEL[col]} {_dm(lo_d)}-{hi_d.strftime('%d.%m.%Y')}: total {_n(t['t'])} {unit} in {t['n']} entries"
                + (f" ({t['u']} entries unknown)" if t["u"] else "") + "\n"
                + "\n".join(f"N{r['id']} {r['name']}: {_n(r['total'])} {unit} ({r['n']}x)" + (f", {r['unk']} unknown" if r["unk"] else "") for r in rows if r["total"] is not None))
    if key == "week":
        hi_d = hi or _food_today(cur, now)
        lo_d = lo or hi_d - timedelta(days=6)
        cur.execute("SELECT * FROM v_food_day WHERE is_total AND day BETWEEN %s AND %s ORDER BY day", (lo_d, hi_d))
        rows = cur.fetchall()
        if not rows:
            return f"No food logged {_dm(lo_d)}-{hi_d.strftime('%d.%m.%Y')}."
        k = len(rows)
        avg = lambda c: sum(float(r[c] or 0) for r in rows) / k  # noqa: E731
        lines = [f"{r['day'].strftime('%d.%m. %a')}: {_n(r['kcal'])} kcal"
                 + (f" / {_n(r['target_kcal'])}" if r["target_kcal"] is not None else "")
                 + f", P {_n(r['protein'])}, F {_n(r['fat'])}, C {_n(r['carbs'])} ({r['items']} items)" for r in rows]
        return (f"{k} day(s) with food {_dm(lo_d)}-{hi_d.strftime('%d.%m.%Y')}: average {_n(avg('kcal'))} kcal, P {_n(avg('protein'))}, "
                f"F {_n(avg('fat'))}, C {_n(avg('carbs'))}, fibre {_n(avg('fibre'))}, added sugar {_n(avg('sugar_added'))}, sodium {_n(avg('sodium_mg'))} mg\n"
                + "\n".join(lines))
    w = parse_when(what, now)
    if w and w[1]:
        return _food_day_view(cur, w[0].date(), now)
    cat = _food_cat(cur)
    hits, how = _find_foods(cat, what)
    if not hits:
        return f"No food matches '{what}'."
    hits = hits[:limit]
    return ("" if how in ("id", "name", "alias", "default") else f"No exact match for '{what}'; similar foods:\n") + "\n".join(_food_line_full(f) for f in hits)


def _food_detail(cur, kind: str, num: int, now: datetime) -> str:
    if kind == "n":
        cur.execute("SELECT * FROM foods WHERE id=%s", (num,))
        f = cur.fetchone()
        if not f:
            return f"Food N{num} does not exist."
        out = [_food_line_full(f)]
        if f["aliases"]:
            out.append("aliases: " + ", ".join(f["aliases"]))
        if f["source_note"]:
            out.append("source: " + f["source_note"])
        if f["review_reason"]:
            out.append("review: " + f["review_reason"])
        if f["variant_of"]:
            cur.execute("SELECT id, name FROM foods WHERE id=%s", (f["variant_of"],))
            v = cur.fetchone()
            out.append(f"other variant: N{v['id']} {v['name']}")
        cur.execute("SELECT unit, grams, source FROM food_units WHERE food_id=%s ORDER BY unit", (num,))
        us = cur.fetchall()
        if us:
            out.append("units: " + ", ".join(f"1 {u['unit']} = {_num(u['grams'])} {f['basis']}" + ("" if u["source"] == "label" else " (est)") for u in us))
        cur.execute("SELECT term FROM food_defaults WHERE food_id=%s", (num,))
        ds = [r["term"] for r in cur.fetchall()]
        if ds:
            out.append("default product for: " + ", ".join(ds))
        cur.execute("SELECT id FROM recipes WHERE food_id=%s", (num,))
        rc = cur.fetchone()
        if rc:
            out.append(f"calculated from recipe C{rc['id']}")
        cur.execute("SELECT count(*) AS n, min(day) AS a, max(day) AS b FROM food_entries WHERE food_id=%s", (num,))
        u = cur.fetchone()
        out.append(f"used in {u['n']} entries" + (f" ({_dm(u['a'])}-{u['b'].strftime('%d.%m.%Y')})" if u["n"] else ""))
        return "\n".join(out)
    if kind == "f":
        cur.execute("SELECT * FROM v_food_log WHERE id=%s", (num,))
        r = cur.fetchone()
        if not r:
            return f"Food entry f{num} does not exist."
        cur.execute("SELECT day_rule, extractor_version, usual_meal_id, recipe_id FROM food_entries WHERE id=%s", (num,))
        x = cur.fetchone()
        return (f"{_entry_line(r)}\nday {r['day'].strftime('%d.%m.%Y')} meal {r['meal']} (day by {x['day_rule']}), raw #{r['raw_id']}, {x['extractor_version']}, logged {fmt(r['logged_at'])}\n"
                f"fat {_n(r['fat'], 1)} (sat {_n(r['sat_fat'], 1)}), carbs {_n(r['carbs'], 1)} (fibre {_n(r['fibre'], 1)}, sugar {_n(r['sugar_total'], 1)}, "
                f"added {_n(r['sugar_added'], 1)}), sodium {_n(r['sodium_mg'])} mg, caffeine {_n(r['caffeine_mg'])} mg")
    if kind == "c":
        cur.execute("SELECT r.*, f.name AS fname FROM recipes r JOIN foods f ON f.id=r.food_id WHERE r.id=%s", (num,))
        r = cur.fetchone()
        if not r:
            return f"Recipe C{num} does not exist."
        cur.execute("""SELECT f.id, f.name, i.grams FROM recipe_items i JOIN foods f ON f.id=i.food_id WHERE i.recipe_id=%s ORDER BY i.sort_order, i.id""", (num,))
        its = cur.fetchall()
        cur.execute("SELECT * FROM foods WHERE id=%s", (r["food_id"],))
        return (f"C{num} {r['name']} = N{r['food_id']}, finished {_num(r['finished_weight_g'])} g" + ("" if r["weight_measured"] else " (estimated)")
                + "\n" + "\n".join(f"  N{i['id']} {i['name']} {_num(i['grams'])} g" for i in its) + "\n" + _food_line100(cur.fetchone()))
    if kind == "u":
        cur.execute("SELECT * FROM usual_meals WHERE id=%s", (num,))
        u = cur.fetchone()
        if not u:
            return f"Usual meal U{num} does not exist."
        cur.execute("""SELECT f.id, f.name, i.grams FROM usual_meal_items i JOIN foods f ON f.id=i.food_id WHERE i.meal_id=%s ORDER BY i.sort_order, i.id""", (num,))
        return (f"U{num} {u['name']}" + (" [drink]" if u["is_drink"] else "") + (f" (also: {', '.join(u['aliases'])})" if u["aliases"] else "") + "\n"
                + "\n".join(f"  N{i['id']} {i['name']} {_num(i['grams'])} g" for i in cur.fetchall()))
    cur.execute("SELECT * FROM food_targets WHERE id=%s", (num,))
    t = cur.fetchone()
    return _target_line(t) if t else f"Target period T{num} does not exist."

