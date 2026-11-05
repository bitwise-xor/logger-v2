"""Water as its own daily target (05.11.2026, IMPROVEMENTS item 40): a water target in ml in Settings > Food targets, a water bar
on Today. What counts (the owner's rule A): every drink by its amount, a drink in g as 1 g = 1 ml; food never counts."""
import re
from datetime import datetime, timedelta

from app import v1_commands
from app.db import connect
from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, n, web  # noqa: F401
from test_webtoday import day, eat, no_targets, raw  # noqa: F401 - no_targets: clean targets and usual meals


def water_bar(page):
    m = re.search(r'<div class="total water">.*?</svg>.*?</div></div>', page, re.S)
    return m[0] if m else ""


def drink_in_grams(name, amount):
    fid = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, is_drink) VALUES (%s, 'g', 80, 3, 3, 10, 'label', true)
                       RETURNING id""", name)[0]["id"]
    owner_sql("""INSERT INTO food_entries (raw_id, food_id, amount, eaten_at, time_known, day, day_rule, extractor_version, kcal, protein, fat, carbs, source)
                 VALUES (%s, %s, %s, now(), true, %s, 'wake', 'test', 80, 3, 3, 10, 'label')""", raw(), fid, amount, day())


def test_settings_has_a_water_target_with_history(web):
    d = day()
    page = web[0].get("/web/settings?tab=targets").text
    assert 'name="water_ml"' in page and "Water ml" in page
    form = dict(valid_from=d.isoformat(), kcal="2000", protein="", fat="", sat_fat="", carbs="", fibre="", sugar_added="", sodium_mg="", water_ml="2000")
    assert "saved: Calories 2000, Water 2000 ml." in act(web, "/web/settings/targets", **form).text
    assert float(owner_sql("SELECT water_ml FROM food_targets")[0]["water_ml"]) == 2000
    page = web[0].get("/web/settings?tab=targets").text
    assert re.search(r'name="water_ml"[^>]*value="2000"', page) and "<td>2000</td>" in page
    only = {**form, "kcal": "", "water_ml": "2500"}  # a water target alone is a target too
    assert "saved: Water 2500 ml." in act(web, "/web/settings/targets", **only).text
    assert "Water: give a number" in act(web, "/web/settings/targets", **{**form, "water_ml": "lots"}).text


def test_no_water_target_no_water_bar(web):
    owner_sql("INSERT INTO food_targets (valid_from, kcal) VALUES ('2020-01-01', 2000)")
    eat("Test tea", 1, 0, drink=True, amount=300)
    page = web[0].get("/web/").text
    assert 'class="total water"' not in page and "/web/food/water" not in page


def test_every_drink_counts_and_food_does_not(web):
    owner_sql("INSERT INTO food_targets (valid_from, water_ml) VALUES ('2020-01-01', 2000)")
    eat("Test tea", 1, 0, drink=True, amount=300)       # a drink in ml
    eat("Test kefir", 42, 3, drink=True, amount=200)    # milk drinks count too
    drink_in_grams("Test hot chocolate", 100)            # a drink weighed in g: 1 g = 1 ml
    eat("Test soup", 50, 2, amount=400)                 # food, even a wet one, does not count
    bar = water_bar(web[0].get("/web/").text)
    assert "600 ml" in bar and "/ 2,000 ml" in bar and "1,400 ml left" in bar and "all drinks" in bar
    assert 'class="fill water"' in bar and 'width="30.0"' in bar and "+ 250 ml water" in bar


def test_reaching_the_target_is_not_amber(web):
    owner_sql("INSERT INTO food_targets (valid_from, water_ml) VALUES ('2020-01-01', 500)")
    eat("Test tea", 1, 0, drink=True, amount=800)
    bar = water_bar(web[0].get("/web/").text)
    assert "target reached" in bar and "over" not in bar and 'width="100.0"' in bar


def test_the_target_valid_on_the_day_counts(web):
    d = day()
    owner_sql("INSERT INTO food_targets (valid_from, water_ml) VALUES (%s, 1500), (%s, 3000)", d - timedelta(days=30), d + timedelta(days=1))
    eat("Test tea", 1, 0, drink=True, amount=300)
    assert "/ 1,500 ml" in water_bar(web[0].get("/web/").text)


def test_the_glass_button_adds_water_once_and_logs_250_ml(web, fake):
    owner_sql("INSERT INTO food_targets (valid_from, water_ml) VALUES ('2020-01-01', 2000)")
    page = act(web, "/web/food/water", key="w-1").text
    assert 'action="/web/undo"' in page and fake.calls == []
    act(web, "/web/food/water", key="w-2")
    foods = owner_sql("SELECT id, name, basis, kcal, is_drink, created_by FROM foods WHERE name = 'Water'")
    assert len(foods) == 1 and (foods[0]["basis"], float(foods[0]["kcal"]), foods[0]["is_drink"], foods[0]["created_by"]) == ("ml", 0, True, "user")
    rows = owner_sql("SELECT amount, is_drink, meal, day FROM food_entries")
    assert [(float(r["amount"]), r["is_drink"], r["meal"], r["day"]) for r in rows] == [(250, True, 0, day())] * 2
    assert "500 ml" in water_bar(web[0].get("/web/").text)
    owner_sql("UPDATE foods SET active = false WHERE name = 'Water'")
    assert "Water is retired" in act(web, "/web/food/water", key="w-3").text
    assert n("SELECT count(*) AS n FROM food_entries") == 2


def test_an_earlier_day_shows_the_bar_without_the_button(web):
    owner_sql("INSERT INTO food_targets (valid_from, water_ml) VALUES ('2020-01-01', 2000)")
    page = web[0].get(f"/web/?day={day() - timedelta(days=1)}").text
    bar = water_bar(page)
    assert "0 ml" in bar and "2,000 ml left" in bar and "/web/food/water" not in page


def test_the_chat_sets_and_shows_the_water_target():
    with connect() as c, c.cursor() as cur:
        out = v1_commands._cf_target(cur, None, {"valid_from": "01.01.2020", "kcal": 2000, "water_ml": 2200}, False, datetime.now())
        assert "water 2,200 ml" in out or "water 2200 ml" in out
        later = v1_commands._cf_target(cur, None, {"valid_from": "01.02.2020", "kcal": 1900}, False, datetime.now())
        assert "water 2" in later  # a new period copies the water target in force then


def test_the_export_has_the_water_target(web):
    from test_export import _download, _rows, _zip
    owner_sql("INSERT INTO food_targets (valid_from, kcal, water_ml) VALUES ('2020-01-01', 2000, 2200)")
    rows = _rows(_zip(_download(web)), "food_targets.csv")
    assert [r["water_ml"] for r in rows] == ["2200"]
