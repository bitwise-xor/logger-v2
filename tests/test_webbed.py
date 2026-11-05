"""The Health card on the Today page (30.10.2026): the rows are Bed (the night before), Woke up, Weight, Blood sugar, Blood pressure morning
and evening, in that order; the Bed row saves like the Woke up row (a time, the night before today)."""
import re
from datetime import datetime, timedelta

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, n, web  # noqa: F401
from test_webhealth import event
from app.when import BERLIN


def order(page):
    return re.findall(r'<form class="hrow"[^>]*>.*?name="row" value="(\w+)"', page, re.S)


def beds():
    return [r["t"] for r in owner_sql("""SELECT to_char(e.event_at AT TIME ZONE 'Europe/Berlin', 'YYYY-MM-DD HH24:MI') AS t FROM events e
                                         JOIN log_types t ON t.id = e.type_id WHERE t.name = 'bed' ORDER BY e.id""")]


def today():
    return owner_sql("SELECT log_day(now()) AS d")[0]["d"]


def recent_wake() -> datetime:
    """A wake-up 2 hours ago: within 12 hours, so Bed is still the night before it (at 07:00 of today it was over 12 hours ago
    at night, when Bed is already for the next sleep, test_nextsleep.py)."""
    up = (datetime.now(BERLIN) - timedelta(hours=2)).replace(second=0, microsecond=0)
    event("wake", up.date(), f"{up:%H:%M}")
    return up


def before(up: datetime, hm: str) -> str:
    """The last hm before the wake-up up, as "YYYY-MM-DD HH:MM"."""
    at = up.replace(hour=int(hm[:2]), minute=int(hm[3:]))
    return f"{at - timedelta(days=1) if at >= up else at:%Y-%m-%d %H:%M}"


def test_the_health_card_rows_are_in_the_chosen_order(web, fake):
    page = web[0].get("/web/").text
    assert order(page) == ["bed", "wake", "weight", "sugar", "bp_morning", "bp_evening"]
    assert 'id="h-bed"' in page and "night before" in page and 'id="h-wake"' in page
    assert page.count('data-now="h-wake"') == 1 and 'data-now="h-bed"' not in page  # only Woke up has a Now button


def test_the_settings_page_lists_the_same_rows_in_the_same_order(web, fake):
    page = web[0].get("/web/settings?tab=today").text
    names = [page.index(f'name="show_health_{k}"') for k in ("bed", "wake", "weight", "sugar", "bp_morning", "bp_evening")]
    assert names == sorted(names)


def test_a_bed_time_is_the_last_one_before_the_wake_up_evening_before(web, fake):
    up = recent_wake()
    page = act(web, "/web/health", row="bed", id="", t="23:10", key="b1")
    assert "Saved." in page.text and fake.calls == []
    assert beds() == [before(up, "23:10")]
    assert 'id="h-bed" value="23:10"' in page.text  # the row shows it


def test_a_bed_time_after_midnight_and_a_typed_hour_is_padded(web, fake):
    up = recent_wake()
    act(web, "/web/health", row="bed", id="", t="0:20", key="b2")
    assert beds() == [before(up, "00:20")]


def test_a_daytime_sleep_is_on_the_day_of_its_wake_up():
    """A daytime sleep (to bed after noon, up in the evening): the bed is that day's, not the day before's, and the day
    before's bed is not offered for it (bug until 04.11.2026). The rule with fixed times (pages: test_webhealth)."""
    from datetime import date
    from app import healthpage
    at = lambda d, h, m=0: datetime(2026, 11, d, h, m, tzinfo=BERLIN)  # noqa: E731
    up = at(3, 21, 15)
    assert healthpage.bed_when("12:30", up) == "03.11.2026 12:30"  # the noon rule made it 02.11.
    assert healthpage.bed_when("23:10", at(3, 7)) == "02.11.2026 23:10" and healthpage.bed_when("00:20", at(3, 7)) == "03.11.2026 00:20"
    beds, wakes = [(1, at(2, 12, 30)), (2, at(3, 12, 30))], [at(2, 20, 30), up]
    assert healthpage.pick_bed(beds, wakes, up) == (2, at(3, 12, 30))
    assert healthpage.pick_bed(beds[:1], wakes, up) is None  # the day before's bed paired with the day before's wake-up
    assert healthpage.bed_anchor(date(2026, 11, 3), date(2026, 11, 4)) == at(3, 22)  # an earlier day without a wake-up


def test_without_a_wake_up_a_bed_time_is_the_last_one_before_now(web, fake):
    two_ago = datetime.now(BERLIN) - timedelta(hours=2)
    act(web, "/web/health", row="bed", id="", t=f"{two_ago:%H:%M}", key="b6")
    assert beds() == [f"{two_ago:%Y-%m-%d %H:%M}"]


def test_the_bed_time_can_be_corrected(web, fake):
    up = recent_wake()
    act(web, "/web/health", row="bed", id="", t="23:10", key="b3")
    eid = owner_sql("""SELECT e.id FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = 'bed'""")[0]["id"]
    page = act(web, "/web/health", row="bed", id=f"e{eid}", t="23:25", key="b4")
    assert "Bed night before changed to 23:25." in page.text
    assert beds() == [before(up, "23:25")]  # corrected, not a second entry


def test_a_bad_bed_time_is_refused(web, fake):
    for bad in ("", "25:99", "late"):
        assert "Give the time as hh:mm." in act(web, "/web/health", row="bed", id="", t=bad).text
    assert beds() == []
