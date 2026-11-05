"""The Gym section, step (c) of 02.11.2026 (plan in the doc, section "Gym page plan"; the owner's answer: gym days on the Week
page only, not in the Sheet): the Progress tab (one exercise over 8 weeks, workouts per week, recent sessions), the History tab
(workouts by date with their day of the split) and the Gym row on the Week page."""
import re

from app import gym
from conftest import owner_sql
from test_gym import ex
from test_web import n, web  # noqa: F401 - fixture reused


def session(days_ago, rows, split=None):
    """A past workout: rows of (exercise id, sets, reps, kg, minutes)."""
    w = owner_sql("""INSERT INTO workouts (day, started_at, split_day_id) VALUES (log_day(now()) - %s, now() - make_interval(days => %s), %s)
                     RETURNING id""", days_ago, days_ago, split)[0]["id"]
    for i, (x, sets, reps, kg, minutes) in enumerate(rows):
        owner_sql("""INSERT INTO workout_sets (workout_id, exercise_id, sets, reps, kg, minutes, sort_order, logged_at)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, now() - make_interval(days => %s) + make_interval(mins => %s))""",
                  w, x, sets, reps, kg, minutes, i, days_ago, 10 * i)
    return w


def test_the_tabs_and_the_empty_pages(web):
    page = web[0].get("/web/gym/progress").text
    # Activity shows once watch data has come (04.11.2026); its own page still opens and keeps its tab
    nav = re.findall(r'<nav class="seg" aria-label="Gym">(.*?)</nav>', page)[0]
    assert nav.count("<a ") == 5 and "/web/gym/activity" not in nav
    act_page = web[0].get("/web/gym/activity").text
    assert re.findall(r'<nav class="seg" aria-label="Gym">(.*?)</nav>', act_page)[0].count("<a ") == 6
    assert 'href="/web/gym/progress" aria-current="page"' in page and "Nothing to show yet" in page and "Workouts per week" in page
    assert "No workouts yet." in web[0].get("/web/gym/history").text


def test_progress_of_one_exercise(web):
    bench, bike = ex("Bench press"), ex("Exercise bike", "cardio")
    session(30, [(bench, 3, 8, 55, None)])
    session(9, [(bench, 3, 8, 57.5, None), (bench, 1, 6, 57.5, None)])
    session(2, [(bench, 3, 8, 60, None)])
    session(1, [(bike, None, None, None, 20)])
    page = web[0].get("/web/gym/progress").text
    assert 'class="chip on" href="/web/gym/progress?x=%d"' % bike in page  # the most recently done first
    page = web[0].get(f"/web/gym/progress?x={bench}").text
    assert "Bench press: top weight" in page and '<span class="num">60 kg</span>' in page and "+5 kg since" in page
    assert "estimated 1-rep max ≈ 76 kg" in page and 'id="c-gym" data-chart=' in page
    recent = page[page.index("Recent sessions"):]
    assert recent.index("3 × 8 at 60 kg") < recent.index("3 × 8 at 57.5 kg, 1 × 6 at 57.5 kg") < recent.index("3 × 8 at 55 kg")
    assert re.search(r"[1-9] of 8 weeks with 2\+|0 of 8 weeks with 2\+", page)
    cardio = web[0].get(f"/web/gym/progress?x={bike}").text
    assert "Exercise bike: longest session" in cardio and "20 min" in cardio and "one session so far" in cardio
    assert "Exercise bike" in web[0].get("/web/gym/progress?x=999").text  # an unknown id falls back to the latest


def test_history_by_date_with_the_day_of_the_split(web):
    gym.seed()
    push = owner_sql("SELECT id FROM workout_days WHERE name = 'Push'")[0]["id"]
    chest, bike = [owner_sql("SELECT id FROM exercises WHERE name = %s", x)[0]["id"] for x in ("Chest press", "Exercise bike")]
    session(3, [(chest, 3, 10, 40, None), (bike, None, None, None, 15)], push)
    session(1, [(chest, 3, 10, 42.5, None)])
    page = web[0].get("/web/gym/history").text
    cards = re.findall(r'<div class="card" id="w\d+"><h2><span>(.*?)</span><span class="small">(.*?)</span></h2>(.*?)</div>', page, re.S)
    assert len(cards) == 2 and "Free workout" in cards[0][1] and "Push" in cards[1][1] and "Day 1" not in cards[1][1]
    assert re.search(r"\d\d:\d\d–\d\d:\d\d", cards[1][1])  # from the first tick to the last
    assert "Chest press" in cards[1][2] and "3 × 10 at 40 kg" in cards[1][2] and "15 min" in cards[1][2]


def test_gym_days_on_the_week_page(web):
    assert "<td>Gym</td>" not in web[0].get("/web/week").text  # no gym list: no row
    gym.seed()
    week = web[0].get("/web/week").text
    assert "<td>Gym</td><td>0 workouts</td>" in week
    push = owner_sql("SELECT id FROM workout_days WHERE name = 'Push'")[0]["id"]
    chest = owner_sql("SELECT id FROM exercises WHERE name = 'Chest press'")[0]["id"]
    session(2, [(chest, 3, 10, 40, None)], push)
    session(3, [(chest, 3, 10, 40, None)])
    week = web[0].get("/web/week").text
    row = re.search(r"<td>Gym</td><td>(.*?)</td>", week)[1]
    assert row.startswith("2 workouts") and "(Push)" in row and n("SELECT count(*) AS n FROM workouts") == 2


def test_the_circle_repeats_the_heaviest_row_and_is_ready_for_a_first_cardio(web):
    """04.11.2026: the circle took the last session's first row (the lightest of a pyramid) and was greyed out the first time
    even when the numbers were complete (cardio's 20 minutes)."""
    bench, bike = ex("Test bench"), ex("Test bike", "cardio")
    session(2, [(bench, 3, 10, 40, None), (bench, 3, 8, 50, None), (bench, 2, 6, 55, None)])
    page = web[0].get("/web/gym").text
    assert 'aria-label="Log Test bench: 2 × 6 at 55 kg"' in page
    assert 'aria-label="Log Test bike: 20 min"' in page


def test_an_exercise_added_on_the_workout_tab_goes_on_todays_day(web):
    from test_web import act
    gym.seed()
    page = act(web, "/web/gym/new", name="Test face pull", kind="weights", today="1").text
    assert "It is on today’s" in page
    xid = owner_sql("SELECT id FROM exercises WHERE name = 'Test face pull'")[0]["id"]
    assert n("SELECT count(*) AS n FROM workout_day_items WHERE exercise_id = %s", xid) == 1
    assert 'id="x%d"' % xid in web[0].get("/web/gym").text.split("Other exercises")[0]
