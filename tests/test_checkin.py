"""The evening check-in (02.11.2026, web item 37; the owner's answers in the doc, section "Mood and energy check-in"): mood and
energy from 1 to 5 on a "How was today?" card on Today from 17:00 until both are answered, stored like weight, on the Health
page (chart and the day's row), on the Week page and in the Sheet's Health tab."""
from datetime import datetime, timedelta

import pytest

from app import rules, todaypage
from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, n, web  # noqa: F401

MEAS = "SELECT t.name, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id WHERE t.name IN ('mood','energy') ORDER BY m.id"


@pytest.fixture
def evening(monkeypatch):
    monkeypatch.setattr(todaypage, "clock", lambda: datetime.now(rules.BERLIN).replace(hour=18))


def test_the_card_shows_in_the_evening_only(web, monkeypatch):
    monkeypatch.setattr(todaypage, "clock", lambda: datetime.now(rules.BERLIN).replace(hour=11))
    assert "How was today?" not in web[0].get("/web/").text
    monkeypatch.setattr(todaypage, "clock", lambda: datetime.now(rules.BERLIN).replace(hour=2))  # after midnight: still the evening's day
    assert "How was today?" in web[0].get("/web/").text


def test_two_taps_answer_it_and_it_goes(web, fake, evening):
    page = web[0].get("/web/").text
    assert "How was today?" in page and page.count('action="/web/checkin"') == 2
    assert 'aria-label="Mood 4, good"' in page and 'aria-label="Energy 5, full of energy"' in page
    one = act(web, "/web/checkin", type="mood", value="4", key="c1")
    assert "Saved." in one.text and fake.calls == [] and 'action="/web/undo"' in one.text
    assert "4 · good" in one.text and one.text.count('action="/web/checkin"') == 1  # mood done, energy still open
    two = act(web, "/web/checkin", type="energy", value="3", key="c2")
    assert "How was today?" not in two.text
    assert [(r["name"], float(r["value1"])) for r in owner_sql(MEAS)] == [("mood", 4.0), ("energy", 3.0)]
    again = act(web, "/web/checkin", type="energy", value="3", key="c2")  # a double tap saves once
    assert n("SELECT count(*) AS n FROM measurements") == 2 and "Already saved" in again.text


def test_a_tap_outside_one_to_five_is_refused(web, fake, evening):
    assert "Pick 1 to 5." in act(web, "/web/checkin", type="mood", value="7", key="c3").text
    assert "Pick 1 to 5." in act(web, "/web/checkin", type="mood", value="2.5", key="c4").text
    assert "Unknown check-in." in act(web, "/web/checkin", type="stress", value="3", key="c5").text
    assert n("SELECT count(*) AS n FROM measurements") == 0


def test_once_answered_it_stays_away_before_the_evening_too(web, fake, monkeypatch):
    monkeypatch.setattr(todaypage, "clock", lambda: datetime.now(rules.BERLIN).replace(hour=10))
    act(web, "/web/checkin", type="mood", value="2", key="c6")  # one of the two in (typed or tapped): the card stays for the other
    page = web[0].get("/web/").text
    assert "How was today?" in page and "2 · low" in page and page.count('action="/web/checkin"') == 1


def test_health_page_week_page_and_the_row_edit(web, fake):
    act(web, "/web/checkin", type="mood", value="4", key="c7")
    act(web, "/web/checkin", type="energy", value="2", key="c8")
    health = web[0].get("/web/health").text
    assert 'id="c-mood"' in health and "mood avg <b>4.0</b>" in health and "energy avg <b>2.0</b>" in health
    assert "<th>Mood <span class=small>1–5</span></th>" in health and 'name="mo"' in health and 'name="en"' in health
    today = owner_sql("SELECT log_day(now()) AS d")[0]["d"]
    done = act(web, "/web/healthday", day=today.isoformat(), r="30", key="c9", mo="5", en="2")  # a correction from the Health page's row
    assert "Mood 4 → 5" in done.text
    assert sorted(float(r["value1"]) for r in owner_sql(MEAS)) == [2.0, 5.0]
    yesterday = (today - timedelta(days=1)).isoformat()  # the Week page counts complete days only, so yesterday's row
    act(web, "/web/healthday", day=yesterday, r="30", key="c10", mo="3", en="4")
    week = web[0].get("/web/week").text
    assert "<td>Mood · energy</td><td>mood 3.0 · energy 4.0" in week and "average of 1 evening, 1 to 5" in week
