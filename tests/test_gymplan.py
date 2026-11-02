"""The Gym section, step (b) of 02.11.2026 (plan and the owner's answers in the doc, section "Gym page plan"): the Plan tab (days,
their exercises in order with the plan's sets × reps, rename, remove and bring back), today's day (the first day not done yet this
week; picked by hand; Free workout), the plan's numbers the first time, the Today card's line and the starter split."""
import re
from datetime import date

from app import gym
from app.db import connect
from conftest import owner_sql
from test_gym import ex, token
from test_web import act, n, web  # noqa: F401 - fixture reused


def split_on(day):
    with connect() as conn, conn.cursor() as cur:
        return gym.split(cur, day)


def name(sp):
    return sp["day"] and sp["day"]["name"]


def ids():
    return {r["name"]: r["id"] for r in owner_sql("SELECT id, name FROM workout_days ORDER BY id")}


def worked(day, split):
    owner_sql("INSERT INTO workouts (day, started_at, split_day_id) VALUES (%s, now(), %s)", day, split)


def test_the_starter_split_is_made_once_and_reuses_an_exercise(web):
    legs = ex("leg press")
    assert gym.seed() == {"made": True, "days": 3, "exercises": 14}
    assert gym.seed()["made"] is False
    assert [r["name"] for r in owner_sql("SELECT name FROM workout_days ORDER BY sort_order")] == ["Push", "Pull", "Legs"]
    assert n("SELECT count(*) AS n FROM exercises") == 15 and n("SELECT count(*) AS n FROM workout_day_items") == 15
    assert n("SELECT count(*) AS n FROM workout_day_items WHERE exercise_id = %s", legs) == 1
    first = owner_sql("""SELECT x.name, i.sets, i.reps FROM workout_day_items i JOIN exercises x ON x.id = i.exercise_id
                         JOIN workout_days d ON d.id = i.day_id WHERE d.name = 'Push' ORDER BY i.sort_order""")
    assert (first[0]["name"], first[0]["sets"], first[0]["reps"]) == ("Chest press", 3, 10) and first[-1]["name"] == "Treadmill"


def test_the_day_is_the_first_not_done_yet_this_week(web):
    gym.seed()
    d = ids()
    mon, wed = date(2026, 10, 26), date(2026, 10, 28)
    assert name(split_on(wed)) == "Push" and split_on(wed)["nth"] == 1
    worked(date(2026, 10, 25), d["Push"])  # last week's Sunday does not count
    assert name(split_on(mon)) == "Push"
    worked(mon, d["Pull"])  # Monday: Day 2 picked by hand
    assert name(split_on(wed)) == "Push" and split_on(wed)["nth"] == 2
    worked(date(2026, 10, 27), d["Push"])
    assert name(split_on(wed)) == "Legs"
    worked(wed, d["Legs"])
    sp = split_on(wed)  # today's workout keeps its day
    assert name(sp) == "Legs" and sp["started"] and not sp["hand"]
    assert name(split_on(date(2026, 10, 29))) == "Push"  # every day done: Day 1 again
    owner_sql("UPDATE workout_days SET active = false WHERE name = 'Push'")
    assert name(split_on(date(2026, 11, 2))) == "Pull" and split_on(date(2026, 11, 2))["days"][0]["name"] == "Pull"


def test_a_day_picked_by_hand_and_free_workout(web):
    gym.seed()
    d = ids()
    page = web[0].get("/web/gym").text
    assert "Day 1 · Push" in page and 'class="chip on"' in page and "the first day not done yet" in page
    picked = act(web, "/web/gym/pick", split=str(d["Legs"]))
    assert "Today: Day 3 · Legs." in picked.text and "Picked by you instead of Day 1 · Push." in picked.text
    assert picked.text.index("Leg press") < picked.text.index("Other exercises (10)")
    leg = owner_sql("SELECT id FROM exercises WHERE name = 'Leg press'")[0]["id"]
    act(web, "/web/gym/tick", exercise=str(leg), sets="3", reps="10", kg="80", key="p1")
    assert owner_sql("SELECT split_day_id FROM workouts")[0]["split_day_id"] == d["Legs"]
    act(web, "/web/gym/pick", split=str(d["Pull"]))  # a started workout moves with the day
    assert owner_sql("SELECT split_day_id FROM workouts")[0]["split_day_id"] == d["Pull"]
    free = act(web, "/web/gym/pick", split="free")
    assert "Free workout: all your exercises." in free.text and owner_sql("SELECT split_day_id FROM workouts")[0]["split_day_id"] is None
    assert "Other exercises" not in free.text and "Unknown day." in act(web, "/web/gym/pick", split="999").text
    today = web[0].get("/web/").text
    assert '<p class="gtoday">Today: Free workout</p>' in today


def test_the_plan_numbers_are_used_the_first_time(web):
    gym.seed()
    page = web[0].get("/web/gym").text
    row = page[page.index(">Chest press<"):][:2500]
    assert "Not done before · plan 3 × 10" in row and 'name="sets" value="3"' in row and 'name="reps" value="10"' in row
    assert page.count('class="gedit" open') == 1  # only the first new exercise opens its numbers
    chest = owner_sql("SELECT id FROM exercises WHERE name = 'Chest press'")[0]["id"]
    owner_sql("INSERT INTO workouts (day, started_at) VALUES (log_day(now()) - 8, now() - interval '8 days')")
    owner_sql("INSERT INTO workout_sets (workout_id, exercise_id, sets, reps, kg) SELECT id, %s, 4, 6, 40 FROM workouts", chest)
    row = web[0].get("/web/gym").text
    row = row[row.index(">Chest press<"):].split('<div class="gx')[0]  # this row only
    assert "plan 3 × 10" not in row and 'name="sets" value="4"' in row  # after that, last time's numbers
    assert '<p class="gtoday">Today: Day 1 · Push</p>' in web[0].get("/web/").text


def test_the_plan_tab_edits_days_and_their_exercises(web):
    act = lambda w, path, **data: globals()["act"](w, path, back="/web/gym/plan", **data)  # the Plan tab's forms come back to it
    c = web[0]
    page = c.get("/web/gym/plan").text
    assert 'href="/web/gym/plan" aria-current="page"' in page and "How the day is picked" in page and "New workout day" in page
    assert "Type the day" in act(web, "/web/gym/daynew", name=" ").text
    page = act(web, "/web/gym/daynew", name="Upper")
    assert "Added Day 1 · Upper." in page.text and "already a day" in act(web, "/web/gym/daynew", name="upper").text
    day = ids()["Upper"]
    a, b, k = ex("Bench press"), ex("Row"), ex("Rowing machine", "cardio")
    assert "Give both sets and reps" in act(web, "/web/gym/itemadd", day=str(day), exercise=str(a), sets="3").text
    act(web, "/web/gym/itemadd", day=str(day), exercise=str(a), sets="3", reps="8")
    act(web, "/web/gym/itemadd", day=str(day), exercise=str(b))
    act(web, "/web/gym/itemadd", day=str(day), exercise=str(k))
    assert "already on Upper" in act(web, "/web/gym/itemadd", day=str(day), exercise=str(a)).text
    order = lambda: [r["name"] for r in owner_sql("SELECT x.name FROM workout_day_items i JOIN exercises x ON x.id = i.exercise_id ORDER BY i.sort_order, i.id")]
    assert order() == ["Bench press", "Row", "Rowing machine"]
    item = lambda nm: owner_sql("SELECT i.id FROM workout_day_items i JOIN exercises x ON x.id = i.exercise_id WHERE x.name = %s", nm)[0]["id"]
    act(web, "/web/gym/itemmove", item=str(item("Rowing machine")), dir="up")
    act(web, "/web/gym/itemmove", item=str(item("Bench press")), dir="up")  # already first: stays
    assert order() == ["Bench press", "Rowing machine", "Row"]
    page = act(web, "/web/gym/itemset", item=str(item("Row")), sets="4", reps="12")
    assert "Row: plan 4 × 12." in page.text and "Weights · plan 4 × 12" in page.text
    gone = act(web, "/web/gym/itemremove", item=str(item("Row")))
    assert "Took Row off Upper." in gone.text and order() == ["Bench press", "Rowing machine"]
    back = act(web, "/web/undo/last", token=token(gone.text))
    assert "Row is back on Upper" in back.text and order() == ["Bench press", "Rowing machine", "Row"]
    act(web, "/web/gym/dayedit", day=str(day), name="Upper body")
    act(web, "/web/gym/daynew", name="Lower")
    page = act(web, "/web/gym/dayactive", day=str(day), on="0")
    assert "Removed Upper body" in page.text and "Removed days" in page.text and "Day 1 · Lower" in page.text
    page = act(web, "/web/gym/dayactive", day=str(day), on="1")
    assert "Upper body is back" in page.text and "Day 2 · Upper body" in page.text and "Removed days" not in page.text


def test_the_split_card_and_no_split(web):
    page = web[0].get("/web/gym").text
    assert "No split yet" in page and "gsplit" not in page and "gtoday" not in web[0].get("/web/").text
    gym.seed()
    page = web[0].get("/web/gym").text
    card = page[page.index('class="card gsplit"'):][:3000]
    assert re.search(r"Day 1 · Push.*Chest press, Shoulder press.*Today", card, re.S) and "Day 3 · Legs" in card
