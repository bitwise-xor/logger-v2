"""The day follows the wake-up (05.11.2026, cycle.py, migration 029): for an irregular or drifting sleep cycle the logging day starts
at the wake-up (also between midnight and 05:00), a second wake-up on one date starts the next date, a wake-up soon after the last one
or after a short sleep asks "a new day, or up from a nap?", naps count as sleep without starting a day, the Health page draws the sleep
times, and the watch can offer a wake-up that was not typed."""
from datetime import date, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import web  # noqa: F401
from app import cycle, logbook, webactions, webquestions
from app.db import connect
from app.when import BERLIN


@pytest.fixture(autouse=True)
def _no_ai(fake):  # noqa: F811 - no test here reaches the AI; the fake stops a real call
    return fake


def at(d: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 11, d, h, m, tzinfo=BERLIN)


def item(kind: str, when: datetime, value=None) -> dict:
    return {"type": kind, "value": value, "value2": None, "period": None, "session": None, "when": f"{when:%Y-%m-%d %H:%M}"}


def log(monkeypatch, now: datetime, *items, text: str = "sleep"):
    monkeypatch.setattr(logbook, "clock", lambda: now)
    return logbook.log(f"{text} {now:%d%H%M}", f"k-{now:%d%H%M}-{text}", webactions._pred(health=list(items)), channel=logbook.WEB)


def days(name: str) -> list:
    return [r["day"] for r in owner_sql("""SELECT e.day FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = %s
                                           ORDER BY e.event_at""", name)]


def weight_day():
    return owner_sql("SELECT m.day FROM measurements m JOIN log_types t ON t.id = m.type_id WHERE t.name = 'weight'")[0]["day"]


def log_day(ts: datetime) -> date:
    return owner_sql("SELECT log_day(%s) AS d", ts)[0]["d"]


def test_a_wake_up_after_midnight_starts_its_day_at_once(monkeypatch):
    """To bed in the evening, up after midnight: until 05:00 the pages showed the day before and left both out (found 05.11.2026)."""
    out = log(monkeypatch, at(5, 0, 26), item("bed", at(4, 19, 20)), item("wake", at(5, 0, 20)))
    assert out["outcome"] == "saved", out
    assert days("wake") == [date(2026, 11, 5)]
    assert log_day(at(5, 1, 30)) == date(2026, 11, 5)  # was 04.11. (the 05:00 rule)
    assert owner_sql("SELECT hours FROM v_sleep")[0]["hours"] == 5
    assert log_day(at(6, 1, 30)) == date(2026, 11, 5)  # 25 hours later, no new wake-up: the 05:00 rule again


def test_an_entry_after_the_wake_up_in_the_same_message_is_on_its_day(monkeypatch):
    log(monkeypatch, at(5, 0, 30), item("wake", at(5, 0, 20)), item("weight", at(5, 0, 25), 80))
    assert weight_day() == date(2026, 11, 5)


def test_an_entry_logged_before_its_wake_up_moves_to_that_day(monkeypatch):
    log(monkeypatch, at(5, 0, 31), item("weight", at(5, 0, 30), 80), text="weight")
    assert weight_day() == date(2026, 11, 4)  # no wake-up yet: the 05:00 rule
    out = log(monkeypatch, at(5, 0, 40), item("wake", at(5, 0, 20)))
    assert weight_day() == date(2026, 11, 5)
    assert any("1 health entry logged after this wake-up moved to 05.11." in n for n in out["notes"]), out


def test_a_second_wake_up_on_one_date_starts_the_next_date(monkeypatch):
    log(monkeypatch, at(5, 0, 20), item("wake", at(5, 0, 20)))
    out = log(monkeypatch, at(5, 23, 45), item("wake", at(5, 23, 40)), text="up again")
    assert out["outcome"] == "saved", out  # 23 h 20 min later: a new day, no question and no "already logged"
    assert days("wake") == [date(2026, 11, 5), date(2026, 11, 6)]
    assert log_day(at(5, 23, 50)) == date(2026, 11, 6)


def test_a_date_the_cycle_skips_stays_empty(monkeypatch):
    log(monkeypatch, at(4, 23, 55), item("wake", at(4, 23, 50)))
    log(monkeypatch, at(6, 0, 35), item("wake", at(6, 0, 30)), text="up again")
    assert days("wake") == [date(2026, 11, 4), date(2026, 11, 6)]


def test_a_wake_up_soon_after_the_last_one_asks_and_nap_keeps_the_day(monkeypatch):
    log(monkeypatch, at(5, 0, 20), item("wake", at(5, 0, 20)))
    out = log(monkeypatch, at(5, 4, 5), item("bed", at(5, 2)), item("wake", at(5, 4)), text="night")
    assert out["outcome"] == "needs_confirmation" and days("wake") == [date(2026, 11, 5)]
    assert "3 h 40 min after the last wake-up (05.11. 00:20)" in out["message"] and cycle.NAP_HINT in out["message"]
    p = owner_sql("SELECT id, payload FROM pending WHERE tool = 'log_confirm'")[0]
    assert p["payload"]["nap"] is True
    # asked at the test's clock (05.11.2026), so it ended 24 h later by the real clock that the card and the answer use
    owner_sql("UPDATE pending SET expires_at = now() + interval '1 day' WHERE id = %s", p["id"])
    card = webquestions.today_section("csrf")
    assert 'value="nap"' in card and "New day" in card and "A new day, or up from a nap or a night waking?" in card
    out = logbook.answer(p["id"], "nap", logbook.WEB)
    assert out["outcome"] == "saved", out
    assert days("wake") == [date(2026, 11, 5)] and days("nap") == [date(2026, 11, 5)]
    assert log_day(at(5, 6)) == date(2026, 11, 5)  # the nap starts no day
    nap = owner_sql("SELECT nap, hours FROM v_sleeps WHERE nap")
    assert [(r["nap"], float(r["hours"])) for r in nap] == [(True, 2.0)]


def test_yes_to_the_question_starts_a_new_day(monkeypatch):
    log(monkeypatch, at(5, 0, 20), item("wake", at(5, 0, 20)))
    log(monkeypatch, at(5, 9, 5), item("wake", at(5, 9)), text="up")
    p = owner_sql("SELECT id FROM pending WHERE tool = 'log_confirm'")[0]
    assert logbook.answer(p["id"], "yes", logbook.WEB)["outcome"] == "saved"
    assert days("wake") == [date(2026, 11, 5), date(2026, 11, 6)]


def test_a_short_sleep_asks_too(monkeypatch):
    log(monkeypatch, at(4, 8, 5), item("wake", at(4, 8)))
    out = log(monkeypatch, at(5, 2, 5), item("bed", at(5, 1)), item("wake", at(5, 2)), text="short")
    assert out["outcome"] == "needs_confirmation" and "after 1 h 00 min of sleep" in out["message"], out


def test_a_bed_before_a_nap_is_not_the_bed_of_the_next_night(monkeypatch):
    log(monkeypatch, at(5, 0, 20), item("wake", at(5, 0, 20)))
    log(monkeypatch, at(5, 11, 35), item("bed", at(5, 10)), item("nap", at(5, 11, 30)), text="nap")
    assert days("nap") == [date(2026, 11, 5)] and days("bed") == [date(2026, 11, 5)]
    log(monkeypatch, at(6, 1, 5), item("wake", at(6, 1)), text="up")  # no bed typed for this night
    assert days("wake") == [date(2026, 11, 5), date(2026, 11, 6)]
    assert owner_sql("SELECT count(*) AS n FROM v_sleep WHERE wake_day = '2026-11-06'")[0]["n"] == 0


def test_the_watch_offers_a_wake_up_that_was_not_typed(web):  # noqa: F811
    now = datetime.now(BERLIN)
    end = (now - timedelta(hours=1)).replace(second=0, microsecond=0)
    owner_sql("""INSERT INTO watch_metrics (metric, at, value, unit, source, extra) VALUES ('sleep_analysis', %s, 6, 'hr', 'watch', %s)""",
              end - timedelta(hours=6), Jsonb({"kind": "night", "sleepEnd": end.strftime("%Y-%m-%d %H:%M:%S %z")}))
    with connect() as c, c.cursor() as cur:
        assert cycle.watch_wake(cur, now) == end
    page = web[0].get("/web/").text
    assert "Your watch says you got up" in page and f'name="t" value="{end:%H:%M}"' in page
    rid = owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('web', 'woke up') RETURNING id")[0]["id"]
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                 SELECT %s, id, %s, %s, true, 'test' FROM log_types WHERE name = 'wake'""", rid, end.date(), end)
    with connect() as c, c.cursor() as cur:
        assert cycle.watch_wake(cur, now) is None  # typed now: not offered again
    assert "Your watch says you got up" not in web[0].get("/web/").text


def test_the_health_page_draws_the_sleep_times(web, monkeypatch):  # noqa: F811
    now = datetime.now(BERLIN).replace(second=0, microsecond=0)
    log(monkeypatch, now, item("bed", now - timedelta(hours=8)), item("wake", now - timedelta(hours=1)))
    page = web[0].get("/web/health").text
    assert "Sleep times" in page and 'class="times"' in page and '<rect class="slp"' in page
    assert page.index("Sleep times") < page.index('id="body"')


def test_after_the_wake_up_the_day_that_ended_is_still_asked_about():
    """The check-in of the day that just ended stays on offer in the 10 hours after the wake-up (it vanished when the wake-up started
    the new day at once)."""
    from app import todaypage
    up = datetime.now(BERLIN).replace(second=0, microsecond=0) - timedelta(hours=1)
    rid = owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('web', 'woke up') RETURNING id")[0]["id"]
    for ts in (up - timedelta(hours=24), up):
        owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                     SELECT %s, id, wake_day(%s), %s, true, 'test' FROM log_types WHERE name = 'wake'""", rid, ts, ts)
    d = todaypage.load()
    assert d["today"] == owner_sql("SELECT wake_day(%s) AS d", up)[0]["d"]
    assert d["ci_day"] == d["today"] - timedelta(days=1)
