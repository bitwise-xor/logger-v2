"""Making a usual meal or drink from foods typed by name and grams on the Food page's Usual meals tab (29.10.2026), and adding a
food by name to an existing usual meal."""
import html

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, n, web  # noqa: F401
from test_webtoday import food, no_targets  # noqa: F401 - no_targets: clean targets and usual meals


def meals():
    return owner_sql("SELECT id, name, is_drink FROM usual_meals ORDER BY id")


def items(mid):
    return [(r["name"], float(r["grams"])) for r in owner_sql("""SELECT f.name, i.grams FROM usual_meal_items i JOIN foods f ON f.id = i.food_id
                                                              WHERE i.meal_id = %s ORDER BY i.sort_order, i.id""", mid)]


def test_the_usual_tab_has_the_form_and_a_list_of_foods_to_pick_from(web):
    food("Test oats", 400, 13, 7, 60)
    page = web[0].get("/web/food/usual").text
    assert "New usual meal or drink" in page and 'action="/web/food/usualcreate"' in page and '<datalist id="foods-dl">' in page
    assert '<option value="Test oats">' in page and page.count('list="foods-dl"') == 8 and '<option value="drink">drink</option>' in page


def test_a_usual_meal_is_made_from_typed_foods(web, fake):
    food("Test oats", 400, 13, 7, 60)
    owner_sql("INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, aliases) VALUES ('Test skyr', 'g', 63, 11, 0.2, 4, 'label', '{quarkish}')")
    done = act(web, "/web/food/usualcreate", name=" morning  bowl ", kind="meal", food_0="Test oats", g_0="60", food_1="quarkish", g_1="150,5",
               food_2="", g_2="")
    assert 'Saved "morning bowl" as a usual meal (2 items).' in html.unescape(done.text)
    (m,) = meals()
    assert (m["name"], m["is_drink"]) == ("morning bowl", False) and items(m["id"]) == [("Test oats", 60.0), ("Test skyr", 150.5)]
    assert "morning bowl" in web[0].get("/web/food/usual").text and "+ morning bowl" in web[0].get("/web/").text


def test_a_usual_drink_is_marked_as_a_drink(web, fake):
    food("Test latte", 90, 5, drink=True)
    assert 'as a usual drink (1 item)' in act(web, "/web/food/usualcreate", name="my latte", kind="drink", food_0="test latte", g_0="250").text
    assert [(m["name"], m["is_drink"]) for m in meals()] == [("my latte", True)]


def test_bad_input_makes_no_usual_meal(web, fake):
    food("Test oats", 400, 13, 7, 60)
    owner_sql("INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, aliases) VALUES ('Twin a', 'g', 1, 1, 1, 1, 'label', '{twin}'), ('Twin b', 'g', 1, 1, 1, 1, 'label', '{twin}')")
    cases = ({"name": "x", "food_0": "Test oats", "g_0": "60"}, {"name": "ok name", "food_0": "Nothing like it", "g_0": "60"},
             {"name": "ok name", "food_0": "Test oats", "g_0": "0"}, {"name": "ok name", "food_0": "Test oats", "g_0": "6000"},
             {"name": "ok name", "food_0": "Test oats", "g_0": ""}, {"name": "ok name"}, {"name": "ok name", "food_0": "twin", "g_0": "50"})
    for form in cases:
        page = act(web, "/web/food/usualcreate", kind="meal", **form)
        assert "Nothing saved." in page.text, form
    assert meals() == []
    act(web, "/web/food/usualcreate", name="same", kind="meal", food_0="Test oats", g_0="60")
    assert "already exists" in act(web, "/web/food/usualcreate", name="SAME", kind="meal", food_0="Test oats", g_0="30").text
    assert len(meals()) == 1


def test_a_food_is_added_to_an_existing_usual_meal_by_name(web, fake):
    oats = food("Test oats", 400, 13, 7, 60)
    food("Test skyr", 63, 11, 0.2, 4)
    mid = owner_sql("INSERT INTO usual_meals (name) VALUES ('test bowl') RETURNING id")[0]["id"]
    owner_sql("INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order) VALUES (%s, %s, 60, 10)", mid, oats)
    page = web[0].get(f"/web/food/usual?um=U{mid}").text
    assert 'name="add_name_0" list="foods-dl"' in page and 'name="add_food"' not in page
    shown = act(web, "/web/food/usualmeal", id=f"U{mid}", rows="1", food_0=str(oats), g_0="60", name="test bowl", add_name="test SKYR", add_g="100", act="save")
    assert "please confirm" in shown.text and "Test skyr 100 added" in html.unescape(shown.text) and items(mid) == [("Test oats", 60.0)]
    wrong = act(web, "/web/food/usualmeal", id=f"U{mid}", rows="1", food_0=str(oats), g_0="60", name="test bowl", add_name="nope", add_g="100", act="save")
    assert "Pick the added food from the list" in wrong.text


def bowl(web):
    oats = food("Test oats", 400, 13, 7, 60)
    for name, kcal in (("Test skyr", 63), ("Test milk", 46), ("Test honey", 304)):
        food(name, kcal, 5, 1, 10)
    mid = owner_sql("INSERT INTO usual_meals (name) VALUES ('test bowl') RETURNING id")[0]["id"]
    owner_sql("INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order) VALUES (%s, %s, 60, 10)", mid, oats)
    return mid, dict(id=f"U{mid}", rows="1", food_0=str(oats), g_0="60", name="test bowl", act="save")


def test_several_foods_are_added_to_an_existing_usual_meal_at_once(web, fake):
    mid, base = bowl(web)
    page = web[0].get(f"/web/food/usual?um=U{mid}").text
    assert all(f'name="add_name_{k}" list="foods-dl"' in page and f'name="add_g_{k}"' in page for k in range(5)) and 'name="add_name_5"' not in page
    form = {**base, "add_name_0": "test SKYR", "add_g_0": "100", "add_name_1": "Test milk", "add_g_1": "200,5", "add_name_3": "Test honey", "add_g_3": "15"}
    shown = act(web, "/web/food/usualmeal", **form)
    text = html.unescape(shown.text)
    assert "please confirm" in shown.text and all(x in text for x in ("Test skyr 100 added", "Test milk 200.5 added", "Test honey 15 added"))
    assert items(mid) == [("Test oats", 60.0)]  # nothing is saved before the Yes
    done = act(web, "/web/food/usualmeal", **form, confirmed="1")
    assert "Changed the usual meal" in done.text
    assert items(mid) == [("Test oats", 60.0), ("Test skyr", 100.0), ("Test milk", 200.5), ("Test honey", 15.0)]


def test_added_rows_are_checked_and_empty_rows_are_ignored(web, fake):
    mid, base = bowl(web)
    for extra, why in (({"add_name_1": "nope", "add_g_1": "10"}, "Pick the added food from the list"),
                       ({"add_name_2": "Test skyr"}, "Give the added food an amount"), ({"add_name_2": "Test skyr", "add_g_2": "0"}, "Give the added food an amount"),
                       ({"add_name_0": "Test milk", "add_g_0": "9000"}, "at most 5000"), ({"add_g_4": "30"}, "Give the amount in added row 5 a food")):
        assert why in act(web, "/web/food/usualmeal", **{**base, **extra}).text
    assert items(mid) == [("Test oats", 60.0)]
    blank = {**base, **{f"add_name_{k}": "" for k in range(5)}, **{f"add_g_{k}": "" for k in range(5)}}
    assert "Nothing changed." in act(web, "/web/food/usualmeal", **blank).text
    one = act(web, "/web/food/usualmeal", **{**blank, "add_name_4": "Test milk", "add_g_4": "120"})  # a single food in the last row still works
    assert "Test milk 120 added" in html.unescape(one.text)
