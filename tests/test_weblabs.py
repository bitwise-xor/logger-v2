"""The Labs and medication page: reports newest first (2014 included) with their values and flags, the flagged values of
the newest lab report, one test over time with the reference band, medication courses; every correction is shown first
and made on Yes through the logger's change function."""
import html
import json
import re
from datetime import date, timedelta

from conftest import owner_sql
from test_web import act, client, n, web  # noqa: F401
from test_webtoday import day, raw


def lab_test(name, unit):
    """Lab tests are a catalogue the tests do not empty: reuse one by name."""
    got = owner_sql("SELECT id FROM lab_tests WHERE name = %s", name)
    return got[0]["id"] if got else owner_sql("INSERT INTO lab_tests (name, category, unit) VALUES (%s, 'lab', %s) RETURNING id", name, unit)[0]["id"]


def report(d, title="Blood test", kind="lab"):
    return owner_sql("""INSERT INTO lab_reports (raw_id, report_type, title, provider, collected_date, collected_at, extractor_version)
                        VALUES (%s, %s, %s, 'Test lab', %s, %s::date + time '08:00', 'test') RETURNING id""", raw(), kind, title, d, d)[0]["id"]


def result(rid, tid, value, unit, lo, hi, flag):
    ref = f"{lo}-{hi}" if lo is not None and hi is not None else f"<{hi}" if hi is not None else f">{lo}"
    return owner_sql("""INSERT INTO lab_results (report_id, test_id, value, unit, value_raw, unit_raw, ref_low, ref_high, ref_range_raw, flag,
                        extractor_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'test') RETURNING id""",
                     rid, tid, value, unit, str(value), unit, lo, hi, ref, flag)[0]["id"]


def course(name, dose, start, stop=None):
    return owner_sql("""INSERT INTO medications (raw_id, name, dose, frequency, start_date, stop_date, extractor_version)
                        VALUES (%s, %s, %s, 'once daily', %s, %s, 'test') RETURNING id""", raw(), name, dose, start, stop)[0]["id"]


def seed():
    ldl, vitd = lab_test("Test LDL", "mg/dL"), lab_test("Test vitamin D", "ng/mL")
    r14, r25, r26 = report(date(2014, 10, 23)), report(date(2025, 9, 15)), report(date(2026, 10, 2), "Annual check-up")
    img = report(date(2026, 10, 10), "Ultrasound", "imaging")
    v = {"ldl14": result(r14, ldl, 142, "mg/dL", None, 116, "H"), "ldl25": result(r25, ldl, 162, "mg/dL", None, 116, "H"),
         "ldl26": result(r26, ldl, 168, "mg/dL", None, 116, "H"), "vitd26": result(r26, vitd, 33, "ng/mL", 30, 100, "N")}
    return {"ldl": ldl, "vitd": vitd, "r14": r14, "r25": r25, "r26": r26, "img": img, **v}


def text(page):
    return html.unescape(page.text)


def test_the_pages_need_sign_in_and_the_app_host():
    for path in ("/web/labs", "/web/labs/tests", "/web/labs/meds"):
        assert client().get(path).headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/labs").status_code == 404


def test_reports_newest_first_with_2014_and_the_flagged_values(web):
    s = seed()
    page = text(web[0].get("/web/labs"))
    assert 'href="/web/labs" aria-current="page">Labs and medication' in page
    table = page.split("<table")[1]
    assert table.index("10.10.2026") < table.index("02.10.2026") < table.index("15.09.2025") < table.index("23.10.2014")  # 2014 kept
    flagged = page.split("Flagged in your newest report")[1].split("</div>")[0]
    assert "02.10.2026" in flagged and "Test LDL" in flagged and "Test vitamin D" not in flagged  # the newest LAB report
    opened = text(web[0].get(f"/web/labs?open=R{s['r26']}"))
    assert "168 mg/dL" in opened and "<116" in opened and '<span class="flag H" title="high">H</span>' in opened


def test_a_value_correction_is_shown_first_then_made(web):
    s = seed()
    form = dict(id=f"V{s['ldl26']}", value="110", unit="mg/dL", ref="<116", back=f"/web/labs?open=R{s['r26']}")
    page = act(web, "/web/labs/value", **form)
    assert "please confirm" in page.text and "Yes, change" in page.text and 'href="/web/labs?open=R' in page.text
    assert float(owner_sql("SELECT value FROM lab_results WHERE id = %s", s["ldl26"])[0]["value"]) == 168
    page = act(web, "/web/labs/value", **form, confirmed="1")
    r = owner_sql("SELECT value, flag FROM lab_results WHERE id = %s", s["ldl26"])[0]
    assert (float(r["value"]), r["flag"]) == (110, "N") and "Changed Test LDL." in page.text  # v1 set the flag again
    assert "Nothing changed" in act(web, "/web/labs/value", **form).text


def test_a_report_correction(web):
    s = seed()
    form = dict(id=f"R{s['r25']}", title="Lipid panel", provider="Test lab", collected="2025-09-16", notes="fasting")
    assert "please confirm" in act(web, "/web/labs/report", **form).text
    act(web, "/web/labs/report", **form, confirmed="1")
    p = owner_sql("SELECT title, collected_date, notes FROM lab_reports WHERE id = %s", s["r25"])[0]
    assert (p["title"], p["collected_date"], p["notes"]) == ("Lipid panel", date(2025, 9, 16), "fasting")


def test_one_test_over_time_with_the_band(web):
    s = seed()
    c, _ = web
    page = c.get(f"/web/labs/tests?t={s['ldl']}").text
    data = json.loads(html.unescape(re.search(r'data-chart="([^"]*)"', page).group(1)))
    assert data["band"] == [None, 116.0] and data["years"] and len(data["series"][0]["pts"]) == 3
    assert [len(x["pts"]) for x in data["series"][1:]] == [0, 3, 0]  # all three outside the range (amber)
    assert "3 values" in page and "23.10.2014" in page
    assert "Test vitamin D" in text(c.get("/web/labs/tests?q=vitamin"))  # one match: shown at once
    assert "Only one value so far" in text(c.get("/web/labs/tests?q=vitamin"))
    assert "No test with that name" in text(c.get("/web/labs/tests?q=zzz"))


def test_medication_end_and_change_after_a_yes(web):
    d = day()
    vit = course("Test vitamin D3", "1000 IU", d - timedelta(days=30))
    old = course("Test ibuprofen", "400 mg", date(2024, 5, 10), date(2024, 5, 24))
    page = text(web[0].get("/web/labs/meds"))
    assert page.index("Test vitamin D3") < page.index("Test ibuprofen") and "1 running" in page
    assert "Yes, end it today" in act(web, "/web/labs/med", id=f"D{vit}", act="end").text
    act(web, "/web/labs/med", id=f"D{vit}", act="end", confirmed="1")
    assert owner_sql("SELECT stop_date FROM medications WHERE id = %s", vit)[0]["stop_date"] == d
    form = dict(id=f"D{vit}", act="save", dose="2000 IU", frequency="once daily", indication="", start=(d - timedelta(days=30)).isoformat(), stop="")
    assert "please confirm" in act(web, "/web/labs/med", **form).text
    act(web, "/web/labs/med", **form, confirmed="1")
    m = owner_sql("SELECT dose, stop_date FROM medications WHERE id = %s", vit)[0]
    assert (m["dose"], m["stop_date"]) == ("2000 IU", None)  # an empty stop date means running again
    assert "has a stop date already" in act(web, "/web/labs/med", id=f"D{old}", act="end").text


def test_bad_input_changes_nothing(web):
    s = seed()
    assert "Unknown lab value" in act(web, "/web/labs/value", id="V1; drop", value="1").text
    assert "cannot be empty" in act(web, "/web/labs/value", id=f"V{s['ldl26']}", value=" ").text
    assert "not there any more" in act(web, "/web/labs/value", id="V999999", value="1").text
    assert "Give the report's date" in text(act(web, "/web/labs/report", id=f"R{s['r26']}", title="x", provider="y", collected="soon"))
    assert "Unknown medication course" in act(web, "/web/labs/med", id="D1 or 1", act="end").text
    assert float(owner_sql("SELECT value FROM lab_results WHERE id = %s", s["ldl26"])[0]["value"]) == 168
    assert n("SELECT count(*) AS n FROM lab_reports WHERE title = 'x'") == 0
