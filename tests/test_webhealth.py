"""The Health page: four charts over 7, 30 or 90 days, the habits calendar, and one row per day whose values are
corrected, deleted (after one yes) or added in place. New values make no AI call; corrections and deletions go
through the logger's own change function."""
import html
import json
import re
from datetime import date, datetime, timedelta, timezone

from app.when import BERLIN
from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, client, n, web  # noqa: F401
from test_webtoday import raw


def today() -> date:
    return owner_sql("SELECT log_day(now()) AS d")[0]["d"]


def at(day: date, hm: str) -> str:
    return f"{day:%d.%m.%Y} {hm}"


def event(name: str, day: date, hm: str) -> int:
    ts = datetime.combine(day, datetime.strptime(hm, "%H:%M").time(), BERLIN)
    return owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                        SELECT %s, id, log_day(%s), %s, true, 'test' FROM log_types WHERE name = %s RETURNING id""", raw(), ts, ts, name)[0]["id"]


def measure(name: str, day: date, hm: str, v1: float, v2=None, period=None) -> int:
    ts = datetime.combine(day, datetime.strptime(hm, "%H:%M").time(), BERLIN)
    return owner_sql("""INSERT INTO measurements (raw_id, type_id, day, event_at, time_known, value1, value2, period, extractor_version)
                        SELECT %s, id, log_day(%s), %s, true, %s, %s, %s, 'test' FROM log_types WHERE name = %s RETURNING id""",
                     raw(), ts, ts, v1, v2, period, name)[0]["id"]


def hours(d1: date, hm1: str, d2: date, hm2: str) -> float:
    a, b = (datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), BERLIN) for d, hm in ((d1, hm1), (d2, hm2)))
    return round((b.astimezone(timezone.utc) - a.astimezone(timezone.utc)).total_seconds() / 3600, 2)


def row(page, day: date) -> dict:
    """A day's row as the page fills its fields."""
    fid = f"hd-{day.isoformat()}"
    return dict(re.findall(rf'name="(\w+)" form="{fid}"[^>]*? value="([^"]*)"', page.text))


def charts(page) -> dict:
    return {m[0]: json.loads(html.unescape(m[1])) for m in re.findall(r'<div class="chart" id="([\w-]+)" data-chart="([^"]*)"', page.text)}


def save(web, day: date, **values):
    """A row's Save: every field of the row is sent, the ones not given as they are now."""
    c, _ = web
    now = row(c.get("/web/health?r=90"), day)
    return act(web, "/web/healthday", day=day.isoformat(), r="30", key=f"k{len(owner_sql('SELECT 1 FROM raw_inbox'))}-{day}", **(now | values))


def weights():
    return [(r["day"], float(r["value1"])) for r in owner_sql("""SELECT m.day, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id
                                                                  WHERE t.name = 'weight' ORDER BY m.day""")]


def test_the_page_needs_sign_in_and_the_app_host():
    assert client().get("/web/health").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/health").status_code == 404


def test_one_row_per_day_with_the_bed_time_of_the_evening_before(web, fake):
    d = today()
    y = d - timedelta(days=1)
    measure("weight", y, "07:05", 82.4)
    measure("bp", y, "07:10", 124, 81, "morning")
    measure("bp", y, "21:30", 130, 85, "evening")
    event("bed", y - timedelta(days=1), "23:10")
    event("wake", y, "06:40")
    event("bed", d, "00:20")  # after midnight: still the night before today
    event("wake", d, "06:50")
    page = web[0].get("/web/health")
    assert 'href="/web/health" aria-current="page">Health' in page.text
    assert row(page, y) == {"w": "82.4", "bm1": "124", "bm2": "81", "be1": "130", "be2": "85", "s": "", "bed": "23:10", "wake": "06:40"}
    assert (row(page, d)["bed"], row(page, d)["wake"]) == ("00:20", "06:50")
    first = hours(y - timedelta(days=1), "23:10", y, "06:40")  # real elapsed time: 8.5 h across the end of summer time
    shown = lambda h: f"{int(h)} h {round(h % 1 * 60):02d}"
    assert shown(first) in page.text and "6 h 30" in page.text
    sleep = charts(page)["c-sleep"]["series"][0]["pts"]
    assert [p[1] for p in sleep] == [first, 6.5] and "bed 23:10, up 06:40" in sleep[0][2]


def test_the_range_switch_and_the_charts(web, fake):
    d = today()
    for back, kg in ((1, 82.4), (10, 83.0), (40, 84.1)):
        measure("weight", d - timedelta(days=back), "07:00", kg)
    c, _ = web
    dots = lambda r: [p[1] for p in charts(c.get(f"/web/health?r={r}"))["c-weight"]["series"][1]["pts"]]
    assert dots(7) == [82.4] and dots(30) == [83.0, 82.4] and dots(90) == [84.1, 83.0, 82.4]
    page = c.get("/web/health?r=999")
    assert 'href="/web/health?r=30" aria-current="page"' in page.text  # an unknown range is the default
    assert "−0.6 kg</b> in 30 days" in page.text
    assert len(re.findall(r'<form class="hday"', page.text)) == 14 and "Show all 30 days" in page.text
    assert len(re.findall(r'<form class="hday"', c.get("/web/health?r=30&all=1").text)) == 30


def test_correcting_a_past_value_is_saved_at_once(web, fake):
    y = today() - timedelta(days=1)
    measure("weight", y, "07:05", 82.4)
    page = save(web, y, w="82,1")
    assert "Saved for" in page.text and "Weight 82.4 → 82.1" in page.text
    assert weights() == [(y, 82.1)] and fake.calls == []
    assert owner_sql("SELECT outcome, client FROM audit_log WHERE tool = 'web_change'") == [{"outcome": "changed", "client": "web"}]
    assert row(page, y)["w"] == "82.1"


def test_values_added_to_an_empty_day_go_to_that_day_without_an_ai_call(web, fake):
    y = today() - timedelta(days=3)
    page = save(web, y, w="81.9", bm1="120", bm2="80", s="92", wake="6:30", bed="23:15")
    assert fake.calls == [], page.text
    assert weights() == [(y, 81.9)]
    assert row(page, y) == {"w": "81.9", "bm1": "120", "bm2": "80", "be1": "", "be2": "", "s": "92", "bed": "23:15", "wake": "06:30"}
    bed = owner_sql("""SELECT to_char(e.event_at AT TIME ZONE 'Europe/Berlin', 'DD.MM. HH24:MI') AS t FROM events e
                       JOIN log_types t ON t.id = e.type_id WHERE t.name = 'bed'""")[0]["t"]
    assert bed == f"{y - timedelta(days=1):%d.%m.} 23:15"
    assert {r["channel"] for r in owner_sql("SELECT channel FROM raw_inbox")} == {"web"}


def test_clearing_a_value_asks_once_then_deletes_it(web, fake):
    y = today() - timedelta(days=1)
    measure("weight", y, "07:05", 82.4)
    page = save(web, y, w="")
    assert "please confirm" in page.text and "Delete on" in page.text and "Yes, delete" in page.text
    assert 'href="/web/health?r=30">No</a>' in page.text
    assert weights() == [(y, 82.4)]
    page = save(web, y, w="", confirm_del="1")
    assert weights() == [] and "Weight 82.4 deleted" in page.text


def test_an_odd_correction_asks_first_then_saves_on_yes(web, fake):
    y = today() - timedelta(days=1)
    measure("weight", y, "07:05", 82.4)
    page = save(web, y, w="400")
    assert "please confirm" in page.text and "Yes, save it" in page.text
    assert weights() == [(y, 82.4)]
    save(web, y, w="400", confirmed="1")
    assert weights() == [(y, 400.0)]


def test_bad_input_changes_nothing(web, fake):
    d = today()
    y = d - timedelta(days=1)
    measure("bp", y, "07:10", 124, 81, "morning")
    assert "give both numbers" in save(web, y, bm1="130", bm2="").text
    assert "give a number" in save(web, y, w="heavy").text
    assert "hh:mm" in save(web, y, wake="25:00").text
    assert "last 12 months" in save(web, d - timedelta(days=400), w="80").text
    assert "last 12 months" in save(web, d + timedelta(days=1), w="80").text
    assert "Unknown day" in act(web, "/web/healthday", day="drop table", r="30", w="80").text
    assert "Nothing changed" in save(web, y).text
    assert n("SELECT count(*) AS n FROM measurements") == 1 and weights() == []


def test_the_habits_calendar(web, fake):
    d = today()
    event("meds", d, "08:00")
    event("shoonya", d, "07:30")
    owner_sql("UPDATE log_types SET daily_target = 2 WHERE name = 'shoonya'")  # two sessions a day: one is "partly"
    try:
        page = web[0].get("/web/health")
    finally:
        owner_sql("UPDATE log_types SET daily_target = 1 WHERE name = 'shoonya'")
    assert re.search(r'Medication <span class="small">1/30</span>', page.text)
    assert f'<i class="d" title="{d:%a %d.%m.}: 1 of 1"></i>' in page.text
    assert f'<i class="h" title="{d:%a %d.%m.}: 1 of 2"></i>' in page.text
