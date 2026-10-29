"""The Habits page, the Settings page (food targets, habits, the Today page) and what the Today page does with the choices
(29.10.2026). Habit rows of log_types are restored after each test: that table is not emptied between tests."""
import re
from datetime import timedelta

import pytest

from app import uiprefs
from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, client, n, web  # noqa: F401
from test_webhealth import event
from test_webtoday import day, eat, food, no_targets  # noqa: F401 - no_targets: clean targets and usual meals


@pytest.fixture(autouse=True)
def habits_as_they_were():
    keep = owner_sql("SELECT name, label, daily_target, sort_order, active FROM log_types WHERE on_checklist")
    owner_sql("DELETE FROM ui_settings")
    yield
    owner_sql("DELETE FROM ui_settings")
    owner_sql("DELETE FROM log_types WHERE name = 'yoga_class'")
    for r in keep:
        owner_sql("UPDATE log_types SET label = %s, daily_target = %s, sort_order = %s, active = %s WHERE name = %s",
                  r["label"], r["daily_target"], r["sort_order"], r["active"], r["name"])


def ticks(name, d):
    return n("SELECT count(*) AS n FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = %s AND e.day = %s", name, d)


# ---- the Habits page -------------------------------------------------------------------------------

def test_the_page_needs_sign_in_and_the_app_host():
    assert client().get("/web/habits").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/habits").status_code == 404


def test_the_habits_page_shows_today_as_buttons_and_30_days_as_squares(web, fake):
    d = day()
    event("meds", d, "08:00")
    owner_sql("UPDATE log_types SET daily_target = 2 WHERE name = 'shoonya'")
    event("shoonya", d, "07:30")
    page = web[0].get("/web/habits").text
    assert "<h1>Habits</h1>" in page and "Today · 1 of 3 done" in page or "Today · 1 of 3" in page
    assert re.search(r'class="habit big done"[^>]*>.*?Medication', page, re.S) and 'aria-current="page">Habits' in page
    assert f'class="sq d" type="submit" name="day" value="{d.isoformat()}" title="{d:%a %d.%m.}: done"' in page  # meds today
    assert f'class="sq h" type="submit" name="day" value="{d.isoformat()}" title="{d:%a %d.%m.}: 1 of 2"' in page  # shoonya, partly
    assert page.count('name="day" value="') == 2 * 30  # meds and shoonya; Wake-up is read-only
    assert re.search(r'<span class="sq[ "]', page) and "read-only" in page
    assert 'name="back" value="/web/habits"' in page or 'name="back" value="/web/" data-here' in page


def test_ticking_on_the_habits_page_stays_on_it(web, fake):
    page = act(web, "/web/habit", name="meds", action="tick", key="hb1", back="/web/habits")
    assert "<h1>Habits</h1>" in page.text and "Saved." in page.text
    assert ticks("meds", day()) == 1
    eid = re.search(r'name="eid" value="(\d+)"', page.text)[1]
    gone = act(web, "/web/habit", name="meds", action="untick", eid=eid, back="/web/habits")
    assert "<h1>Habits</h1>" in gone.text and "Removed the Medication tick." in gone.text and ticks("meds", day()) == 0


def test_a_click_on_a_past_day_ticks_it_and_a_second_click_takes_it_off(web, fake):
    d = day() - timedelta(days=3)
    page = act(web, "/web/habit/day", name="meds", day=d.isoformat(), key="pd1", back="/web/habits")
    assert "Saved." in page.text and ticks("meds", d) == 1 and ticks("meds", day()) == 0
    again = act(web, "/web/habit/day", name="meds", day=d.isoformat(), key="pd2", back="/web/habits")
    assert f"Removed 1 tick of Medication on {d:%d.%m.%Y}." in again.text and ticks("meds", d) == 0


def test_a_habit_with_a_daily_count_fills_up_and_then_empties(web, fake):
    owner_sql("UPDATE log_types SET daily_target = 2 WHERE name = 'shoonya'")
    d = day() - timedelta(days=1)
    for i in (1, 2):
        act(web, "/web/habit/day", name="shoonya", day=d.isoformat(), key=f"sh{i}", back="/web/habits")
        assert ticks("shoonya", d) == i
    emptied = act(web, "/web/habit/day", name="shoonya", day=d.isoformat(), key="sh3", back="/web/habits")
    assert f"Removed 2 ticks of Shoonya on {d:%d.%m.%Y}." in emptied.text and ticks("shoonya", d) == 0


def test_past_day_clicks_are_limited(web, fake):
    d = day()
    for name, when, why in (("wake", d - timedelta(days=1), "cannot be changed for a past day"), ("nope", d, "cannot be changed for a past day"),
                            ("meds", d + timedelta(days=1), "Only the last 30 days"), ("meds", d - timedelta(days=60), "Only the last 30 days")):
        assert why in act(web, "/web/habit/day", name=name, day=when.isoformat(), key="x").text
    assert "Unknown day." in act(web, "/web/habit/day", name="meds", day="soon", key="x").text
    assert n("SELECT count(*) AS n FROM events") == 0


def test_the_health_page_no_longer_has_the_calendar_but_links_to_habits(web, fake):
    page = web[0].get("/web/health").text
    assert "Habits · last" not in page and 'href="/web/habits"' in page


# ---- Settings: food targets ------------------------------------------------------------------------

def test_targets_are_saved_from_a_date_and_old_ones_stay(web):
    d = day()
    page = web[0].get("/web/settings?tab=targets").text
    assert 'name="valid_from"' in page and "none set yet" in page and "No targets yet." in page
    form = dict(valid_from=d.isoformat(), kcal="2000", protein="140,5", fat="70", sat_fat="", carbs="200", fibre="30", sugar_added="", sodium_mg="", note="test")
    done = act(web, "/web/settings/targets", **form)
    assert f"Targets from {d:%d.%m.%Y} saved: Calories 2000, Protein 140.5, Fat 70, Carbs 200." in done.text
    row = owner_sql("SELECT kcal, protein, sat_fat, fibre, note FROM food_targets")
    assert len(row) == 1 and (float(row[0]["kcal"]), float(row[0]["protein"]), row[0]["sat_fat"], float(row[0]["fibre"]), row[0]["note"]) == (2000, 140.5, None, 30, "test")
    act(web, "/web/settings/targets", **{**form, "kcal": "2100"})  # the same date replaces its row
    assert [float(r["kcal"]) for r in owner_sql("SELECT kcal FROM food_targets")] == [2100]
    earlier = d - timedelta(days=10)
    act(web, "/web/settings/targets", **{**form, "valid_from": earlier.isoformat(), "kcal": "1800"})
    assert n("SELECT count(*) AS n FROM food_targets") == 2
    page = web[0].get("/web/settings?tab=targets").text
    assert f"valid from {d:%d.%m.%Y}" in page and f"{earlier:%d.%m.%Y}" in page  # the newest valid one is shown, the older one is history
    assert 'value="2100"' in page
    eat("Test oats", 400, 20)
    assert "/ 2,100 kcal" in web[0].get("/web/").text  # Today reads it at once


def test_bad_targets_change_nothing(web):
    d = day().isoformat()
    ok = dict(valid_from=d, kcal="2000", protein="", fat="", carbs="", sat_fat="", fibre="", sugar_added="", sodium_mg="")
    for bad, why in (({"valid_from": "soon"}, "Give the date"), ({"kcal": "lots"}, "Calories: give a number"), ({"protein": "-5"}, "Protein: give a number"),
                     ({"kcal": ""}, "Give at least one target"), ({"fat": "1e9"}, "Fat: give a number")):
        assert why in act(web, "/web/settings/targets", **{**ok, **bad}).text
    assert n("SELECT count(*) AS n FROM food_targets") == 0


# ---- Settings: habits ------------------------------------------------------------------------------

def test_a_habit_can_be_renamed_reordered_and_retired(web):
    page = web[0].get("/web/settings?tab=habits").text
    assert 'value="Medication"' in page and "Add a habit" in page
    saved = act(web, "/web/settings/habit", name="meds", label=" Meds  and vitamins ", daily_target="2", sort_order="5", active="1")
    assert "Saved the habit Meds and vitamins." in saved.text
    assert owner_sql("SELECT label, daily_target, sort_order FROM log_types WHERE name = 'meds'")[0] == {"label": "Meds and vitamins", "daily_target": 2, "sort_order": 5}
    retired = act(web, "/web/settings/habit", name="meds", label="Meds and vitamins", daily_target="2", sort_order="5")  # Active unticked
    assert "It is retired" in retired.text
    assert "Meds and vitamins" not in web[0].get("/web/").text and "Meds and vitamins" not in web[0].get("/web/habits").text
    assert "Unknown habit." in act(web, "/web/settings/habit", name="weight", label="x", daily_target="1", sort_order="1", active="1").text
    assert "needs a name" in act(web, "/web/settings/habit", name="meds", label=" ", daily_target="1", sort_order="1", active="1").text


def test_a_new_habit_is_added_and_shows_everywhere(web):
    done = act(web, "/web/settings/habit_new", label="Yoga class", daily_target="1")
    assert "Added the habit Yoga class." in done.text
    row = owner_sql("SELECT name, kind, on_checklist, active, daily_target FROM log_types WHERE name = 'yoga_class'")
    assert row == [{"name": "yoga_class", "kind": "event", "on_checklist": True, "active": True, "daily_target": 1}]
    assert "Yoga class" in web[0].get("/web/").text and "Yoga class" in web[0].get("/web/habits").text
    assert "already a log type" in act(web, "/web/settings/habit_new", label="yoga CLASS", daily_target="1").text
    assert "already a log type" in act(web, "/web/settings/habit_new", label="Medication", daily_target="1").text
    assert "letters or digits" in act(web, "/web/settings/habit_new", label="***", daily_target="1").text


# ---- Settings: the Today page ----------------------------------------------------------------------

def usual_meal(name="test bowl"):
    fid = food("Test oats", 400, 13, 7, 60)
    mid = owner_sql("INSERT INTO usual_meals (name) VALUES (%s) RETURNING id", name)[0]["id"]
    owner_sql("INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order) VALUES (%s, %s, 60, 1)", mid, fid)
    return mid


def test_the_today_page_shows_everything_until_something_is_chosen(web):
    page = web[0].get("/web/").text
    for want in ("Medication", "Shoonya", "Wake-up", "Weight", "Blood sugar", "To review", "Customize this page", "All habits"):
        assert want in page
    assert uiprefs.load.__doc__ and n("SELECT count(*) AS n FROM ui_settings") == 0


def test_the_today_tab_lists_every_part_with_its_order(web):
    usual_meal()
    page = web[0].get("/web/settings?tab=today").text
    for want in ("Habits", "Health card rows", "Usual meal buttons", "Medication", "Blood sugar fasted", "test bowl", "To review",
                 'name="show_habit_meds" value="1" checked', 'name="pos_habit_meds"', 'name="show_review_box"', "Show everything in the default order"):
        assert want in page, want
    assert 'name="pos_review_box"' not in page  # one item has no order


def test_hidden_parts_and_their_order_apply_on_today(web):
    mid = usual_meal()
    eat("Old thing", 100, 5, review=True)
    keep = {"show_habit_shoonya": "1", "pos_habit_shoonya": "1", "pos_habit_meds": "2", "pos_habit_wake": "3", "show_habit_wake": "1",
            "show_health_weight": "1", "show_health_bp_morning": "1", "show_health_bp_evening": "1", "show_health_wake": "1"}  # not: meds, sugar, usual, review
    assert "Saved." in act(web, "/web/settings/today", **keep).text
    stored = owner_sql("SELECT value FROM ui_settings WHERE key = 'today'")[0]["value"]
    assert set(stored["hidden"]) == {"habit:meds", "health:sugar", f"usual:{mid}", "review:box"}
    assert stored["order"]["habit"] == ["shoonya", "wake", "meds"] or stored["order"]["habit"][0] == "shoonya"
    page = web[0].get("/web/").text
    assert "Medication" not in page and "Blood sugar" not in page and "+ test bowl" not in page and "To review</h2>" not in page
    assert "Shoonya" in page and page.index("Shoonya") < page.index("Wake-up") and "Weight" in page
    assert "1 item to review is hidden here" in page and "Looks right" not in page  # a one-line notice, not the box
    assert "Medication" in web[0].get("/web/habits").text  # only the Today page is changed
    assert "Medication" in web[0].get("/web/settings?tab=habits").text


def test_hiding_everything_and_the_reset(web):
    act(web, "/web/settings/today")  # nothing ticked: every part is hidden
    page = web[0].get("/web/").text
    assert 'class="habits"' not in page and "Health</h2>" not in page and "Nothing logged yet today." in page  # Food stays
    done = act(web, "/web/settings/today_reset")
    assert "shows everything again" in done.text and n("SELECT count(*) AS n FROM ui_settings") == 0
    assert "Medication" in web[0].get("/web/").text


def test_the_order_helper_keeps_unknown_items_last_and_in_their_own_order():
    items = ["a", "b", "c", "d"]
    assert uiprefs.ordered(items, lambda x: x, ["c", "a"]) == ["c", "a", "b", "d"]
    assert uiprefs.ordered(items, lambda x: x, []) == items
    assert uiprefs.shown("habit", "x", {"hidden": {"habit:y"}}) and not uiprefs.shown("habit", "y", {"hidden": {"habit:y"}})
