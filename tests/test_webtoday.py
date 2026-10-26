"""The web app's Today page: food with four macros against targets, usual meals, habits, the Health card, review items."""
import re

import pytest

from app import service
from conftest import owner_sql
from test_web import web  # noqa: F401


@pytest.fixture(autouse=True)
def no_targets():
    owner_sql("DELETE FROM food_targets")
    owner_sql("TRUNCATE usual_meals CASCADE")
    yield
    owner_sql("DELETE FROM food_targets")
    owner_sql("TRUNCATE usual_meals CASCADE")


def today(web):
    r = web[0].get("/web/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r.text


def day():
    return owner_sql("SELECT log_day(now()) AS d")[0]["d"]


def raw():
    return owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('test', 'by hand') RETURNING id")[0]["id"]


def food(name, kcal, protein, fat=1, carbs=1, drink=False):
    return owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, is_drink) VALUES (%s, %s, %s, %s, %s, %s, 'label', %s)
                        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                     name, "ml" if drink else "g", kcal, protein, fat, carbs, drink)[0]["id"]


def eat(name, kcal, protein, fat=1, carbs=1, amount=100, meal_time="now()", drink=False, review=False, portion=None):
    fid = food(name, kcal, protein, fat, carbs, drink)
    owner_sql(f"""INSERT INTO food_entries (raw_id, food_id, amount, portion_text, eaten_at, time_known, day, day_rule, extractor_version,
                  kcal, protein, fat, carbs, source, needs_review, review_reason)
                  VALUES (%s, %s, %s, %s, {meal_time}, true, %s, 'wake', 'test', %s, %s, %s, %s, 'label', %s, %s)""",
              raw(), fid, amount, portion, day(), kcal, protein, fat, carbs, review, "odd value" if review else None)
    return fid


def test_empty_day(web):
    page = today(web)
    assert "Nothing logged yet today." in page and "Nothing to review." in page
    assert "Waiting for your answer" not in page


def test_four_macros_with_targets_and_one_column_each(web):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 100, 70, 200)")
    eat("Test oats", 400, 20, 10, 60, 100, "now() - interval '5 hours'", portion="1 bowl")
    eat("Test skyr", 100, 10, 1, 4, 150, "now()")
    page = today(web)
    for want in ("Calories", "500", "/ 2,000 kcal", "Protein", "30 g", "/ 100 g", "Fat", "11 g", "/ 70 g", "Carbs", "64 g", "/ 200 g",
                 "Meal 1", "Meal 2", "Test oats", "1 bowl", "Test skyr", "150 g"):
        assert want in page, want
    assert "Protein g</span>" in page and "Carbs g</span>" in page  # the labels sit in the header
    row = re.search(r"Test oats.*?</tr>", page, re.S)[0]
    assert re.findall(r"<td>([\d,]+)</td>", row) == ["400", "20", "10", "60"] and "kcal" not in row
    assert 'class="meter"' in page and 'width="25.0"' in page  # 500 of 2000 kcal


def test_drinks_have_their_own_rows(web):
    eat("Test latte", 90, 5, drink=True, amount=250)
    page = today(web)
    assert "Drinks" in page and "Test latte" in page and "250 ml" in page


def test_without_targets_there_are_no_bars(web):
    eat("Test oats", 400, 20)
    page = today(web)
    assert "400" in page and 'class="meter"' not in page and "/ 2,000" not in page


def test_usual_meals_open_with_editable_weights(web):
    oats, skyr = food("Test oats", 380, 13, 7, 60), food("Test skyr", 63, 11, 0.2, 4)
    mid = owner_sql("INSERT INTO usual_meals (name) VALUES ('usual breakfast') RETURNING id")[0]["id"]
    owner_sql("INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order) VALUES (%s, %s, 60, 1), (%s, %s, 200, 2)", mid, oats, mid, skyr)
    page = today(web)
    assert "+ usual breakfast" in page and 'action="/web/usual"' in page
    assert re.search(r'name="g_\d+"[^>]*value="60"', page) and re.search(r'name="g_\d+"[^>]*value="200"', page)
    assert 'data-per100="380.0,13.0,7.0,60.0"' in page
    tot = re.search(r'<tr class="sum">.*?</tr>', page, re.S)[0]
    assert re.findall(r"<td class=m>([\d,]+)</td>", tot) == ["354", "30", "5", "44"]  # 60 g oats + 200 g skyr


def test_habits_are_toggles(web):
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                 SELECT %s, id, log_day(now()), now(), true, 'test' FROM log_types WHERE name = 'meds'""", raw())
    eid = owner_sql("SELECT id FROM events")[0]["id"]
    page = today(web)
    meds = re.search(r'<form[^>]*action="/web/habit">(?:(?!</form>).)*?value="meds".*?</form>', page, re.S)[0]
    assert 'value="untick"' in meds and f'name="eid" value="{eid}"' in meds and 'aria-pressed="true"' in meds
    assert 'value="tick"' in page and "of" in page and "done" in page


def test_health_card_shows_todays_values_in_fields(web):
    service.record_weight(81.5)
    service.record_bp(124, 81, "morning")
    page = today(web)
    w = re.search(r'<form class="hrow"[^>]*>(?:(?!</form>).)*?value="weight".*?</form>', page, re.S)[0]
    assert 'value="81.5"' in w and re.search(r'name="id" value="m\d+"', w)
    bp = re.search(r'<form class="hrow"[^>]*>(?:(?!</form>).)*?value="bp_morning".*?</form>', page, re.S)[0]
    assert 'value="124"' in bp and 'value="81"' in bp
    ev = re.search(r'<form class="hrow"[^>]*>(?:(?!</form>).)*?value="bp_evening".*?</form>', page, re.S)[0]
    assert 'name="id" value=""' in ev
    assert "Health" in page and "Vitals" not in page


def test_review_items_are_listed_escaped_with_three_buttons(web):
    eat("Test <b>odd</b> bar", 900, 2, review=True)
    owner_sql("""INSERT INTO thoughts (raw_id, original_text, summary, needs_review, review_reason)
                 VALUES (%s, 'call the landlord', 'call the landlord', true, 'nothing extracted')""", raw())
    page = today(web)
    assert "odd value" in page and "<b>odd</b>" not in page and "&lt;b&gt;odd&lt;/b&gt;" in page
    assert '"call the landlord"' in page and "nothing extracted" in page
    assert page.count("Looks right") == 2 and page.count("<summary>Change</summary>") == 2 and page.count("<summary>Delete</summary>") == 2


def test_page_needs_a_session(web):
    eat("Test oats", 400, 20)
    from test_web import client
    r = client().get("/web/")
    assert r.status_code == 302 and "Test oats" not in r.text
