"""A usual meal logged at once is one line in Today's food table (08.11.2026): "usual breakfast · 3 items" with the sums, opening
(CSS only) to its items, each still changeable; foods logged on their own stay single rows."""
import re

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, web  # noqa: F401
from test_webactions import breakfast, skyr  # noqa: F401
from test_webfood import eat_on, oats
from test_webtoday import day, no_targets  # noqa: F401 - no_targets: clean targets and usual meals


def test_a_usual_meal_is_one_line_that_opens_to_its_items(web, fake, breakfast):
    mid, _, _ = breakfast
    act(web, "/web/usual", meal=str(mid), key="g1", quick="1")
    eat_on(day(), "06:00", oats(), 30)  # a food on its own, earlier: stays a single row
    page = web[0].get("/web/").text
    ug = re.search(r'<tbody class="ug[^"]*">.*?</tbody>', page, re.S)[0]
    assert '<details class="ugt"><summary title="Show the 3 items">usual breakfast <span class="small">· 3 items</span></summary>' in ug
    sums = owner_sql("SELECT round(sum(kcal)) AS k FROM food_entries WHERE usual_meal_id = %s", mid)[0]["k"]
    assert f"<td>{int(sums):,}</td>" in ug
    assert ug.count('<tr class="item gi">') == 3 and ug.count('<details class="fe">') == 3  # each item still changeable
    assert page.count('<tbody class="ug') == 1 and 'class="mealrow"' in page
    meal = page[page.index('class="mealrow"'):]
    assert meal.index("Test oats") < meal.index('<tbody class="ug')  # the earlier single food comes first, in its own tbody


def test_two_loggings_of_one_usual_meal_are_two_lines(web, fake, breakfast):
    mid, _, _ = breakfast
    act(web, "/web/usual", meal=str(mid), key="g2", quick="1")
    owner_sql("UPDATE raw_inbox SET received_at = received_at - interval '1 hour'")  # not the repeat question
    act(web, "/web/usual", meal=str(mid), key="g3", quick="1")
    page = web[0].get("/web/").text
    assert page.count('<tbody class="ug') == 2
