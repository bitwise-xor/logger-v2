"""Lab reports and medication courses in v2 (decided 26.10.2026). A report's text (written out by the chat from the
person's photo or PDF) is structured by the larger model; v1's own code (v1_labs.py) then checks it, builds the read-back
table and saves it after the person's yes. Medication changes come through log as commands and are read back before
they are saved; the person's words are kept as the message."""
import re
from datetime import datetime

from psycopg.types.json import Jsonb

from .ai import report as reader
from .ai.provider import Provider, Usage
from .v1_commands import _change_med, _change_report, _course_text
from .v1_labs import Med, Report, _active_courses, _insert_meds, _insert_report, _prepare_meds, _prepare_report
from .v1_log import ReadBack, Reject

CHANNEL = "mcp"
REPORT_TEXT = ("city", "country", "reported", "accession", "ordered_by", "signed_by", "fasting", "status", "notes", "tz")
VALUE_TEXT = ("unit", "ref", "printed_name", "panel", "site", "side", "specimen", "method", "note")
YES_NO = {"yes": True, "no": False}
MED_ARGS = set(Med.model_fields) - {"confirmed"}
DOSE = re.compile(r"\s+\d+(?:[.,]\d+)?\s*(?:mg|µg|ug|mcg|g|ml|iu|ie|i\.e\.)\b.*$", re.I)  # "Rosuvastatin 5 mg" -> "Rosuvastatin"

def _num(s: str):
    try:
        return float(s.replace(",", ".")) if s and s.strip() else None
    except ValueError:
        return None

def _report(d: dict, source: str) -> Report:
    """The model's answer ("" = not on the report) as v1's Report."""
    values = [dict(test=v["test"], value=v["value"], ref_low=_num(v["ref_low"]), ref_high=_num(v["ref_high"]),
                   calculated=YES_NO.get(v["calculated"]), abnormal=YES_NO.get(v["abnormal"]),
                   **{k: v[k] or None for k in VALUE_TEXT}) for v in d["values"]]
    return Report(type=d["type"], title=d["title"], provider=d["provider"], collected=d["collected"], values=values,
                  source="document" if source == "document" else "image", **{k: d[k] or None for k in REPORT_TEXT})

def read_back(cur, provider: Provider, text: str, now: datetime, source: str = "image", corrections: str = "") -> tuple[dict, list[Usage]]:
    """-> (read-back text, the structured report and v1's code for the yes), AI usage. v1's checks may raise Reject."""
    cur.execute("SELECT name FROM lab_tests ORDER BY name")
    d, u = reader.read(provider, text, [r["name"] for r in cur.fetchall()], corrections or "")
    rep = _report(d, source)
    prep = _prepare_report(cur, rep, now)
    rows = prep["preview"].split("\n")[1:]  # v1's first line told its chat how to resend; in v2 the yes goes through answer
    head = ("Read back, nothing saved yet. Show the user this whole table and ask them to check it against the report: "
            "their yes saves it (answer); for a correction call lab_report again with the same text and their corrections.")
    return {"read_back": "\n".join([head] + rows), "report": rep.model_dump(mode="json", exclude={"confirm"}),
            "code": prep["code"]}, [u]

def save_report(cur, payload: dict, now: datetime) -> dict:
    prep = _prepare_report(cur, Report(**payload["report"], confirm=payload["code"]), now)
    if prep["preview"]:  # the read-back no longer matches (a test name was added meanwhile ...)
        return {"outcome": "rejected", "reason": "the report reads differently now; send it again for a new read-back"}
    cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES (%s, %s, %s) RETURNING id",
                (CHANNEL, payload["text"], Jsonb({"lab_report": True})))
    raw_id = cur.fetchone()["id"]
    out, notes = [], []
    _insert_report(cur, raw_id, prep, out, notes)
    return {"outcome": "saved", "raw_id": raw_id, "receipt": out, "notes": notes}

def _course_name(cur, name: str) -> str:
    """The active course a stop means: v1's match, else the name without a dose ("Rosuvastatin 5 mg"), else the one active
    course that has a distinctive word of it ("ASS-AbZ TAH" -> "ASS-AbZ 100 mg TAH")."""
    if _active_courses(cur, name):
        return name
    short = DOSE.sub("", name).strip()
    if short and _active_courses(cur, short):
        return short
    cur.execute("SELECT * FROM medications WHERE stop_date IS NULL OR stop_date > current_date ORDER BY id")
    rows = cur.fetchall()
    words = [w for w in re.findall(r"[\w-]+", name.lower()) if len(w) > 2 and not w.isdigit() and w not in ("the", "and", "tablet")]
    hits = [r for r in rows if any(w in f"{r['name']} {r['ingredient'] or ''}".lower() for w in words)]
    return hits[0]["name"] if len(hits) == 1 else name


def meds(cur, steps: list[dict], now: datetime, text: str = "", dry: bool = False) -> list[str]:
    """Start or stop courses (v1's checks). dry: what would happen, for the read-back; else saved, with the person's words
    as the message and the stopped course ids in its metadata (undo puts them back)."""
    steps = [{**s, "name": _course_name(cur, s["name"])} if s.get("action") == "stop" else s for s in steps]
    ms = [Med(**s, confirmed=not dry) for s in steps]
    try:
        prep = _prepare_meds(cur, ms, now)
    except ReadBack as e:  # already active: shown, and the yes saves it anyway
        soft = str(e).split("\n", 1)[1]
        prep = _prepare_meds(cur, [Med(**s, confirmed=True) for s in steps], now)
        lines = [f"note: {soft}"]
    else:
        lines = []
    if dry:
        return [(f"stop {_course_text(p['row'])} on {p['stop']:%d.%m.%Y}" + (f" ({p['m'].reason})" if p["m"].reason else "")) if "row" in p
                else f"start {p['m'].name}" + "".join(f" {x}" for x in (p["m"].dose, p["m"].frequency) if x)
                + (f" from {p['start']:%d.%m.%Y}" if p["known"] else " (start date unknown, flagged)") for p in prep] + lines
    cur.execute("INSERT INTO raw_inbox (channel, content, metadata) VALUES (%s, %s, %s) RETURNING id",
                (CHANNEL, text, Jsonb({"med_stops": [p["row"]["id"] for p in prep if "row" in p]})))
    raw_id = cur.fetchone()["id"]
    out, notes = [], []
    _insert_meds(cur, raw_id, prep, out, notes)
    return out + [f"note: {n}" for n in notes]

def undo(cur, raw_id: int, now: datetime, dry: bool = False) -> list[str]:
    """A report saved from this message (v1 allows deleting it for 24 hours), the courses it started, the stops it made."""
    lines = []
    cur.execute("SELECT id FROM lab_reports WHERE raw_id = %s", (raw_id,))
    for r in cur.fetchall():
        lines.append(f"delete report R{r['id']} and its values" if dry else _change_report(cur, r["id"], {}, True, now))
    cur.execute("SELECT * FROM medications WHERE raw_id = %s", (raw_id,))
    for r in cur.fetchall():
        lines.append(f"delete {_course_text(r)}" if dry else _change_med(cur, r["id"], {}, True, now))
    cur.execute("""SELECT m.* FROM medications m, raw_inbox i WHERE i.id = %s AND m.stop_date IS NOT NULL
                   AND m.id IN (SELECT jsonb_array_elements_text(i.metadata->'med_stops')::int)""", (raw_id,))
    for r in cur.fetchall():
        if not dry:
            cur.execute("UPDATE medications SET stop_date = NULL, stop_reason = NULL WHERE id = %s", (r["id"],))
        lines.append(f"{'take back' if dry else 'took back'} the stop of {_course_text(r)}")
    return lines
