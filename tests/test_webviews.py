"""The Food trends tab, the Week page and the search (29.10.2026): calories, protein and fat over complete days only."""
from datetime import date, timedelta

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import client, web  # noqa: F401
from test_webfood import eat_on
from test_webhealth import charts, measure
from test_webtoday import day, food, no_targets, raw  # noqa: F401 - no_targets: clean targets and usual meals
from app import weekpage


def targets():
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 100, 70, 200)")


def eat_days(f, kcals):
    """One meal on each of the days before today (1 day ago, 2 days ago ...) with the given kcal; protein 10, fat 2, carbs 5."""
    for i, k in enumerate(kcals, 1):
        eat_on(day() - timedelta(days=i), "12:00", f, 100, k)


# ---- trends ----------------------------------------------------------------------------------------

def test_the_trends_tab_needs_sign_in_and_the_app_host():
    assert client().get("/web/food/trends").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/food/trends").status_code == 404


def test_trends_show_calories_protein_and_fat_over_complete_days(web):
    targets()
    f = food("Test oats", 400, 13, 7, 60)
    eat_days(f, [2000, 2200, 1800])
    eat_on(day(), "08:00", f, 100, 9999)  # today is not over: left out
    page = web[0].get("/web/food/trends")
    assert 'href="/web/food/trends" aria-current="page">Trends' in page.text and "Last 30 complete days" in page.text and "today not counted" in page.text
    ch = charts(page)
    assert set(k for k in ch if k.startswith("c-trend-")) == {"c-trend-kcal", "c-trend-protein", "c-trend-fat"}
    kcal = ch["c-trend-kcal"]
    assert kcal["days"] == 30 and kcal["min"] == 0 and kcal["lines"] == [[2000.0, "t", "2,000"]]
    bars, avg = kcal["series"]
    assert sorted(p[1] for p in bars["pts"]) == [1800, 2000, 2200] and len(avg["pts"]) == 3 and max(p[1] for p in bars["pts"]) < 9999
    assert ch["c-trend-protein"]["lines"] == [[100.0, "t", "100"]] and ch["c-trend-fat"]["lines"] == [[70.0, "t", "70"]]
    assert "average <b>2,000 kcal</b> · target 2,000 · over the target on <b>1</b> of 3 days" in page.text
    assert "average <b>10 g</b> · target 100 · target reached on <b>0</b> of 3 days" in page.text
    assert "average <b>2 g</b> · target 70 · over the target on <b>0</b> of 3 days" in page.text


def test_trend_ranges_and_an_empty_range(web):
    assert "Last 7 complete days" in web[0].get("/web/food/trends?r=7").text and "Last 90 complete days" in web[0].get("/web/food/trends?r=90").text
    page = web[0].get("/web/food/trends?r=999")
    assert "Last 30 complete days" in page.text and "no complete days with food in this range" in page.text
    assert charts(page)["c-trend-kcal"]["series"][0]["pts"] == [] and "lines" not in charts(page)["c-trend-kcal"]  # no target without data


# ---- the Week page ---------------------------------------------------------------------------------

def test_the_week_page_needs_sign_in_and_the_app_host():
    assert client().get("/web/week").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/week").status_code == 404


def test_the_periods():
    wed = date(2026, 10, 28)  # a Wednesday
    assert weekpage.bounds("7", wed) == (date(2026, 10, 21), date(2026, 10, 27))
    assert weekpage.bounds("30", wed) == (date(2026, 9, 28), date(2026, 10, 27))
    assert weekpage.bounds("last", wed) == (date(2026, 10, 19), date(2026, 10, 25))
    assert weekpage.bounds("this", wed) == (date(2026, 10, 26), date(2026, 10, 27))
    start, end = weekpage.bounds("this", date(2026, 10, 26))  # on a Monday nothing of the week is over
    assert end < start


def test_the_week_has_food_against_targets_health_foods_and_the_days(web):
    targets()
    f = food("Test oats", 400, 13, 7, 60)
    g = food("Test skyr", 63, 11, 0.2, 4)
    eat_days(f, [2000, 2400, 1600])
    eat_on(day() - timedelta(days=1), "18:00", g, 100, 500)
    d = day() - timedelta(days=2)
    measure("weight", d, "07:00", 82.0)
    measure("weight", d + timedelta(days=1), "07:00", 81.4)
    measure("bp", d, "07:10", 120, 80, "morning")
    measure("blood_sugar", d, "07:12", 96, None, "fasted")
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                 SELECT %s, id, %s, now(), true, 'test' FROM log_types WHERE name = 'meds'""", raw(), d)
    page = web[0].get("/web/week")
    t = page.text
    assert 'aria-current="page">Last 7 days' in t and "3 of 7 days with food" in t and "today is left out" in t
    for want in ("Calories per day", "Protein per day", "Fat per day", "Carbs per day", "/ 2,000 kcal", "/ 100 g", "/ 70 g", "/ 200 g"):
        assert want in t, want
    assert "Average day as a share of the target" in t and "2,167" in t  # (2000 + 500, 2400, 1600) / 3
    assert "81.4 kg" in t and "−0.6 kg since" in t and "120/80" in t and "96 mg/dL" in t and "of habit-days done" in t
    assert "Test oats" in t and "Test skyr" in t and "Most calories" in t and "Eaten most often" in t
    assert t.count('<tr><td>') >= 3 and "The days" in t
    ch = charts(page)
    assert set(k for k in ch if k.startswith("c-trend-")) == {"c-trend-kcal", "c-trend-protein", "c-trend-fat"} and ch["c-trend-kcal"]["days"] == 7


def test_other_periods_and_an_unknown_one(web):
    for p, label in (("last", "Last week"), ("30", "Last 30 days"), ("this", "This week so far")):
        assert f'aria-current="page">{label}' in web[0].get(f"/web/week?p={p}").text
    assert 'aria-current="page">Last 7 days' in web[0].get("/web/week?p=nonsense").text
    assert "No complete day in this period yet" in web[0].get("/web/week?p=this").text or "days with food" in web[0].get("/web/week?p=this").text


# ---- search ----------------------------------------------------------------------------------------

def test_search_needs_sign_in_and_two_letters(web):
    assert client().get("/web/search?q=oats").headers["location"] == "/web/login"
    assert "Type at least two letters" in web[0].get("/web/search").text and "Type at least two letters" in web[0].get("/web/search?q=a").text
    assert "Private and gratitude entries are searched on the Private page only" in web[0].get("/web/search?q=oats").text


def test_search_finds_foods_what_was_eaten_notes_and_thoughts(web):
    f = food("Test oats", 400, 13, 7, 60)
    owner_sql("UPDATE foods SET aliases = '{haferflocken}' WHERE id = %s", f)
    eid = eat_on(day(), "08:00", f, 60)
    owner_sql("UPDATE food_entries SET note = 'with <b>honey</b>' WHERE id = %s", eid)
    owner_sql("""INSERT INTO thoughts (raw_id, original_text, summary, thought_type, area) VALUES (%s, 'buy haferflocken in bulk', 'Oats in bulk', 'task', 'personal')""", raw())
    page = web[0].get("/web/search?q=haferflocken").text
    assert f'href="/web/food/catalogue?q=haferflocken&amp;food=N{f}#N{f}"' in page and "Test oats" in page
    assert "Oats in bulk" in page and "stage=all" in page
    by_name = web[0].get("/web/search?q=OATS").text
    assert f'href="/web/food?day={day().isoformat()}"' in by_name and "60 g" in by_name
    note = web[0].get("/web/search?q=honey").text
    assert "with &lt;b&gt;honey&lt;/b&gt;" in note and "<b>honey</b>" not in note


def test_search_treats_percent_and_underscore_as_plain_letters(web):
    food("Half 50% juice", 40, 0)
    food("Fifty juice", 40, 0)
    page = web[0].get("/web/search?q=50%25").text
    assert "Half 50% juice" in page and "Fifty juice" not in page
    assert "Nothing found." in web[0].get("/web/search?q=a_c").text
