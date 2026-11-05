"""Export of everything logged (web item 45, core item 19; 05.11.2026): one ZIP download from Settings > Export with a CSV file
per kind of record, a README and one all.json with the same rows (plus coachings and lab reports in full). An open copy to keep
or to open in a spreadsheet, apart from the Google Sheet and the encrypted backup. Private and gratitude entries only when the
box is ticked. Each download leaves one audit row: whether private entries were in it and how many rows each file had, never
any content. Login data, sessions, tokens and the audit log itself are never exported."""
import csv
import io
import json
import zipfile
from datetime import date, datetime, time
from decimal import Decimal

from . import logbook
from .db import connect
from .webentry import hidden
from .when import BERLIN

# A time is only shown when it is known: a row with time_known false has a placeholder time, so it is left empty.
_T = "CASE WHEN {k} THEN {c} END"

# (file, what it holds, query). The order is the README's order. Every query reads one kind of record, oldest first.
FILES: list[tuple[str, str, str]] = [
    ("food_entries", "Every food and drink logged, with its nutrients as eaten (kcal, grams, mg).",
     f"""SELECT e.id, e.day, {_T.format(k='e.time_known', c='e.eaten_at')} AS time, e.meal, e.is_drink, f.name AS food, e.food_id,
                e.amount, f.basis AS unit, e.portion_text, e.kcal, e.protein, e.fat, e.sat_fat, e.carbs, e.fibre, e.sugar_total,
                e.sugar_added, e.sodium_mg, e.caffeine_mg, e.estimated, e.source, e.note, e.needs_review, e.raw_id, e.logged_at
         FROM food_entries e JOIN foods f ON f.id = e.food_id ORDER BY e.day, e.eaten_at, e.id"""),
    ("foods", "The food catalogue: nutrients per 100 g (or 100 ml for drinks), other names, units in grams.",
     """SELECT f.id, f.name, f.aliases, f.basis, f.kcal, f.protein, f.fat, f.sat_fat, f.carbs, f.fibre, f.sugar_total, f.sugar_added,
               f.sodium_mg, f.caffeine_mg, f.carbs_include_fibre, f.is_drink, f.brand, f.barcode, f.category, f.state, f.variant_of,
               f.active, f.source, f.source_note, f.source_date,
               (SELECT string_agg(u.unit || ' = ' || trim(trailing '.' FROM trim(trailing '0' FROM u.grams::text)) || ' g', '; ' ORDER BY u.unit)
                FROM food_units u WHERE u.food_id = f.id) AS units, f.created_at, f.updated_at
        FROM foods f ORDER BY f.id"""),
    ("measurements", "Health values: weight, blood pressure (value1/value2), blood sugar, body measurements, mood and energy.",
     f"""SELECT m.id, m.day, {_T.format(k='m.time_known', c='m.event_at')} AS time, t.name AS type, m.period, m.value1, m.value2,
                t.unit, m.note, m.details, m.needs_review, m.raw_id, m.logged_at
         FROM measurements m JOIN log_types t ON t.id = m.type_id ORDER BY m.day, m.event_at, m.id"""),
    ("events", "Wake-ups, bed times, naps and habits (one row each time).",
     f"""SELECT e.id, e.day, {_T.format(k='e.time_known', c='e.event_at')} AS time, t.name AS type, t.label, t.category, e.note,
                e.details, e.needs_review, e.raw_id, e.logged_at
         FROM events e JOIN log_types t ON t.id = e.type_id ORDER BY e.day, e.event_at, e.id"""),
    ("sleeps", "Each sleep: the bed time before a wake-up or nap, the wake-up, the hours between.",
     "SELECT wake_day, nap, bed_at, wake_at, hours, wake_id FROM v_sleeps ORDER BY wake_at"),
    ("thoughts", "Thoughts and ideas (not private or gratitude entries).",
     """SELECT id, event_at, original_text, summary, thought_type, next_step, area, tags, aliases, stage, source, source_detail,
               needs_review, raw_id, logged_at FROM thoughts ORDER BY event_at, id"""),
    ("thought_updates", "Later updates to a thought (thought_id is the id in thoughts.csv).",
     "SELECT id, thought_id, event_at, update_text, raw_id, logged_at FROM thought_updates ORDER BY event_at, id"),
    ("gym_sets", "Gym workouts, one row per exercise line (a workout without lines has one empty row).",
     """SELECT w.id AS workout_id, w.day, w.started_at, w.ended_at, d.name AS plan_day, w.note AS workout_note, w.raw_id, x.name AS exercise,
               x.kind, s.sets, s.reps, s.kg, s.minutes, s.km, s.note
        FROM workouts w LEFT JOIN workout_days d ON d.id = w.split_day_id LEFT JOIN workout_sets s ON s.workout_id = w.id
        LEFT JOIN exercises x ON x.id = s.exercise_id ORDER BY w.day, w.started_at, w.id, s.sort_order, s.id"""),
    ("home_done", "Home chores done.",
     """SELECT h.id, h.day, h.done_at, c.name AS chore, z.name AS zone, h.dated, h.source, h.raw_id
        FROM home_done h JOIN home_chores c ON c.id = h.chore_id LEFT JOIN home_zones z ON z.id = h.zone_id ORDER BY h.done_at, h.id"""),
    ("lab_results", "Lab results, one row per value, with the report it came from.",
     """SELECT r.id, r.report_id, p.collected_date, p.provider, p.city, p.report_type, t.name AS test, r.name_printed, t.category,
               r.panel, r.specimen, r.body_site, r.side, r.qualifier, r.value, r.unit, r.value_text, r.ref_range_raw, r.ref_low,
               r.ref_high, r.flag, r.method, r.is_calculated, r.note, p.raw_id
        FROM lab_results r JOIN lab_reports p ON p.id = r.report_id JOIN lab_tests t ON t.id = r.test_id
        ORDER BY p.collected_date, r.report_id, r.id"""),
    ("medications", "Medication and supplements with dose and start/stop dates.",
     """SELECT id, name, ingredient, dose, frequency, drug_class, indication, start_date, stop_date, stop_reason, note, raw_id, logged_at
        FROM medications ORDER BY start_date NULLS FIRST, id"""),
    ("body_height", "Height in cm, each valid from a date.", "SELECT valid_from, height_cm, logged_at FROM body_height ORDER BY valid_from"),
    ("food_targets", "Daily food targets, each valid from a date.",
     """SELECT valid_from, kcal, protein, fat, sat_fat, carbs, fibre, sugar_added, sodium_mg, water_ml, note FROM food_targets ORDER BY valid_from"""),
    ("usual_meals", "Usual meals, one row per food in a meal.",
     """SELECT m.id AS meal_id, m.name AS meal, m.aliases, m.is_drink, m.note, f.name AS food, i.grams
        FROM usual_meals m LEFT JOIN usual_meal_items i ON i.meal_id = m.id LEFT JOIN foods f ON f.id = i.food_id
        ORDER BY lower(m.name), i.sort_order, i.id"""),
    ("recipes", "Recipes, one row per ingredient.",
     """SELECT r.id AS recipe_id, r.name AS recipe, r.finished_weight_g, r.weight_measured, r.note, f.name AS ingredient, i.grams
        FROM recipes r LEFT JOIN recipe_items i ON i.recipe_id = r.id LEFT JOIN foods f ON f.id = i.food_id
        ORDER BY lower(r.name), i.sort_order, i.id"""),
    ("watch_metrics", "Values from the watch (steps, heart rate, sleep and so on).",
     "SELECT id, metric, at, value, unit, source, extra FROM watch_metrics ORDER BY at, id"),
    ("watch_workouts", "Workouts recorded by the watch.",
     "SELECT id, name, start_at, end_at, kcal, km, hr_avg, hr_max, source FROM watch_workouts ORDER BY start_at, id"),
    ("gym_exercises", "The gym exercise list.", "SELECT id, name, kind, words, active, sort_order FROM exercises ORDER BY sort_order, id"),
    ("gym_plan", "The gym plan: each plan day with its exercises.",
     """SELECT d.id AS day_id, d.name AS plan_day, d.active, x.name AS exercise, i.sets, i.reps
        FROM workout_days d LEFT JOIN workout_day_items i ON i.day_id = d.id LEFT JOIN exercises x ON x.id = i.exercise_id
        ORDER BY d.sort_order, d.id, i.sort_order, i.id"""),
    ("home_zones", "Home zones.", "SELECT id, name, short, spots, words, active, sort_order FROM home_zones ORDER BY sort_order, id"),
    ("home_chores", "Home chores with their zone and how often they are due.",
     """SELECT c.id, c.name, z.name AS zone, c.interval_days, c.minutes, c.zone_days, c.zone_minutes, c.zone_off, c.steps, c.howto,
               c.words, c.active, c.sort_order
        FROM home_chores c LEFT JOIN home_zones z ON z.id = c.zone_id ORDER BY c.sort_order, c.id"""),
    ("messages", "The original typed messages the records above were read from (raw_id in other files points here).",
     "SELECT id, received_at, channel, content, metadata FROM raw_inbox ORDER BY received_at, id"),
]
PRIVATE = ("private", "Private and gratitude entries, as written (only when asked for).",
           """SELECT id, label, event_at, received_at, content, summary, thought_type, area, tags FROM private.entries
              ORDER BY coalesce(event_at, received_at), id""")
# Kept in all.json only: their main content is nested (a coaching's text, a report with its results).
COACHINGS = "SELECT id, kind, start_day, end_day, status, body, model, created_at, finished_at FROM coachings ORDER BY created_at, id"
LAB_REPORTS = """SELECT id, report_type, title, provider, department, city, country, accession_no, ordering_doctor, signing_doctors,
                        collected_date, collected_at, reported_at, report_status, fasting_status, source, source_detail, document_role,
                        notes, details FROM lab_reports ORDER BY collected_date, id"""


def _num(d: Decimal):
    return int(d) if d == d.to_integral_value() else float(d)


def _json(v):
    """One value as JSON keeps it: times in Berlin time with the offset, dates as yyyy-mm-dd."""
    if isinstance(v, datetime):
        return v.astimezone(BERLIN).isoformat(timespec="seconds")
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return _num(v)
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    return v


def _cell(v) -> str:
    """One value as a CSV cell: lists joined with "; ", nested data as JSON text, yes/no for true/false, empty for none."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v else ""
    if isinstance(v, (list, tuple)):
        return "; ".join(_cell(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(_json(v), ensure_ascii=False, sort_keys=True)
    return str(_json(v))


def _csv(cols: list[str], rows: list[dict]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(r[c]) for c in cols])
    return out.getvalue().encode("utf-8-sig")  # with the mark, Excel reads umlauts right


def _read(cur, sql: str) -> tuple[list[str], list[dict]]:
    cur.execute(sql)
    return [d.name for d in cur.description], cur.fetchall()


def _readme(stamp: datetime, files: list[tuple[str, str, int]], with_private: bool) -> bytes:
    lines = [f"Logger export of {stamp:%d.%m.%Y %H:%M} (Berlin time)", "",
             "Everything logged, as CSV files (one per kind of record) and all.json (the same rows, plus coachings and",
             "lab reports in full). Apart from the Google Sheet and the encrypted backup.", "",
             "How to read the files:",
             "- CSV: UTF-8, comma-separated, first row = column names. In Excel use Data > From Text/CSV if columns run together.",
             "- Dates are yyyy-mm-dd; times are Berlin time with the offset (2026-11-05T07:30:00+01:00).",
             "- A time is empty when only the day is known.",
             "- \"day\" is the logger's day (it follows the wake-up), so a snack after midnight can belong to the day before.",
             "- Numbers use a dot. Energy in kcal; nutrients in g; sodium and caffeine in mg; weight in kg; lengths in cm.",
             "- Lists are joined with \"; \"; nested details are JSON text.",
             "- raw_id in a row points to the message it was read from (messages.csv, column id), when there is one.",
             f"- Private and gratitude entries: {'included (private.csv)' if with_private else 'not included'}.", "",
             "Files (rows):"]
    lines += [f"- {name}.csv ({n}): {what}" for name, what, n in files]
    lines += ["- all.json: every file above under \"tables\", plus \"coachings\" and \"lab_reports\" (each with its results).", ""]
    return "\r\n".join(lines).encode("utf-8")


def build(with_private: bool, now: datetime | None = None) -> tuple[bytes, str, dict]:
    """(the ZIP's bytes, its file name, rows per file). All reads in one snapshot, so the files agree with each other."""
    stamp = (now or datetime.now(BERLIN)).astimezone(BERLIN)
    base = f"logger-export-{stamp:%d.%m.%Y}"
    specs = FILES + ([PRIVATE] if with_private else [])
    tables: dict[str, tuple[list[str], list[dict]]] = {}
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for name, _what, sql in specs:
            tables[name] = _read(cur, sql)
        _c, coachings = _read(cur, COACHINGS)
        _c, reports = _read(cur, LAB_REPORTS)
    results: dict[int, list] = {}
    for r in tables["lab_results"][1]:
        results.setdefault(r["report_id"], []).append({k: v for k, v in r.items() if k not in ("report_id", "collected_date", "provider", "city", "report_type")})
    counts = {name: len(tables[name][1]) for name, _w, _s in specs}
    doc = {"exported_at": _json(stamp), "time_zone": "Europe/Berlin", "private_included": with_private, "rows": counts,
           "tables": {name: [_json(dict(r)) for r in rows] for name, (_cols, rows) in tables.items()},
           "coachings": [_json(dict(r)) for r in coachings],
           "lab_reports": [{**_json(dict(r)), "results": _json(results.get(r["id"], []))} for r in reports]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}/README.txt", _readme(stamp, [(n, w, counts[n]) for n, w, _s in specs], with_private))
        for name, (cols, rows) in tables.items():
            z.writestr(f"{base}/{name}.csv", _csv(cols, rows))
        z.writestr(f"{base}/all.json", json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8"))
    return buf.getvalue(), base + ".zip", counts


def download(form, session) -> tuple[bytes, str]:
    """The Export button: build the file and leave the audit row (counts only)."""
    with_private = str(form.get("private", "")) == "1"
    data, name, counts = build(with_private)
    with connect() as conn, conn.cursor() as cur:
        logbook._audit(cur, "export", "ok", {"private": with_private, "rows": counts, "bytes": len(data)}, logbook.WEB)
    return data, name


def settings_tab(session, csrf: str) -> str:
    """Settings > Export: one button, private entries only when ticked, and when the last download was."""
    with connect() as c:
        last = c.execute("SELECT at, detail FROM audit_log WHERE tool = 'export' AND outcome = 'ok' ORDER BY at DESC LIMIT 1").fetchone()
    seen = (f'<p class="small">Last download: {last["at"].astimezone(BERLIN):%d.%m.%Y %H:%M}'
            f'{", with private entries" if (last["detail"] or {}).get("private") else ""}.</p>' if last else "")
    return ('<div class="card"><h2>Export everything</h2>'
            '<p>One ZIP file with everything you logged: food with nutrients, the food catalogue, health values, wake-up, bed, naps and habits, '
            'sleeps, thoughts, gym, home, coachings, lab results, medication, watch data, usual meals, recipes, your gym plan and chores, and your original messages. '
            'A CSV file for each (opens in a spreadsheet), all.json with everything in one file, and a README.</p>'
            f'<form method="post" action="/web/settings/export" data-download>{hidden("csrf", csrf)}'
            '<label class="row"><input type="checkbox" name="private" value="1"> Include private and gratitude entries</label>'
            '<p class="row"><button class="primary" type="submit">Download</button></p></form>'
            f'{seen}<p class="small hint">The Google Sheet and the encrypted backup stay as they are; this is a plain copy to keep. '
            'Each download is noted in the audit log (with or without private entries, how many rows), never what is in it. '
            'Keep the file somewhere safe: it holds your health data.</p></div>')

