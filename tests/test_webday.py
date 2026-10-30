"""The Today page for any day (30.10.2026): a date picker with arrows, an earlier day shown read-only, and the macro pie with its share table."""
import re
from datetime import timedelta

from conftest import owner_sql
from test_web import web  # noqa: F401
from test_webhealth import measure
from test_webtoday import day, eat, food, no_targets, raw, today  # noqa: F401 - no_targets: clean targets and usual meals


def eat_back(days, name, kcal, protein, fat, carbs):
    """A food entry `days` days ago, on that logging day."""
    fid = food(name, kcal, protein, fat, carbs)
    owner_sql("""INSERT INTO food_entries (raw_id, food_id, amount, eaten_at, time_known, day, day_rule, extractor_version,
                 kcal, protein, fat, carbs, source)
                 VALUES (%s, %s, 100, now() - make_interval(days => %s), true, %s, 'wake', 'test', %s, %s, %s, %s, 'label')""",
              raw(), fid, days, day() - timedelta(days=days), kcal, protein, fat, carbs)


def at(web, d):
    r = web[0].get(f"/web/?day={d}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r.text


def pie(page):
    return re.search(r'<svg class="pie".*?</svg>', page, re.S)[0]


def macro_row(page, label):
    return re.search(rf"<tr><td><span[^>]*></span>{label}</td>.*?</tr>", page, re.S)[0]


# ---- the date picker -----------------------------------------------------------------------------

def test_today_has_a_picker_and_no_next_arrow(web):
    page = today(web)
    d = day()
    assert f'name="day" value="{d}" max="{d}"' in page and 'class="autosubmit"' in page and '<div class="kicker">Today</div>' in page
    assert f'href="/web/?day={d - timedelta(days=1)}" aria-label="Previous day"' in page
    assert 'aria-label="Next day"' not in page and 'class="ghost" href="/web/">Today</a>' not in page and "An earlier day" not in page


def test_an_earlier_day_shows_that_days_food_and_its_arrows(web):
    eat_back(1, "Test yesterday porridge", 300, 10, 5, 50)
    eat("Test todays skyr", 100, 10, 1, 4)
    d = day()
    page = at(web, d - timedelta(days=1))
    assert "Test yesterday porridge" in page and "Test todays skyr" not in page
    assert f'name="day" value="{d - timedelta(days=1)}"' in page and '<div class="kicker">Today</div>' not in page
    assert f'href="/web/?day={d}" aria-label="Next day"' in page and f'href="/web/?day={d - timedelta(days=2)}" aria-label="Previous day"' in page
    assert 'class="ghost" href="/web/">Today</a>' in page and "An earlier day" in page
    table = re.search(r'<table class="ft">.*?</table>', today(web), re.S)[0]  # (yesterday's food may be an Again chip today)
    assert "Test yesterday porridge" not in table and "Test todays skyr" in table


def test_a_day_without_food_says_so(web):
    page = at(web, day() - timedelta(days=3))
    assert "Nothing logged on this day." in page and "Nothing logged yet today." not in page and 'macrobox' not in page


def test_a_future_or_broken_day_shows_today(web):
    d = day()
    for asked in ("2999-01-01", "abc", "", "2026-13-45"):
        page = at(web, asked)
        assert f'name="day" value="{d}"' in page and "An earlier day" not in page and '<div class="kicker">Today</div>' in page, asked


def test_an_earlier_day_is_read_only(web):
    measure("weight", day() - timedelta(days=1), "07:30", 81.4)
    page = at(web, day() - timedelta(days=1))
    assert 'action="/web/habit"' not in page and 'action="/web/health"' not in page and 'action="/web/review"' not in page
    assert 'class="habit static' in page and page.count('class="hrow ro"') == 6 and "To review" not in page
    assert re.search(r'<div class="hrow ro"><span class="lbl">Weight.*?<span class="val">81.4</span>', page, re.S)
    assert "Values of this day, shown only." in page and "Repeat:" not in page and "Click to tick" not in page
    assert 'action="/web/habit"' in today(web) and 'action="/web/health"' in today(web)  # today stays editable


def test_an_empty_health_value_on_an_earlier_day_is_a_dash(web):
    page = at(web, day() - timedelta(days=5))
    assert re.search(r'<div class="hrow ro"><span class="lbl">Weight.*?<span class="val">–</span>', page, re.S)


# ---- the macro pie ---------------------------------------------------------------------------------

def test_the_pie_and_share_table_against_the_targets(web):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 100, 70, 200)")
    eat("Test oats", 400, 20, 10, 60)
    page = today(web)
    assert "Energy from macros" in page and page.count('<svg class="pie"') == 1 and pie(page).count("<path ") == 3
    assert "Calories from protein 20 %, carbs 59 %, fat 22 %" in page  # in the pie's label
    assert page.index("Energy from macros") < page.index('class="ft"')  # above the meals
    p, c, f = (macro_row(page, x) for x in ("Protein", "Carbs", "Fat"))
    assert "<td>20.0</td><td>80</td><td>19.5 %</td><td>21.9 %</td><td><span class=\"dp ok\">-2.3 pts</span></td>" in p
    assert "<td>60.0</td><td>240</td><td>58.5 %</td><td>43.7 %</td><td><span class=\"dp bad\">+14.8 pts</span></td>" in c
    assert "<td>10.0</td><td>90</td><td>22.0 %</td><td>34.4 %</td><td><span class=\"dp bad\">-12.5 pts</span></td>" in f
    assert "<td>Total</td><td></td><td>410</td><td>100 %</td><td>100 %</td>" in page and "ideal share comes from the day" in page


def test_the_difference_is_amber_between_five_and_ten_points(web):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 100, 70, 200)")
    eat("Test mix", 500, 30, 14, 48)  # protein 120 kcal, carbs 192, fat 126: 27.4 %, 43.8 %, 28.8 % against 21.9 %, 43.7 %, 34.4 %
    page = today(web)
    assert 'class="dp warn">+5.5 pts' in macro_row(page, "Protein") and 'class="dp ok">+0.1 pts' in macro_row(page, "Carbs")
    assert 'class="dp warn">-5.7 pts' in macro_row(page, "Fat")


def test_without_targets_there_is_no_ideal_share(web):
    eat("Test oats", 400, 20, 10, 60)
    page = today(web)
    assert "No targets for this day, so no ideal share." in page and "<td>–</td><td>–</td>" in macro_row(page, "Protein")
    assert "<td>100 %</td><td>–</td>" in page  # the total row


def test_one_macro_only_is_a_whole_circle(web):
    eat("Test whey", 400, 100, 0, 0)
    page = today(web)
    assert '<circle class="p"' in page and "<path " not in pie(page) and "100.0 %" in macro_row(page, "Protein")


def test_a_day_without_food_has_no_pie(web):
    assert 'macrobox' not in today(web) and '<svg class="pie"' not in today(web)


def test_an_earlier_day_has_its_own_pie_and_targets(web):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 100, 70, 200)")
    eat_back(2, "Test old dinner", 400, 20, 10, 60)
    page = at(web, day() - timedelta(days=2))
    assert page.count('<svg class="pie"') == 1 and "19.5 %" in macro_row(page, "Protein") and "21.9 %" in macro_row(page, "Protein")
    assert 'macrobox' not in today(web)
