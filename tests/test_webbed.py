"""The Health card on the Today page (30.10.2026): the rows are Bed (the night before), Woke up, Weight, Blood sugar, Blood pressure morning
and evening, in that order; the Bed row saves like the Woke up row (a time, the night before today)."""
import re
from datetime import timedelta

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, n, web  # noqa: F401


def order(page):
    return re.findall(r'<form class="hrow"[^>]*>.*?name="row" value="(\w+)"', page, re.S)


def beds():
    return [r["t"] for r in owner_sql("""SELECT to_char(e.event_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI') AS t FROM events e
                                         JOIN log_types t ON t.id = e.type_id WHERE t.name = 'bed' ORDER BY e.id""")]


def today():
    return owner_sql("SELECT log_day(now()) AS d")[0]["d"]


def test_the_health_card_rows_are_in_the_chosen_order(web, fake):
    page = web[0].get("/web/").text
    assert order(page) == ["bed", "wake", "weight", "sugar", "bp_morning", "bp_evening"]
    assert 'id="h-bed"' in page and "night before" in page and 'id="h-wake"' in page
    assert page.count('data-now="h-wake"') == 1 and 'data-now="h-bed"' not in page  # only Woke up has a Now button


def test_the_settings_page_lists_the_same_rows_in_the_same_order(web, fake):
    page = web[0].get("/web/settings?tab=today").text
    names = [page.index(f'name="show_health_{k}"') for k in ("bed", "wake", "weight", "sugar", "bp_morning", "bp_evening")]
    assert names == sorted(names)


def test_a_bed_time_from_noon_on_is_the_evening_before(web, fake):
    page = act(web, "/web/health", row="bed", id="", t="23:10", key="b1")
    assert "Saved." in page.text and fake.calls == []
    assert beds() == [f"{today() - timedelta(days=1)} 23:10"]
    assert 'id="h-bed" value="23:10"' in page.text  # the row shows it


def test_a_bed_time_after_midnight_is_on_today_and_a_typed_hour_is_padded(web, fake):
    act(web, "/web/health", row="bed", id="", t="0:20", key="b2")
    assert beds() == [f"{today()} 00:20"]


def test_the_bed_time_can_be_corrected(web, fake):
    act(web, "/web/health", row="bed", id="", t="23:10", key="b3")
    eid = owner_sql("""SELECT e.id FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = 'bed'""")[0]["id"]
    page = act(web, "/web/health", row="bed", id=f"e{eid}", t="23:25", key="b4")
    assert "Bed night before changed to 23:25." in page.text
    assert beds() == [f"{today() - timedelta(days=1)} 23:25"]  # corrected, not a second entry


def test_a_bad_bed_time_is_refused(web, fake):
    for bad in ("", "25:99", "late"):
        assert "Give the time as hh:mm." in act(web, "/web/health", row="bed", id="", t=bad).text
    assert beds() == []
