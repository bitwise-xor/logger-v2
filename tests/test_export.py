"""Settings > Export (web item 45, core item 19; 05.11.2026): one ZIP with a CSV per kind of record, a README and all.json;
private and gratitude entries only when ticked; every download leaves an audit row with counts only; signed-in web only."""
import csv
import io
import json
import zipfile
from decimal import Decimal

from conftest import owner_sql
from test_web import ORIGIN, act, client, web  # noqa: F401
from app import export

PRIVATE_WORDS = "zz-private-marker"
GRATITUDE_WORDS = "zz-gratitude-marker"


def _seed():
    """One record of each kind, made-up values only."""
    raw = owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('test', 'porridge 60 g, weight 70.4') RETURNING id")[0]["id"]
    food = owner_sql("""INSERT INTO foods (name, aliases, basis, kcal, protein, fat, carbs, fibre, source)
                        VALUES ('Test oats', ARRAY['oatmeal','porridge oats'], 'g', 370, 13.5, 7, 59, 10, 'label') RETURNING id""")[0]["id"]
    owner_sql("INSERT INTO food_units (food_id, unit, grams, source) VALUES (%s, 'cup', 80.0, 'label')", food)
    owner_sql("""INSERT INTO food_entries (raw_id, food_id, amount, eaten_at, time_known, day, day_rule, meal, is_drink, kcal, protein, fat,
                                           carbs, source, extractor_version)
                 VALUES (%s, %s, 60, '2026-10-20 07:30+02', true, '2026-10-20', 'wake', 1, false, 222.00, 8.10, 4.20, 35.40, 'label', 't'),
                        (%s, %s, 40, '2026-10-20 12:00+02', false, '2026-10-20', 'wake', 2, false, 148, 5.4, 2.8, 23.6, 'label', 't')""",
              raw, food, raw, food)
    t = {r["name"]: r["id"] for r in owner_sql("SELECT id, name FROM log_types WHERE name IN ('weight','wake','bed','nap','yoga')")}
    owner_sql("""INSERT INTO measurements (raw_id, type_id, day, event_at, time_known, value1, extractor_version)
                 VALUES (%s, %s, '2026-10-20', '2026-10-20 07:10+02', true, 70.4, 't')""", raw, t["weight"])
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version) VALUES
                 (%s, %s, '2026-10-19', '2026-10-19 23:00+02', true, 't'), (%s, %s, '2026-10-20', '2026-10-20 07:00+02', true, 't'),
                 (%s, %s, '2026-10-20', '2026-10-20 18:00+02', true, 't')""", raw, t["bed"], raw, t["wake"], raw, t["yoga"])
    th = owner_sql("""INSERT INTO thoughts (raw_id, original_text, summary, thought_type, area, tags, event_at)
                      VALUES (%s, 'idea: a shelf for the hall', 'A shelf for the hall', 'idea', 'projects', ARRAY['home','diy'],
                              '2026-10-20 09:00+02') RETURNING id""", raw)[0]["id"]
    owner_sql("INSERT INTO thought_updates (thought_id, update_text, event_at) VALUES (%s, 'measured the wall', '2026-10-21 10:00+02')", th)
    ex = owner_sql("INSERT INTO exercises (name, kind) VALUES ('Test squat', 'weights') RETURNING id")[0]["id"]
    w = owner_sql("INSERT INTO workouts (day, started_at) VALUES ('2026-10-20', '2026-10-20 17:00+02') RETURNING id")[0]["id"]
    owner_sql("INSERT INTO workout_sets (workout_id, exercise_id, sets, reps, kg) VALUES (%s, %s, 3, 8, 42.5)", w, ex)
    z = owner_sql("INSERT INTO home_zones (name, short) VALUES ('Test kitchen', 'K') RETURNING id")[0]["id"]
    ch = owner_sql("INSERT INTO home_chores (name, zone_id, interval_days) VALUES ('Wipe the hob', %s, 7) RETURNING id", z)[0]["id"]
    owner_sql("INSERT INTO home_done (chore_id, zone_id, done_at, day, source) VALUES (%s, %s, '2026-10-20 19:00+02', '2026-10-20', 'web')", ch, z)
    owner_sql("""INSERT INTO coachings (kind, start_day, end_day, status, body) VALUES ('day', '2026-10-20', '2026-10-20', 'done',
                 '{"text": "zz-coaching-text"}')""")
    owner_sql("INSERT INTO private.entries (channel, label, content) VALUES ('test', 'private', %s), ('test', 'gratitude', %s)",
              "private: " + PRIVATE_WORDS, "gratitude: " + GRATITUDE_WORDS)


def _zip(r) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return {n.split("/", 1)[1]: z.read(n) for n in z.namelist()}


def _rows(files, name) -> list[dict]:
    return list(csv.DictReader(io.StringIO(files[name].decode("utf-8-sig"))))


def _download(web, **data):
    c, csrf = web
    return c.post("/web/settings/export", data={"csrf": csrf, **data}, headers=ORIGIN)


def test_the_export_tab_and_route_need_sign_in_the_app_host_and_the_form():
    assert client().post("/web/settings/export", data={}, headers=ORIGIN).headers["location"] == "/web/login"
    assert client("log.example.test").post("/web/settings/export", data={}, headers=ORIGIN).status_code == 404


def test_a_foreign_origin_or_an_old_form_gets_no_file(web):
    c, csrf = web
    assert c.post("/web/settings/export", data={"csrf": csrf}, headers={"Origin": "https://evil.example"}).status_code == 403
    r = c.post("/web/settings/export", data={"csrf": "old"}, headers=ORIGIN)
    assert r.status_code == 303 and owner_sql("SELECT count(*) AS n FROM audit_log WHERE tool = 'export'")[0]["n"] == 0


def test_the_settings_tab_offers_the_download_with_private_off(web):
    page = web[0].get("/web/settings?tab=export").text
    assert 'href="/web/settings?tab=export" aria-current="page">Export' in page
    assert 'action="/web/settings/export"' in page and 'name="private" value="1">' in page and 'value="1" checked' not in page
    assert "Last download" not in page


def test_the_download_has_every_file_and_leaves_private_entries_out(web):
    _seed()
    r = _download(web)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="logger-export-') and r.headers["cache-control"] == "no-store"
    files = _zip(r)
    want = {f"{name}.csv" for name, _w, _s in export.FILES} | {"README.txt", "all.json"}
    assert set(files) == want and "private.csv" not in files
    everything = b"".join(files.values()).decode("utf-8", "replace")
    assert PRIVATE_WORDS not in everything and GRATITUDE_WORDS not in everything
    food = _rows(files, "food_entries.csv")
    assert [(f["food"], f["amount"], f["kcal"], f["protein"], f["unit"]) for f in food] == [
        ("Test oats", "60", "222", "8.1", "g"), ("Test oats", "40", "148", "5.4", "g")]
    assert food[0]["time"] == "2026-10-20T07:30:00+02:00" and food[1]["time"] == ""  # an unknown time stays empty
    assert food[0]["day"] == "2026-10-20" and food[0]["is_drink"] == "no"
    cat = _rows(files, "foods.csv")[0]
    assert cat["aliases"] == "oatmeal; porridge oats" and cat["units"] == "cup = 80 g" and cat["kcal"] == "370"
    assert [(m["type"], m["value1"], m["unit"]) for m in _rows(files, "measurements.csv")] == [("weight", "70.4", "kg")]
    assert [e["type"] for e in _rows(files, "events.csv")] == ["bed", "wake", "yoga"]
    sleep = _rows(files, "sleeps.csv")
    assert [(s["wake_day"], s["hours"], s["nap"]) for s in sleep] == [("2026-10-20", "8", "no")]
    assert _rows(files, "thoughts.csv")[0]["tags"] == "home; diy" and _rows(files, "thought_updates.csv")[0]["update_text"] == "measured the wall"
    g = _rows(files, "gym_sets.csv")[0]
    assert (g["exercise"], g["sets"], g["reps"], g["kg"]) == ("Test squat", "3", "8", "42.5")
    h = _rows(files, "home_done.csv")[0]
    assert (h["chore"], h["zone"], h["day"]) == ("Wipe the hob", "Test kitchen", "2026-10-20")
    msg = _rows(files, "messages.csv")[0]
    assert msg["content"] == "porridge 60 g, weight 70.4" and food[0]["raw_id"] == msg["id"] == _rows(files, "events.csv")[0]["raw_id"]
    readme = files["README.txt"].decode()
    assert "Private and gratitude entries: not included" in readme and "- food_entries.csv (2):" in readme
    doc = json.loads(files["all.json"])
    assert doc["private_included"] is False and "private" not in doc["tables"] and doc["rows"]["food_entries"] == 2
    assert doc["tables"]["food_entries"][0]["kcal"] == 222 and doc["tables"]["foods"][0]["aliases"] == ["oatmeal", "porridge oats"]
    assert doc["coachings"][0]["body"] == {"text": "zz-coaching-text"}
    audit = owner_sql("SELECT outcome, client, detail FROM audit_log WHERE tool = 'export'")
    assert len(audit) == 1 and audit[0]["outcome"] == "ok" and audit[0]["client"] == "web"
    detail = audit[0]["detail"]
    assert detail["private"] is False and detail["rows"]["food_entries"] == 2 and detail["bytes"] == len(r.content)
    assert "oats" not in json.dumps(detail) and "porridge" not in json.dumps(detail)  # counts, never content


def test_private_entries_come_only_when_ticked_and_the_tab_says_so(web):
    _seed()
    files = _zip(_download(web, private="1"))
    priv = _rows(files, "private.csv")
    assert sorted((p["label"], p["content"]) for p in priv) == [("gratitude", "gratitude: " + GRATITUDE_WORDS), ("private", "private: " + PRIVATE_WORDS)]
    doc = json.loads(files["all.json"])
    assert doc["private_included"] is True and len(doc["tables"]["private"]) == 2
    assert "Private and gratitude entries: included (private.csv)" in files["README.txt"].decode()
    detail = owner_sql("SELECT detail FROM audit_log WHERE tool = 'export'")[0]["detail"]
    assert detail["private"] is True and detail["rows"]["private"] == 2 and PRIVATE_WORDS not in json.dumps(detail)
    page = web[0].get("/web/settings?tab=export").text
    assert "Last download: " in page and "with private entries" in page


def test_lab_reports_are_nested_in_the_json():
    raw = owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('test', 'lab report') RETURNING id")[0]["id"]
    test = owner_sql("INSERT INTO lab_tests (name, category) VALUES ('zz Test marker', 'lab') RETURNING id")[0]["id"]
    rep = owner_sql("""INSERT INTO lab_reports (raw_id, report_type, title, provider, collected_date, collected_at, extractor_version)
                       VALUES (%s, 'lab', 'Blood test', 'Test lab', '2026-10-01', '2026-10-01 08:00+02', 't') RETURNING id""", raw)[0]["id"]
    owner_sql("""INSERT INTO lab_results (report_id, test_id, value, unit, ref_low, ref_high, flag, extractor_version)
                 VALUES (%s, %s, 5.20, 'mmol/l', 3, 6, 'N', 't')""", rep, test)
    try:
        data, name, counts = export.build(False)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            base = name[:-4]
            doc = json.loads(z.read(f"{base}/all.json"))
            rows = list(csv.DictReader(io.StringIO(z.read(f"{base}/lab_results.csv").decode("utf-8-sig"))))
        mine = [r for r in doc["lab_reports"] if r["id"] == rep][0]
        assert mine["title"] == "Blood test" and [(x["test"], x["value"], x["unit"]) for x in mine["results"]] == [("zz Test marker", 5.2, "mmol/l")]
        assert [(x["test"], x["value"], x["ref_low"], x["flag"]) for x in rows if x["report_id"] == str(rep)] == [("zz Test marker", "5.2", "3", "N")]
        assert counts["lab_results"] >= 1
    finally:
        owner_sql("DELETE FROM lab_results WHERE report_id = %s", rep)
        owner_sql("DELETE FROM lab_reports WHERE id = %s", rep)
        owner_sql("DELETE FROM lab_tests WHERE id = %s", test)


def test_csv_cells_are_plain():
    assert export._cell(Decimal("12.50")) == "12.5" and export._cell(Decimal("100")) == "100" and export._cell(Decimal("0.000")) == "0"
    assert export._cell(None) == "" and export._cell(True) == "yes" and export._cell(["a", "b"]) == "a; b"
    assert export._cell({"b": 1, "a": Decimal("2.0")}) == '{"a": 2, "b": 1}'
