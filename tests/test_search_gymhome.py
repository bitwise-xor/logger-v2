"""Search also finds gym workouts (by exercise name, its other words and the day of the split) and chores done (by chore and zone
name and their words), 05.11.2026. Every value here is the starter list or invented."""
import html

import pytest

from app import home
from conftest import owner_sql
from test_web import web  # noqa: F401 - fixture reused


def ex(name, kind="weights", words=()):
    return owner_sql("INSERT INTO exercises (name, kind, words) VALUES (%s, %s, %s) RETURNING id", name, kind, list(words))[0]["id"]


def workout(days_ago, split=None, rows=()):
    w = owner_sql("""INSERT INTO workouts (day, started_at, split_day_id) VALUES (log_day(now()) - %s, now() - make_interval(days => %s), %s)
                     RETURNING id""", days_ago, days_ago, split)[0]["id"]
    for i, (x, sets, reps, kg) in enumerate(rows):
        owner_sql("INSERT INTO workout_sets (workout_id, exercise_id, sets, reps, kg, sort_order) VALUES (%s, %s, %s, %s, %s, %s)",
                  w, x, sets, reps, kg, i)
    return w


def group(web, q, title):
    page = html.unescape(web[0].get("/web/search", params={"q": q}).text)
    return page.split(f"<h2>{title} ")[1].split('<div class="card">')[0]


def test_workouts_are_found_by_exercise_its_words_and_the_day(web):
    bench, squat = ex("Bench press", words=("chest press",)), ex("Kniebeuge")
    push = owner_sql("INSERT INTO workout_days (name) VALUES ('Push') RETURNING id")[0]["id"]
    old = workout(9, None, [(squat, 3, 5, 80)])
    new = workout(2, push, [(bench, 3, 8, 60), (bench, 1, 6, 65), (squat, 3, 5, 85)])
    g = group(web, "bench", "Gym workouts")
    assert g.startswith('<span class="small">1</span>') and f'href="/web/gym/history#w{new}"' in g
    assert "Push" in g and "Bench press 3 × 8 at 60 kg, 1 × 6 at 65 kg" in g and "Kniebeuge" not in g  # only the matching exercise
    assert f"#w{new}" in group(web, "chest", "Gym workouts")  # the exercise's other words
    g = group(web, "push", "Gym workouts")  # only the day matches: the exercise names
    assert "Bench press, Kniebeuge" in g and f"#w{old}" not in g
    g = group(web, "kniebeuge", "Gym workouts")
    assert g.index(f"#w{new}") < g.index(f"#w{old}") and "Free workout" in g  # newest first
    assert "Nothing found." in group(web, "deadlift", "Gym workouts")
    assert f'id="w{new}"' in web[0].get("/web/gym/history").text  # the link's place on History


@pytest.fixture
def starter():
    return home.seed()


def zid(name):
    return owner_sql("SELECT id FROM home_zones WHERE name = %s", name)[0]["id"]


def cid(name, zone=None):
    return owner_sql("SELECT c.id FROM home_chores c LEFT JOIN home_zones z ON z.id = c.zone_id WHERE c.name = %s AND (%s::text IS NULL OR z.name = %s)",
                     name, zone, zone)[0]["id"]


def done(chore, zones, days_ago, source="web"):
    owner_sql("""INSERT INTO home_done (chore_id, zone_id, done_at, day, source)
                 SELECT %s, z, now() - make_interval(days => %s), log_day(now()) - %s, %s FROM unnest(%s::bigint[]) z""",
              chore, days_ago, days_ago, source, zones)


def test_chores_done_are_found_by_chore_zone_and_their_words(web, starter):
    tub, bath, kitchen = cid("Bathtub", "Bathroom"), zid("Bathroom"), zid("Kitchen")
    done(tub, [bath], 3)
    done(cid("Dust"), [bath, kitchen], 1)  # a whole-flat chore stamped in two zones at once
    done(tub, [bath], 30, "start")
    g = group(web, "tub", "Chores done")
    assert g.startswith('<span class="small">2</span>') and g.count(f'href="/web/home/chore?id={tub}"') == 2
    assert "Bathtub · Bathroom</a></p>" in g and "Bathtub · Bathroom</a> <span class=\"small\">starting point</span>" in g
    g = group(web, "dust", "Chores done")
    assert "Dust · Bathroom, Kitchen" in g and g.count("<p>") == 1  # stamped together: one line
    g = group(web, "bath", "Chores done")  # the zone: only its own rows of the whole-flat chore
    assert "Dust · Bathroom</a>" in g and "Kitchen" not in g and g.index("Dust") < g.index("Bathtub")
    assert "Bathtub · Bathroom" in group(web, "shower", "Chores done")  # the chore's words
    assert "Nothing found." in group(web, "garage", "Chores done")
