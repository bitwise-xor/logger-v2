"""The Habits page's habit list and its forms (30.10.2026): track and stop tracking, retire and bring back, rename, delete a habit that was
never ticked, add a habit; Wake-up and Bedtime are times and are not part of it. Event rows of log_types that a test adds are removed
after it and the others restored (that table is not emptied between tests)."""
import re

import pytest

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, client, n, web  # noqa: F401
from test_webhealth import event
from test_webtoday import day


@pytest.fixture(autouse=True)
def habit_types_as_they_were():
    keep = owner_sql("SELECT name, label, daily_target, sort_order, active, on_checklist FROM log_types WHERE kind = 'event'")
    owner_sql("DELETE FROM ui_settings")
    yield
    owner_sql("DELETE FROM ui_settings")
    names = [r["name"] for r in keep]
    owner_sql("DELETE FROM events WHERE type_id IN (SELECT id FROM log_types WHERE kind = 'event' AND name <> ALL(%s))", names)
    owner_sql("DELETE FROM log_types WHERE kind = 'event' AND name <> ALL(%s)", names)
    for r in keep:
        owner_sql("UPDATE log_types SET label = %s, daily_target = %s, sort_order = %s, active = %s, on_checklist = %s WHERE name = %s",
                  r["label"], r["daily_target"], r["sort_order"], r["active"], r["on_checklist"], r["name"])


def flags(name):
    return owner_sql("SELECT on_checklist, active, label, daily_target, category, aliases FROM log_types WHERE name = %s", name)[0]


def listed(page):
    return re.findall(r'id="hab-([a-z0-9_]+)"', page)


def row(page, name):
    m = re.search(rf'<div class="hm" id="hab-{name}">.*?</div></div>', page, re.S)
    return m[0] if m else ""


def add(web, label="Stretching", **more):
    return act(web, "/web/habits/new", label=label, group="practice", daily_target="1", **more)


# ---- the list --------------------------------------------------------------------------------------

def test_wake_up_and_bedtime_are_times_and_not_habits(web, fake):
    assert flags("wake")["on_checklist"] is False  # migration 017
    for show in ("tracked", "untracked", "retired", "all"):
        page = web[0].get(f"/web/habits?show={show}").text
        assert "Wake-up" not in page and "Bedtime" not in page and "hab-wake" not in page and "hab-bed" not in page
    for name in ("wake", "bed"):
        for action in ("track", "retire", "restore", "edit", "delete"):
            assert "Unknown habit." in act(web, f"/web/habits/{action}", name=name, on="1", label="x", confirmed="1").text
    assert 'data-now="h-wake"' in web[0].get("/web/").text  # the Health card's Woke up row has a Now button


def test_the_list_has_filters_groups_and_counts(web, fake):
    total = n("SELECT count(*) AS n FROM log_types WHERE kind = 'event' AND category <> 'sleep'")
    everything = web[0].get("/web/habits?show=all").text
    assert len(listed(everything)) == total and f"All · {total}" in everything and "<h3>Practice" in everything
    default = web[0].get("/web/habits").text
    want = {r["name"] for r in owner_sql("SELECT name FROM log_types WHERE on_checklist AND active AND kind = 'event'")}
    assert set(listed(default)) == want and 'class="chip on" href="/web/habits?show=tracked"' in default
    assert listed(web[0].get("/web/habits?show=nonsense").text) == listed(default)
    add(web)
    untracked = web[0].get("/web/habits?show=untracked").text
    assert not want & set(listed(untracked)) and "Not tracked" in untracked and "Never ticked" in untracked


def test_a_row_shows_how_often_the_habit_was_ticked(web, fake):
    d = day()
    event("meds", d, "08:00")
    r = row(web[0].get("/web/habits").text, "meds")
    assert "1 of the last 30 days" in r and f"last {d:%d.%m.}" in r and 'aria-checked="true"' in r
    assert "Delete" not in r  # it has history: it can only be retired


# ---- adding ----------------------------------------------------------------------------------------

def test_a_new_habit_is_tracked_when_asked(web, fake):
    done = add(web, track="1")
    assert "Added the habit Stretching. It is tracked" in done.text
    assert flags("stretching") == {"on_checklist": True, "active": True, "label": "Stretching", "daily_target": 1, "category": "practice", "aliases": ["stretching"]}
    assert "Stretching" in web[0].get("/web/").text and "hab-stretching" in web[0].get("/web/habits").text
    assert "Stretching" in web[0].get("/web/settings?tab=habits").text and "Stretching" in web[0].get("/web/settings?tab=today").text
    last = owner_sql("SELECT name FROM log_types WHERE on_checklist ORDER BY sort_order DESC, name LIMIT 1")[0]["name"]
    assert last == "stretching"  # last on Today


def test_a_new_habit_can_start_untracked(web, fake):
    assert "not tracked yet" in add(web, label="Reading hour").text  # no track field: the box was left empty
    assert flags("reading_hour")["on_checklist"] is False
    assert "hab-reading_hour" not in web[0].get("/web/habits").text and "hab-reading_hour" in web[0].get("/web/habits?show=untracked").text
    assert "Reading hour" not in web[0].get("/web/").text


def test_bad_new_habits_change_nothing(web, fake):
    before = n("SELECT count(*) AS n FROM log_types")
    for form, why in (({"label": "  ", "group": "daily"}, "Give the habit a name"), ({"label": "???", "group": "daily"}, "Give the habit a name"),
                      ({"label": "Walks", "group": "sleep"}, "Choose a group"), ({"label": "Walks", "group": "nope"}, "Choose a group"),
                      ({"label": "medication", "group": "daily"}, "already a log type called medication")):
        assert why in act(web, "/web/habits/new", daily_target="1", **form).text
    assert n("SELECT count(*) AS n FROM log_types") == before


# ---- tracking, retiring, renaming, deleting ---------------------------------------------------------

def test_the_track_switch_moves_a_habit_on_and_off_today(web, fake):
    add(web)
    on = act(web, "/web/habits/track", name="stretching", on="1", back="/web/habits")
    assert "Now tracking Stretching." in on.text and flags("stretching")["on_checklist"] is True
    assert "Stretching" in web[0].get("/web/").text and 'aria-label="Stretching ' in web[0].get("/web/habits").text  # a calendar row
    off = act(web, "/web/habits/track", name="stretching", on="0")
    assert "Stopped tracking Stretching." in off.text and flags("stretching")["on_checklist"] is False
    assert "Stretching" not in web[0].get("/web/").text and 'aria-label="Stretching ' not in web[0].get("/web/habits").text


def test_a_retired_habit_is_hidden_everywhere_and_comes_back_as_it_was(web, fake):
    add(web, track="1")
    gone = act(web, "/web/habits/retire", name="stretching")
    assert "Retired Stretching." in gone.text and flags("stretching")["active"] is False
    assert "Stretching" not in web[0].get("/web/").text and "hab-stretching" not in web[0].get("/web/habits?show=untracked").text
    assert "hab-stretching" in web[0].get("/web/habits?show=retired").text and "Bring back" in web[0].get("/web/habits?show=retired").text
    assert "is retired: bring it back first" in act(web, "/web/habits/track", name="stretching", on="0").text
    back = act(web, "/web/habits/restore", name="stretching")
    assert "Brought back Stretching (tracked again)." in back.text and "Stretching" in web[0].get("/web/").text
    act(web, "/web/habits/track", name="stretching", on="0")
    act(web, "/web/habits/retire", name="stretching")
    assert "not tracked: switch Track on" in act(web, "/web/habits/restore", name="stretching").text


def test_a_habit_can_be_renamed_and_its_daily_count_changed(web, fake):
    add(web, track="1")
    saved = act(web, "/web/habits/edit", name="stretching", label=" Morning  stretch ", daily_target="3")
    assert "Saved the habit Morning stretch." in saved.text
    assert flags("stretching")["label"] == "Morning stretch" and flags("stretching")["daily_target"] == 3
    for form, why in (({"label": ""}, "needs a name"), ({"label": "medication"}, "already a log type called medication")):
        assert why in act(web, "/web/habits/edit", name="stretching", daily_target="1", **form).text
    assert flags("stretching")["label"] == "Morning stretch"
    assert flags("stretching")["aliases"] == ["stretching"]  # the word that logs it stays


def test_only_a_never_used_habit_can_be_deleted_and_only_after_the_yes(web, fake):
    add(web)
    assert "Delete Stretching?" in row(web[0].get("/web/habits?show=untracked").text, "stretching")
    assert "Press Yes" in act(web, "/web/habits/delete", name="stretching").text and flags("stretching")
    gone = act(web, "/web/habits/delete", name="stretching", confirmed="1")
    assert "Deleted the habit Stretching." in gone.text
    assert not owner_sql("SELECT 1 FROM log_types WHERE name = 'stretching'")
    event("meds", day(), "08:00")
    assert "has history" in act(web, "/web/habits/delete", name="meds", confirmed="1").text
    assert owner_sql("SELECT 1 FROM log_types WHERE name = 'meds'")


def test_the_forms_need_a_session_and_unknown_habits_are_refused(web, fake):
    assert client().post("/web/habits/track", data={"name": "meds", "on": "0"}).status_code in (303, 302, 403)
    assert flags("meds")["on_checklist"] is True
    for action in ("track", "retire", "restore", "edit", "delete"):
        assert "Unknown habit." in act(web, f"/web/habits/{action}", name="nope", on="1", label="x", confirmed="1").text
