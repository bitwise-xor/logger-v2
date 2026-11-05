"""The Health card's Bed and Woke up for the next sleep (06.11.2026): more than 12 hours after the day's wake-up both fields start
empty, so typed times log a new sleep instead of moving the last night's entries (the owner typed a new bed and wake-up into the filled
fields, which moved the last night's ones). A bed time logged after that wake-up is shown and can be corrected."""
import re
from datetime import datetime, timedelta

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, web  # noqa: F401
from test_webtoday import raw
from app.when import BERLIN

FMT = "%Y-%m-%d %H:%M"


def ev(name: str, ts: datetime) -> int:
    """An event as the logger stores it: a wake-up's day by wake_day(), anything else by log_day()."""
    return owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                        SELECT %s, id, CASE WHEN name = 'wake' THEN wake_day(%s) ELSE log_day(%s) END, %s, true, 'test'
                        FROM log_types WHERE name = %s RETURNING id""", raw(), ts, ts, ts, name)[0]["id"]


def times(name: str) -> list:
    return [f"{r['t']:{FMT}}" for r in owner_sql("""SELECT e.event_at AT TIME ZONE 'Europe/Berlin' AS t FROM events e
                                                   JOIN log_types t ON t.id = e.type_id WHERE t.name = %s ORDER BY e.event_at""", name)]


def row(page: str, k: str) -> str:
    return re.search(rf'<form class="hrow"[^>]*>(?:(?!</form>).)*?name="row" value="{k}".*?</form>', page, re.S)[0]


def now() -> datetime:
    return datetime.now(BERLIN).replace(second=0, microsecond=0)


def test_twelve_hours_after_the_wake_up_bed_and_woke_up_start_empty(web, fake):
    bed, up = now() - timedelta(hours=20), now() - timedelta(hours=13)
    ev("bed", bed)
    ev("wake", up)
    page = web[0].get("/web/").text
    for k, last in (("wake", up), ("bed", bed)):
        r = row(page, k)
        assert 'name="id" value=""' in r and f'id="h-{k}" value=""' in r
        assert f"last {last:%H:%M}" in r
    assert "12 hours after the wake-up" in page


def test_before_twelve_hours_they_still_correct_the_last_sleep(web, fake):
    bed, up = now() - timedelta(hours=18), now() - timedelta(hours=11)
    b, w = ev("bed", bed), ev("wake", up)
    page = web[0].get("/web/").text
    assert f'name="id" value="e{w}"' in row(page, "wake") and f'value="{up:%H:%M}"' in row(page, "wake")
    assert f'name="id" value="e{b}"' in row(page, "bed")


def test_the_typed_times_log_a_new_sleep_and_keep_the_last_one(web, fake):
    bed, up = now() - timedelta(hours=20), now() - timedelta(hours=13)
    ev("bed", bed)
    ev("wake", up)
    new_bed, new_up = now() - timedelta(hours=9), now() - timedelta(minutes=10)
    page = act(web, "/web/health", row="bed", id="", t=f"{new_bed:%H:%M}", key="n1")
    assert "Saved." in page.text and fake.calls == []
    assert times("bed") == [f"{bed:{FMT}}", f"{new_bed:{FMT}}"]  # before: anchored to the old wake-up, the day before it
    r = row(page.text, "bed")  # the new bed is shown and corrects itself
    bid = owner_sql("SELECT max(id) AS id FROM events")[0]["id"]
    assert f'name="id" value="e{bid}"' in r and f'value="{new_bed:%H:%M}"' in r
    fixed = new_bed - timedelta(minutes=30)
    act(web, "/web/health", row="bed", id=f"e{bid}", t=f"{fixed:%H:%M}", key="n2")
    assert times("bed") == [f"{bed:{FMT}}", f"{fixed:{FMT}}"]
    page = act(web, "/web/health", row="wake", id="", t=f"{new_up:%H:%M}", key="n3")
    assert "Saved." in page.text
    assert times("wake") == [f"{up:{FMT}}", f"{new_up:{FMT}}"]  # a new wake-up; the last one is where it was
    s = owner_sql("""SELECT bed_at AT TIME ZONE 'Europe/Berlin' AS b, wake_at AT TIME ZONE 'Europe/Berlin' AS w FROM v_sleep
                     ORDER BY wake_at""")
    assert [(f"{x['b']:{FMT}}", f"{x['w']:{FMT}}") for x in s] == [(f"{bed:{FMT}}", f"{up:{FMT}}"), (f"{fixed:{FMT}}", f"{new_up:{FMT}}")]
    r = row(page.text, "wake")  # the new day: Woke up shows the new wake-up again
    assert f'value="{new_up:%H:%M}"' in r and 'name="id" value=""' not in r
