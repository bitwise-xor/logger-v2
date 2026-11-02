"""The watch data, Gym step (d) of 02.11.2026 (plan and the owner's answers in the doc, section "Step (d), Activity"): the
receiving address /watch/health (token, size, format), the readings kept once when the app sends them again, units turned into
kcal and km, sleep as a summary or as segments, watch workouts, and the Activity tab (set-up help before any data, the day's
tiles, the night's sleep next to the typed times, a workout matched to the Logger's)."""
import json
from datetime import datetime, timedelta

import pytest

from app import rules, watch
from conftest import owner_sql
from test_gym import ex
from test_web import act, client, n, web  # noqa: F401 - fixture reused

TOKEN = "t" * 32
URL = watch.PATH


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("WATCH_TOKEN", TOKEN)


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %z")


def payload(day):
    """What Health Auto Export sends (JSON, export version 2), summarised by hour, for one day."""
    at = lambda h: stamp(datetime.combine(day, datetime.min.time(), rules.BERLIN) + timedelta(hours=h))
    return {"data": {
        "metrics": [
            {"name": "step_count", "units": "count", "data": [{"qty": 1200, "date": at(8), "source": "Watch"}, {"qty": 800, "date": at(9), "source": "Watch"},
                                                             {"qty": 1500, "date": at(9), "source": "iPhone"}]},
            {"name": "active_energy", "units": "kJ", "data": [{"qty": 418.4, "date": at(8), "source": "Watch"}]},
            {"name": "walking_running_distance", "units": "mi", "data": [{"qty": 1, "date": at(8), "source": "Watch"}]},
            {"name": "resting_heart_rate", "units": "count/min", "data": [{"qty": 58, "date": at(0), "source": "Watch"}]},
            {"name": "heart_rate", "units": "count/min", "data": [{"Min": 50, "Avg": 70, "Max": 120, "date": at(8), "source": "Watch"}]},
            {"name": "sleep_analysis", "units": "hr", "data": [{"date": at(0), "totalSleep": 7.25, "deep": 1, "core": 4.5, "rem": 1.75, "awake": 0.4,
                                                               "sleepStart": at(-1.5), "sleepEnd": at(6.5), "inBedStart": at(-1.75), "inBedEnd": at(6.75),
                                                               "source": "Watch"}]},
            {"name": "vo2_max", "units": "ml/(kg·min)", "data": [{"qty": 40, "date": at(8)}]},
        ],
        "workouts": [{"id": "A1B2", "name": "Traditional Strength Training", "start": at(18), "end": at(19),
                      "activeEnergyBurned": {"qty": 300, "units": "kcal"}, "heartRate": {"avg": {"qty": 121, "units": "bpm"}, "max": {"qty": 150, "units": "bpm"}}}],
    }}


def post(c, body, auth=f"Bearer {TOKEN}"):
    return c.post(URL, content=json.dumps(body), headers={"Authorization": auth, "Content-Type": "application/json"})


def test_the_address_needs_its_token_and_a_data_object(monkeypatch):
    c = client("log.19hospitalroad.fyi")
    monkeypatch.delenv("WATCH_TOKEN", raising=False)
    assert post(c, {"data": {}}).status_code == 503  # not set up yet
    monkeypatch.setenv("WATCH_TOKEN", TOKEN)
    assert post(c, {"data": {}}, auth="Bearer wrong").status_code == 401 and post(c, {"data": {}}, auth="").status_code == 401
    assert post(c, {"metrics": []}).status_code == 400
    assert post(c, {"data": {}}).json() == {"ok": True, "metrics": 0, "workouts": 0, "skipped": 0}


def test_readings_are_kept_once_in_kcal_and_km(token):
    c = client("log.19hospitalroad.fyi")
    day = datetime.now(rules.BERLIN).date() - timedelta(days=1)
    r = post(c, payload(day))
    assert r.status_code == 200 and r.json() == {"ok": True, "metrics": 8, "workouts": 1, "skipped": 1}
    post(c, payload(day))  # every run sends yesterday and today again
    assert n("SELECT count(*) AS n FROM watch_metrics") == 8 and n("SELECT count(*) AS n FROM watch_workouts") == 1
    v = {r["metric"]: r for r in owner_sql("SELECT metric, value, unit, extra FROM watch_metrics WHERE source = 'Watch'")}
    assert float(v["active_energy"]["value"]) == pytest.approx(100) and v["active_energy"]["unit"] == "kcal"
    assert float(v["walking_running_distance"]["value"]) == pytest.approx(1.609, abs=0.001)
    assert v["heart_rate"]["extra"] == {"min": 50.0, "avg": 70.0, "max": 120.0} and v["sleep_analysis"]["extra"]["kind"] == "night"
    w = owner_sql("SELECT * FROM watch_workouts")[0]
    assert (w["apple_id"], float(w["kcal"]), float(w["hr_avg"])) == ("A1B2", 300.0, 121.0)
    assert owner_sql("SELECT detail FROM audit_log WHERE tool = 'watch'")[-1]["detail"] == {"metrics": 8, "workouts": 1, "skipped": 1}


def test_the_activity_tab(token, web):
    page = web[0].get("/web/gym/activity").text
    assert "No watch data yet" in page and watch.PATH in page and "is set on the server" in page
    assert 'href="/web/gym/activity" aria-current="page"' in page
    day = datetime.now(rules.BERLIN).date() - timedelta(days=1)
    post(client("log.19hospitalroad.fyi"), payload(day))
    bench = ex("Bench press")
    owner_sql("""INSERT INTO workouts (day, started_at) VALUES (%s, %s)""", day,
              datetime.combine(day, datetime.min.time(), rules.BERLIN) + timedelta(hours=18, minutes=5))
    page = web[0].get(f"/web/gym/activity?day={day}").text
    assert "Yesterday" in page and "2 000" in page and "3 500" not in page  # one source's steps, the larger, not both added up
    assert "100 kcal" in page and "58 bpm" in page and "7 h 15 min" in page and "Deep 1 h 00 min" in page and "in bed 22:15–06:45" in page
    assert "Traditional Strength Training" in page and "Matches Free workout in the Logger" in page and 'id="c-steps"' in page
    assert bench and "Nothing typed in the Logger for this night." in page
    assert "Today" in web[0].get("/web/gym/activity?day=2999-01-01").text  # a future day: today


def test_sleep_sent_as_segments(token, web):
    day = datetime.now(rules.BERLIN).date()
    at = lambda h: stamp(datetime.combine(day, datetime.min.time(), rules.BERLIN) + timedelta(hours=h))
    seg = lambda a, b, stage: {"startDate": at(a), "endDate": at(b), "qty": b - a, "value": stage, "source": "Watch", "date": at(a)}
    body = {"data": {"metrics": [{"name": "sleep_analysis", "units": "hr", "data": [
        seg(-1, 0, "Core"), seg(0, 1, "Deep"), seg(1, 1.5, "Awake"), seg(1.5, 6, "Core"), seg(6, 7, "REM")]}]}}
    assert post(client("log.19hospitalroad.fyi"), body).json()["metrics"] == 5
    page = web[0].get("/web/gym/activity").text
    assert "7 h 30 min" in page and "Awake 0 h 30 min" in page and "Core 5 h 30 min" in page
