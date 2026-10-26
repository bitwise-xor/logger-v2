"""The web app's buttons and fields: habit tick and untick, usual meals with edited weights, the Health card,
review items. New entries make no AI call; corrections go through the logger's own change function."""
import pytest

from app import service
from conftest import owner_sql
from test_log import fake, off, skyr  # noqa: F401 - fixtures reused
from test_web import act, n, web  # noqa: F401
from test_webtoday import day, eat, food, raw


@pytest.fixture(autouse=True)
def clean_usual():
    owner_sql("TRUNCATE usual_meals CASCADE")
    yield
    owner_sql("TRUNCATE usual_meals CASCADE")


def meds():
    return n("SELECT count(*) AS n FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = 'meds'")


# ---- habits

def test_tick_then_untick(web, fake):
    page = act(web, "/web/habit", name="meds", action="tick", key="h1")
    assert "Saved." in page.text and meds() == 1 and fake.calls == []
    assert owner_sql("SELECT content, channel FROM raw_inbox") == [{"content": "tick meds", "channel": "web"}]
    eid = owner_sql("SELECT id FROM events")[0]["id"]
    page = act(web, "/web/habit", name="meds", action="untick", eid=str(eid))
    assert "Removed the Medication tick." in page.text and meds() == 0
    assert 'value="tick"' in page.text
    again = act(web, "/web/habit", name="meds", action="untick", eid=str(eid))  # a second click removes nothing more
    assert "no tick from today" in again.text
    assert owner_sql("SELECT tool, outcome, client FROM audit_log WHERE tool = 'web_change'") == [
        {"tool": "web_change", "outcome": "changed", "client": "web"}]


def test_a_double_click_ticks_once(web, fake):
    act(web, "/web/habit", name="shoonya", action="tick", key="same")
    act(web, "/web/habit", name="shoonya", action="tick", key="same")
    assert n("SELECT count(*) AS n FROM events") == 1


def test_untick_only_takes_a_tick_of_that_habit_from_today(web, fake):
    act(web, "/web/habit", name="meds", action="tick", key="h2")
    eid = owner_sql("SELECT id FROM events")[0]["id"]
    assert "no tick from today" in act(web, "/web/habit", name="shoonya", action="untick", eid=str(eid)).text
    owner_sql("UPDATE events SET day = day - 1")
    assert "no tick from today" in act(web, "/web/habit", name="meds", action="untick", eid=str(eid)).text
    assert meds() == 1


def test_unknown_habits_are_refused(web, fake):
    for name in ("weight", "nonsense", "night_hot_drink"):
        assert "not on the checklist" in act(web, "/web/habit", name=name, action="tick", key=name).text
    assert n("SELECT count(*) AS n FROM raw_inbox") == 0


# ---- usual meals

@pytest.fixture
def breakfast(skyr):
    oats = food("Test oats", 380, 13, 7, 60)
    honey = food("Test honey", 304, 0.3, 0, 82)
    mid = owner_sql("INSERT INTO usual_meals (name) VALUES ('usual breakfast') RETURNING id")[0]["id"]
    items = owner_sql("""INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order)
                         VALUES (%s, %s, 60, 1), (%s, %s, 200, 2), (%s, %s, 10, 3) RETURNING id""", mid, oats, mid, skyr, mid, honey)
    return mid, [i["id"] for i in items], (oats, skyr, honey)


def test_a_usual_meal_as_it_is(web, fake, breakfast):
    mid, (a, b, c), _ = breakfast
    page = act(web, "/web/usual", meal=str(mid), key="u1", time="", **{f"on_{a}": "on", f"g_{a}": "60", f"on_{b}": "on", f"g_{b}": "200",
                                                                         f"on_{c}": "on", f"g_{c}": "10"})
    assert "Saved." in page.text and fake.calls == []
    rows = owner_sql("SELECT amount, usual_meal_id FROM food_entries ORDER BY id")
    assert [float(r["amount"]) for r in rows] == [60, 200, 10] and {r["usual_meal_id"] for r in rows} == {mid}


def test_a_usual_meal_with_a_changed_weight_and_an_item_left_out(web, fake, breakfast):
    mid, (a, b, c), (oats, skyr, honey) = breakfast
    act(web, "/web/usual", meal=str(mid), key="u2", time="07:30", **{f"on_{a}": "on", f"g_{a}": "60", f"on_{b}": "on", f"g_{b}": "150",
                                                                       f"g_{c}": "10"})
    rows = {r["food_id"]: float(r["amount"]) for r in owner_sql("SELECT food_id, amount FROM food_entries")}
    assert rows == {oats: 60, skyr: 150}
    assert owner_sql("SELECT content FROM raw_inbox")[0]["content"] == "my usual usual breakfast (Skyr natural 150 g, no Test honey) at 07:30"
    assert owner_sql("SELECT to_char(eaten_at AT TIME ZONE 'Europe/Berlin', 'HH24:MI') AS t FROM food_entries LIMIT 1")[0]["t"] == "07:30"


def test_a_usual_meal_refuses_bad_input(web, fake, breakfast):
    mid, (a, b, c), _ = breakfast
    assert "at least one item" in act(web, "/web/usual", meal=str(mid), key="u3").text
    assert "amount above 0" in act(web, "/web/usual", meal=str(mid), key="u4", **{f"on_{a}": "on", f"g_{a}": "0"}).text
    assert "Unknown usual meal" in act(web, "/web/usual", meal="999", key="u5").text
    assert n("SELECT count(*) AS n FROM food_entries") == 0


# ---- health

def test_a_new_weight_from_the_health_card(web, fake):
    page = act(web, "/web/health", row="weight", id="", v1="82,4", key="k1")
    assert "Saved." in page.text and fake.calls == []
    assert [float(r["value1"]) for r in owner_sql("SELECT value1 FROM measurements")] == [82.4]
    assert 'value="82.4"' in page.text


def test_a_new_evening_blood_pressure_and_a_wake_up_time(web, fake):
    act(web, "/web/health", row="bp_evening", id="", v1="131", v2="84", key="k2")
    act(web, "/web/health", row="wake", id="", t="06:40", key="k3")
    bp = owner_sql("SELECT value1, value2, period FROM measurements")[0]
    assert (float(bp["value1"]), float(bp["value2"]), bp["period"]) == (131, 84, "evening")
    assert owner_sql("""SELECT to_char(event_at AT TIME ZONE 'Europe/Berlin', 'HH24:MI') AS t FROM events e
                        JOIN log_types t ON t.id = e.type_id WHERE t.name = 'wake'""")[0]["t"] == "06:40"


def test_correcting_todays_weight_is_saved_at_once(web, fake):
    service.record_weight(82.4)
    mid = owner_sql("SELECT id FROM measurements")[0]["id"]
    page = act(web, "/web/health", row="weight", id=f"m{mid}", v1="82.1", key="k4")
    assert "Weight changed to 82.1." in page.text
    assert [float(r["value1"]) for r in owner_sql("SELECT value1 FROM measurements")] == [82.1]
    assert n("SELECT count(*) AS n FROM measurements") == 1  # corrected, not a second weighing


def test_an_odd_correction_asks_first_then_saves_on_yes(web, fake):
    service.record_weight(82.4)
    mid = owner_sql("SELECT id FROM measurements")[0]["id"]
    page = act(web, "/web/health", row="weight", id=f"m{mid}", v1="400", key="k5")
    assert "please confirm" in page.text and "Yes, save it" in page.text
    assert [float(r["value1"]) for r in owner_sql("SELECT value1 FROM measurements")] == [82.4]
    page = act(web, "/web/health", row="weight", id=f"m{mid}", v1="400", confirmed="1")
    assert [float(r["value1"]) for r in owner_sql("SELECT value1 FROM measurements")] == [400]


def test_health_refuses_bad_input(web, fake):
    service.record_bp(124, 81, "morning")
    mid = owner_sql("SELECT id FROM measurements")[0]["id"]
    assert "give both numbers" in act(web, "/web/health", row="bp_morning", id=f"m{mid}", v1="130", v2="").text
    assert "a number" in act(web, "/web/health", row="weight", id="", v1="heavy").text
    assert "Unknown row" in act(web, "/web/health", row="cholesterol", id="", v1="1").text
    assert "not there any more" in act(web, "/web/health", row="weight", id=f"m{mid}", v1="80").text  # m is a bp, not a weight
    assert "Unknown entry" in act(web, "/web/health", row="weight", id="x1; drop", v1="80").text
    assert [float(r["value1"]) for r in owner_sql("SELECT value1 FROM measurements")] == [124]


# ---- review

def flagged_thought():
    owner_sql("""INSERT INTO thoughts (raw_id, original_text, summary, needs_review, review_reason)
                 VALUES (%s, 'call the landlord', 'call the landlord', true, 'nothing extracted')""", raw())
    return owner_sql("SELECT id FROM thoughts")[0]["id"]


def test_review_looks_right_clears_the_flag(web):
    tid = flagged_thought()
    page = act(web, "/web/review", ref=f"t{tid}", act="ok")
    assert "Kept." in page.text and "Nothing to review." in page.text
    assert owner_sql("SELECT needs_review, review_reason FROM thoughts") == [{"needs_review": False, "review_reason": None}]


def test_review_change_sorts_a_thought(web):
    tid = flagged_thought()
    act(web, "/web/review", ref=f"t{tid}", act="change", thought_type="task", area="personal")
    t = owner_sql("SELECT thought_type, area, needs_review FROM thoughts")[0]
    assert t == {"thought_type": "task", "area": "personal", "needs_review": False}
    assert "Pick a type" in act(web, "/web/review", ref=f"t{tid}", act="change", thought_type="x", area="personal").text


def test_review_change_and_delete_a_food_entry(web):
    eat("Test bar", 900, 2, review=True, amount=120)
    fid = owner_sql("SELECT id FROM food_entries")[0]["id"]
    act(web, "/web/review", ref=f"f{fid}", act="change", amount="60")
    assert owner_sql("SELECT amount, needs_review FROM food_entries") == [{"amount": 60, "needs_review": False}]
    page = act(web, "/web/review", ref=f"f{fid}", act="delete")
    assert "Deleted." in page.text and n("SELECT count(*) AS n FROM food_entries") == 0
    assert "does not exist" in act(web, "/web/review", ref=f"f{fid}", act="delete").text


def test_review_refuses_unknown_items(web):
    assert "Unknown item" in act(web, "/web/review", ref="x1", act="ok").text
    assert "Unknown item" in act(web, "/web/review", ref="t1", act="drop").text
