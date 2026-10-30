"""Round 1 of the frictionless web app (31.10.2026): the Undo bar (a delete or a correction put back, a logged message
undone), the food editor on Today, Again chips, food suggestions for the entry box and the Recent page."""
import re
from datetime import timedelta

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, client, web  # noqa: F401
from test_webfood import eat_on, entry, oats
from test_webtoday import day, no_targets  # noqa: F401 - no_targets: clean targets and usual meals


def token(page: str) -> str:
    return re.search(r'action="/web/undo/last">.*?name="token" value="([^"]+)"', page, re.S)[1]


def ticks() -> int:
    return owner_sql("SELECT count(*) AS n FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = 'meds'")[0]["n"]


# ---- the Undo bar ----------------------------------------------------------------------------------

def test_a_food_changed_on_today_is_changed_at_once_and_undone_from_the_bar(web, fake):
    eid = eat_on(day(), "08:10", oats(), 60, 240)
    page = web[0].get("/web/").text
    assert f'name="id" value="f{eid}"' in page and 'name="back" value="/web/"' in page and 'class="undobar"' not in page
    page = act(web, "/web/food/entry", id=f"f{eid}", amount="80", time="08:10", act="save", back="/web/").text
    assert "Changed Test oats." in page and float(entry(eid)["amount"]) == 80 and 'class="undobar"' in page
    page = act(web, "/web/undo/last", token=token(page), back="/web/").text
    assert "Undone." in page and float(entry(eid)["amount"]) == 60 and 'class="undobar"' not in page


def test_a_deleted_food_comes_back_with_its_id(web, fake):
    eid = eat_on(day(), "08:10", oats(), 60)
    page = act(web, "/web/food/entry", id=f"f{eid}", act="delete", amount="60", back="/web/").text
    assert "Deleted Test oats." in page and entry(eid) is None
    act(web, "/web/undo/last", token=token(page), back="/web/")
    assert float(entry(eid)["amount"]) == 60


def test_the_bar_stays_on_other_pages_and_goes_with_the_next_action(web, fake):
    eid = eat_on(day(), "08:10", oats(), 60)
    first = act(web, "/web/food/entry", id=f"f{eid}", amount="70", time="08:10", act="save", back="/web/").text
    assert 'class="undobar"' in web[0].get("/web/health").text  # still offered after moving to another page
    act(web, "/web/food/entry", id=f"f{eid}", amount="75", time="08:10", act="save", back="/web/")
    page = act(web, "/web/undo/last", token=token(first), back="/web/").text  # the first change's Undo is gone
    assert "Nothing undone." in page and float(entry(eid)["amount"]) == 75


def test_a_tick_and_an_untick_are_undone(web, fake):
    page = act(web, "/web/habit", name="meds", action="tick", key="r1-a").text
    assert ticks() == 1 and re.search(r'class="undobar".*?action="/web/undo".*?name="raw_id"', page, re.S)
    eid = owner_sql("SELECT e.id FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = 'meds'")[0]["id"]
    page = act(web, "/web/habit", name="meds", action="untick", eid=str(eid)).text
    assert ticks() == 0
    act(web, "/web/undo/last", token=token(page), back="/web/")
    assert ticks() == 1


def test_a_corrected_health_value_is_undone(web, fake):
    act(web, "/web/health", row="weight", id="", v1="82.4", key="r1-w")
    mid = owner_sql("SELECT m.id FROM measurements m JOIN log_types t ON t.id = m.type_id WHERE t.name = 'weight'")[0]["id"]
    page = act(web, "/web/health", row="weight", id=f"m{mid}", v1="81.9").text
    value = lambda: float(owner_sql("SELECT value1 FROM measurements WHERE id = %s", mid)[0]["value1"])  # noqa: E731
    assert value() == 81.9
    act(web, "/web/undo/last", token=token(page), back="/web/")
    assert value() == 82.4


# ---- Again chips -----------------------------------------------------------------------------------

def test_again_chips_log_a_recent_food_with_the_amount_typed(web, fake):
    oid = oats()
    eat_on(day(), "08:10", oid, 60)
    page = web[0].get("/web/").text
    assert "Again:" in page and 'action="/web/food/again"' in page and f'name="food" value="{oid}"' in page and 'value="60"' in page
    assert "Again:" not in web[0].get(f"/web/?day={day() - timedelta(days=1)}").text  # an earlier day is read-only
    page = act(web, "/web/food/again", food=str(oid), amount="45", time="09:30", key="r1-ag").text
    assert sorted(float(r["amount"]) for r in owner_sql("SELECT amount FROM food_entries")) == [45.0, 60.0]
    assert fake.calls == [] and 'action="/web/undo"' in page
    assert "above 0" in act(web, "/web/food/again", food=str(oid), amount="0").text
    assert "hh:mm" in act(web, "/web/food/again", food=str(oid), amount="5", time="25:00").text
    assert "Unknown food" in act(web, "/web/food/again", food="x", amount="5").text


# ---- suggestions and the two-line box --------------------------------------------------------------

def test_the_suggestion_list_has_the_foods_with_their_last_amount(web):
    eat_on(day(), "08:10", oats(), 60)
    foods = web[0].get("/web/foods.json").json()
    o = next(f for f in foods if f["n"] == "Test oats")
    assert (o["a"], o["b"], o["c"]) == (60, "g", 1)
    assert client().get("/web/foods.json").status_code == 404  # signed out: nothing
    assert "connect-src 'self'" in web[0].get("/web/").headers["content-security-policy"]


# ---- Recent ----------------------------------------------------------------------------------------

def test_recent_lists_the_last_messages_and_what_they_saved(web, fake):
    oid = oats()
    act(web, "/web/food/again", food=str(oid), amount="45", time="09:30", key="r1-r1")
    act(web, "/web/habit", name="meds", action="tick", key="r1-r2")
    page = web[0].get("/web/recent").text
    assert "<h1>Recent</h1>" in page and 'aria-current="page">Recent' in page
    assert "Test oats 45 g again at 09:30" in page and "Test oats 45 g" in page and "Medication" in page
    assert page.index("tick meds") < page.index("Test oats 45 g again")  # newest first
    assert 'data-fill="tick meds"' in page and 'href="/web/?day=' in page
