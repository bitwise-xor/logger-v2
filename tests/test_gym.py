"""The Gym section, step (a) of 02.11.2026 (plan in the doc, section "Gym page plan"): the owner's exercises, ticking one into
today's workout (the first tick starts it and ticks the habit "gym" once), the numbers of the − / + editor, another row,
removing, Undo, Recent, the Today card and the menu."""
import re

from app import gym
from conftest import owner_sql
from test_web import act, n, web  # noqa: F401 - fixture reused


def ex(name, kind="weights"):
    return owner_sql("INSERT INTO exercises (name, kind) VALUES (%s, %s) RETURNING id", name, kind)[0]["id"]


def sets():
    return owner_sql("""SELECT x.name, s.sets, s.reps, s.kg, s.minutes, s.km FROM workout_sets s JOIN exercises x ON x.id = s.exercise_id
                        ORDER BY s.id""")


def gym_ticks():
    return n("SELECT count(*) AS n FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = 'gym'")


def token(page):
    return re.search(r'action="/web/undo/last">.*?name="token" value="([^"]+)"', page)[1]


def test_the_gym_pages_need_sign_in_and_show_in_the_menu(web):
    c = web[0]
    page = c.get("/web/gym").text
    assert "<h1>Gym</h1>" in page and "No exercises yet" in page and 'action="/web/gym/new"' in page
    assert page.count('href="/web/gym"') >= 3  # the top bar, the phone's bottom bar, the tab
    bottom = page[page.index('<nav class="tabs"'):]
    assert [m for m in re.findall(r'href="/web/(\w*)"', bottom.split("<details")[0])] == ["", "food", "health", "ideas", "gym", "home"]
    assert c.get("/web/gym/exercises").status_code == 200


def test_new_exercises_and_the_first_tick_starts_the_workout_and_ticks_gym_once(web):
    act(web, "/web/gym/new", name="Bench  press", kind="weights", words="bench, chest press")
    act(web, "/web/gym/new", name="Rowing machine", kind="cardio")
    assert [(r["name"], r["kind"], r["words"]) for r in owner_sql("SELECT name, kind, words FROM exercises ORDER BY id")] == \
        [("Bench press", "weights", ["bench", "chest press"]), ("Rowing machine", "cardio", [])]
    again = act(web, "/web/gym/new", name="bench press", kind="weights")
    assert "already in your list" in again.text
    bench, row = [r["id"] for r in owner_sql("SELECT id FROM exercises ORDER BY id")]
    page = act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="8", kg="60", key="g1")
    assert "Saved: Bench press 3 × 8 at 60 kg" in page.text
    act(web, "/web/gym/tick", exercise=str(row), minutes="20", km="3,5", key="g2")
    assert [(r["name"], r["sets"], r["reps"], r["kg"] and float(r["kg"]), r["minutes"] and float(r["minutes"]), r["km"] and float(r["km"]))
            for r in sets()] == [("Bench press", 3, 8, 60.0, None, None), ("Rowing machine", None, None, None, 20.0, 3.5)]
    assert n("SELECT count(*) AS n FROM workouts") == 1 and gym_ticks() == 1  # one workout, one gym tick a day
    assert [r["content"] for r in owner_sql("SELECT content FROM raw_inbox ORDER BY id")] == \
        ["gym: Bench press 3 × 8 at 60 kg", "gym: Rowing machine 20 min · 3.5 km"]
    twice = act(web, "/web/gym/tick", exercise=str(row), minutes="20", km="3,5", key="g2")  # a double tap
    assert len(sets()) == 2 and "Already saved earlier" in twice.text


def test_numbers_are_checked_for_the_kind(web):
    bench, push = ex("Bench press"), ex("Push-ups", "bodyweight")
    assert "Give the weight in kg." in act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="8", kg="").text
    assert "Numbers only" in act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="eight", kg="60").text
    assert "Numbers only" in act(web, "/web/gym/tick", exercise=str(bench), sets="3.5", reps="8", kg="60").text
    assert "Saved: Push-ups 3 × 15" in act(web, "/web/gym/tick", exercise=str(push), sets="3", reps="15").text
    assert gym.fmt("weights", 3, 8, 62.5) == "3 × 8 at 62.5 kg"


def test_the_workout_tab_repeats_last_time_and_shows_a_heavier_weight(web):
    bench = ex("Bench press")
    owner_sql("INSERT INTO workouts (day, started_at) VALUES (log_day(now()) - 2, now() - interval '2 days')")
    owner_sql("INSERT INTO workout_sets (workout_id, exercise_id, sets, reps, kg) SELECT id, %s, 3, 8, 57.5 FROM workouts", bench)
    page = web[0].get("/web/gym").text
    assert "Last " in page and "3 × 8 at 57.5 kg" in page
    tick = re.search(r'<form method="post" action="/web/gym/tick">(.*?)</form>', page, re.S)[1]
    assert 'name="kg" value="57.5"' in tick and 'name="sets" value="3"' in tick  # one tap repeats last time
    done = act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="8", kg="60", key="g3")
    assert '<span class="pill up">+2.5 kg</span>' in done.text and 'class="gtick done"' in done.text
    more = act(web, "/web/gym/tick", exercise=str(bench), sets="1", reps="6", kg="60", key="g4")  # a differing set: another row
    assert "3 × 8 at 60 kg, 1 × 6 at 60 kg" in more.text and 'action="/web/gym/remove"' in more.text


def test_removing_and_undo_put_everything_back(web):
    bench = ex("Bench press")
    page = act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="8", kg="60", key="g5")
    undone = act(web, "/web/undo/last", token=token(page.text))
    assert "Undone" in undone.text and sets() == [] and n("SELECT count(*) AS n FROM workouts") == 0 and gym_ticks() == 0
    act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="8", kg="60", key="g6")
    gone = act(web, "/web/gym/remove", exercise=str(bench))
    assert "Removed: Bench press 3 × 8 at 60 kg" in gone.text and sets() == [] and gym_ticks() == 0
    back = act(web, "/web/undo/last", token=token(gone.text))
    assert "it is back in today" in back.text and len(sets()) == 1 and gym_ticks() == 1 and n("SELECT count(*) AS n FROM workouts") == 1


def test_retire_and_bring_back(web):
    bench = ex("Bench press")
    act(web, "/web/gym/active", exercise=str(bench), on="0")
    assert "Bench press" not in web[0].get("/web/gym").text.split("Today’s workout")[1].split("Add exercise")[0]
    page = act(web, "/web/gym/new", name="BENCH PRESS", kind="weights")
    assert "Bench press is back in your list (weights)." in page.text
    assert owner_sql("SELECT active FROM exercises")[0]["active"] is True


def test_bringing_back_an_unused_exercise_takes_the_kind_picked_now(web):
    plank = ex("Plank")
    act(web, "/web/gym/active", exercise=str(plank), on="0")
    page = act(web, "/web/gym/new", name="plank", kind="bodyweight")
    assert "Plank is back in your list (bodyweight)." in page.text
    assert owner_sql("SELECT kind FROM exercises WHERE id = %s", plank)[0]["kind"] == "bodyweight"


def test_rename_keeps_the_history_and_the_old_name(web):
    bench, row = ex("Bench press"), ex("Rowing machine", "cardio")
    act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="8", kg="60", key="g8")
    page = web[0].get("/web/gym/exercises").text
    assert 'action="/web/gym/rename"' in page and f'name="exercise" value="{bench}"' in page
    done = act(web, "/web/gym/rename", exercise=str(bench), name="Chest  press")
    assert "Renamed Bench press to Chest press. Its history moved with it." in done.text
    assert owner_sql("SELECT name, words FROM exercises WHERE id = %s", bench)[0] == {"name": "Chest press", "words": ["Bench press"]}
    assert [r["name"] for r in sets()] == ["Chest press"]  # today's sets go with it
    assert "also: Bench press" in web[0].get("/web/gym/exercises").text
    assert "already in your list" in act(web, "/web/gym/rename", exercise=str(row), name="chest press").text
    assert "It is called Chest press already." in act(web, "/web/gym/rename", exercise=str(bench), name="Chest press").text
    act(web, "/web/gym/rename", exercise=str(bench), name="Bench press")  # back again: the old name leaves the other words
    assert owner_sql("SELECT name, words FROM exercises WHERE id = %s", bench)[0] == {"name": "Bench press", "words": ["Chest press"]}
    assert "Unknown exercise." in act(web, "/web/gym/rename", exercise="999", name="Squat").text


def test_today_card_and_recent(web):
    page = web[0].get("/web/").text
    assert 'class="card gymcard"' in page and "No workouts yet" in page and 'href="/web/gym"' in page
    bench = ex("Bench press")
    act(web, "/web/gym/tick", exercise=str(bench), sets="3", reps="8", kg="60", key="g7")
    page = web[0].get("/web/").text
    card = page[page.index('class="card gymcard"'):][:1500]
    assert "Last: " in card and "this week 1 workout" in card and card.count('class="gd on today"') == 1
    recent = web[0].get("/web/recent").text
    assert "Bench press 3 × 8 at 60 kg" in recent and ">Gym<" in recent
