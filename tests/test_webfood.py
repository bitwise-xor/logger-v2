"""The Food page: the food log (one day with entries to change, a date range with day totals) and the catalogue (values,
words, barcode, usual meals). Changes are shown first and made on Yes through the logger's own change function; notes,
words and barcodes are saved at once; copying a meal makes no AI call."""
import html
from datetime import datetime, timedelta

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, client, n, web  # noqa: F401
from test_webtoday import day, food, no_targets, raw  # noqa: F401 - no_targets: clean targets and usual meals
from app.when import BERLIN


def eat_on(d, hm, fid, amount, kcal=100):
    ts = datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), BERLIN)
    return owner_sql("""INSERT INTO food_entries (raw_id, food_id, amount, eaten_at, time_known, day, day_rule, extractor_version,
                        kcal, protein, fat, carbs, source) VALUES (%s, %s, %s, %s, true, %s, 'wake', 'test', %s, 10, 2, 5, 'label')
                        RETURNING id""", raw(), fid, amount, ts, d, kcal)[0]["id"]


def entry(eid):
    rows = owner_sql("""SELECT f.name, e.amount, e.note, e.day, e.meal, to_char(e.eaten_at AT TIME ZONE 'Europe/Berlin', 'HH24:MI') AS t
                        FROM food_entries e JOIN foods f ON f.id = e.food_id WHERE e.id = %s""", eid)
    return rows[0] if rows else None


def oats():
    return food("Test oats", 400, 13, 7, 60)


def test_the_food_pages_need_sign_in_and_the_app_host():
    assert client().get("/web/food").headers["location"] == "/web/login"
    assert client().get("/web/food/catalogue").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/food").status_code == 404


def test_one_day_with_links_to_change_each_food(web):
    d = day()
    eid = eat_on(d, "08:10", oats(), 60, 240)
    page = web[0].get("/web/food")
    assert 'href="/web/food" aria-current="page">Food' in page.text
    assert "Test oats" in page.text and f"edit=f{eid}" in page.text and f"note=f{eid}" in page.text
    assert "Log again" in page.text and "Save as usual meal" in page.text
    page = web[0].get(f"/web/food?day={d.isoformat()}&edit=f{eid}")
    assert 'action="/web/food/entry"' in page.text and 'value="60"' in page.text and 'value="08:10"' in page.text
    yesterday = web[0].get(f"/web/food?day={(d - timedelta(days=1)).isoformat()}")
    assert "Nothing logged on this day." in yesterday.text and "Copy to today" not in yesterday.text
    assert "Nothing logged" not in web[0].get("/web/food?day=garbage").text  # an unknown day is today
    assert "% of target" not in page.text  # no targets set


def test_one_day_shows_target_percent_and_left_or_over_in_colour(web):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 100, 70, 200)")
    d = day()
    eat_on(d, "08:00", oats(), 100, 2100)  # protein 10, fat 2, carbs 5 (eat_on)
    page = web[0].get("/web/food").text
    assert "% of target" in page and "Left (+) / over (−)" in page
    assert '<td class="warn">105%</td>' in page and '<td class="warn">−100</td>' in page  # kcal: 5% over
    assert '<td class="">10%</td>' in page and '<td class="">+90</td>' in page  # protein: far from reached
    assert '<td class="good">3%</td>' in page and '<td class="good">+68</td>' in page  # fat: within
    owner_sql("UPDATE food_entries SET kcal = 2300, protein = 100")
    page = web[0].get("/web/food").text
    assert '<td class="bad">115%</td>' in page and '<td class="good">100%</td>' in page


def test_changing_an_entry_shows_it_first_then_changes_on_yes(web, fake):
    d = day()
    eid = eat_on(d, "08:10", oats(), 60, 240)
    form = dict(id=f"f{eid}", food=str(owner_sql("SELECT food_id FROM food_entries")[0]["food_id"]), amount="80", time="08:10",
                act="save", back=f"/web/food?day={d.isoformat()}")
    page = act(web, "/web/food/entry", **form)
    assert "please confirm" in page.text and "Yes, change" in page.text and "80" in page.text
    assert float(entry(eid)["amount"]) == 60
    page = act(web, "/web/food/entry", **form, confirmed="1")
    assert "Changed Test oats." in page.text and float(entry(eid)["amount"]) == 80 and fake.calls == []


def test_another_food_and_another_time(web, fake):
    d = day()
    eid = eat_on(d, "08:10", oats(), 60)
    skyr = food("Test skyr", 63, 11, 0.2, 4)
    form = dict(id=f"f{eid}", food=str(skyr), amount="60", time="7:15", act="save")
    act(web, "/web/food/entry", **form)
    act(web, "/web/food/entry", **form, confirmed="1")
    e = entry(eid)
    assert (e["name"], e["t"]) == ("Test skyr", "07:15")


def test_deleting_an_entry_asks_first(web, fake):
    eid = eat_on(day(), "08:10", oats(), 60)
    page = act(web, "/web/food/entry", id=f"f{eid}", act="delete", amount="60")
    assert "Yes, delete" in page.text and entry(eid)
    act(web, "/web/food/entry", id=f"f{eid}", act="delete", amount="60", confirmed="1")
    assert entry(eid) is None


def test_a_note_is_saved_at_once_and_shown(web, fake):
    d = day()
    eid = eat_on(d, "08:10", oats(), 60)
    page = act(web, "/web/food/note", id=f"f{eid}", note="  half   portion ", back=f"/web/food?day={d.isoformat()}")
    assert "Note saved." in page.text and entry(eid)["note"] == "half portion"
    assert '<span class="notel">half portion</span>' in page.text
    act(web, "/web/food/note", id=f"f{eid}", note="")
    assert not entry(eid)["note"]


def test_copying_a_meal_to_today_makes_no_ai_call(web, fake):
    d = day()
    y = d - timedelta(days=1)
    o, s = oats(), food("Test skyr", 63, 11, 0.2, 4)
    eat_on(y, "08:00", o, 60)
    eat_on(y, "08:05", s, 150)
    page = act(web, "/web/food/copy", day=y.isoformat(), meal="1", time="12:30", key="c1")
    assert fake.calls == [], page.text
    today = owner_sql("""SELECT f.name, e.amount, to_char(e.eaten_at AT TIME ZONE 'Europe/Berlin', 'HH24:MI') AS t FROM food_entries e
                         JOIN foods f ON f.id = e.food_id WHERE e.day = %s ORDER BY f.name""", d)
    assert [(r["name"], float(r["amount"]), r["t"]) for r in today] == [("Test oats", 60, "12:30"), ("Test skyr", 150, "12:30")]
    assert owner_sql("SELECT channel FROM raw_inbox WHERE channel <> 'test'") == [{"channel": "web"}]


def test_saving_a_meal_as_a_usual_meal(web, fake):
    d = day()
    eat_on(d, "08:00", oats(), 60)
    eat_on(d, "08:05", food("Test skyr", 63, 11, 0.2, 4), 150)
    page = act(web, "/web/food/usualnew", day=d.isoformat(), meal="1", name="test  breakfast", back="/web/food")
    assert 'Saved "test breakfast" as a usual meal (2 items).' in html.unescape(page.text)
    assert [(r["name"], float(r["grams"])) for r in owner_sql("""SELECT f.name, i.grams FROM usual_meal_items i JOIN foods f ON f.id = i.food_id
                                                                 ORDER BY i.sort_order""")] == [("Test oats", 60), ("Test skyr", 150)]
    again = act(web, "/web/food/usualnew", day=d.isoformat(), meal="1", name="Test Breakfast")
    assert "already exists" in again.text and n("SELECT count(*) AS n FROM usual_meals") == 1


def test_the_date_range_with_day_totals_and_average(web):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 100, 70, 200)")
    d = day()
    o = oats()
    eat_on(d - timedelta(days=2), "08:00", o, 100, 2500)
    eat_on(d - timedelta(days=1), "08:00", o, 100, 1500)
    eat_on(d, "08:00", o, 100, 900)
    c, _ = web
    page = c.get(f"/web/food?from={(d - timedelta(days=3)).isoformat()}&to={d.isoformat()}")
    assert "Average <span class=\"small\">2 full days</span>" in page.text and "<td>2,000</td>" in page.text  # today left out
    assert '<td class="over">2,500</td>' in page.text and "nothing logged" in page.text and "so far" in page.text
    assert "/ 2,000" in page.text
    swapped = c.get(f"/web/food?from={d.isoformat()}&to={(d - timedelta(days=3)).isoformat()}")
    assert "2 full days" in swapped.text
    opened = c.get(f"/web/food?from={(d - timedelta(days=3)).isoformat()}&to={d.isoformat()}&open={(d - timedelta(days=1)).isoformat()}")
    assert "Open this day to change it" in opened.text
    panel = opened.text.split('<tr class="panel">')[1].split("</tr></tbody></table></div>")[0]
    assert "Test oats" in panel and "edit=" not in panel  # the opened day is read only
    assert "Last month" in c.get("/web/food?range=7").text


def test_catalogue_list_search_and_filters(web):
    o, cola = oats(), food("Test cola", 42, 0, 0, 10.6, drink=True)
    eat_on(day(), "08:00", o, 60)
    c, _ = web
    page = c.get("/web/food/catalogue").text
    assert page.index("Test oats") < page.index("Test cola") and 'aria-current="page">Catalogue' in page
    assert "Test oats" not in c.get("/web/food/catalogue?q=cola").text
    assert "Test cola" in c.get(f"/web/food/catalogue?q=N{cola}").text
    drinks = c.get("/web/food/catalogue?f=drinks").text
    assert "Test cola" in drinks and "Test oats" not in drinks
    assert "No food matches." in c.get("/web/food/catalogue?f=review").text
    detail = c.get(f"/web/food/catalogue?food=N{o}").text
    assert 'action="/web/food/values"' in detail and "Also recalculate the 1 past entry" in detail


def test_changing_food_values_keeps_the_past_unless_ticked(web, fake):
    o = oats()
    eid = eat_on(day(), "08:00", o, 100, 400)
    kcal = lambda: float(owner_sql("SELECT kcal FROM food_entries WHERE id = %s", eid)[0]["kcal"])
    page = act(web, "/web/food/values", id=f"N{o}", kcal="380", protein="13")
    assert "please confirm" in page.text and "380" in page.text
    assert float(owner_sql("SELECT kcal FROM foods WHERE id = %s", o)[0]["kcal"]) == 400
    act(web, "/web/food/values", id=f"N{o}", kcal="380", protein="13", confirmed="1")
    assert float(owner_sql("SELECT kcal FROM foods WHERE id = %s", o)[0]["kcal"]) == 380 and kcal() == 400
    act(web, "/web/food/values", id=f"N{o}", kcal="360", past="yes", confirmed="1")
    assert kcal() == 360
    assert "Nothing changed" in act(web, "/web/food/values", id=f"N{o}", kcal="360").text


def test_words_barcode_and_looks_right(web, fake):
    o = oats()
    owner_sql("UPDATE foods SET needs_review = true WHERE id = %s", o)
    page = act(web, "/web/food/words", id=f"N{o}", act="add", word="  Porridge ", back=f"/web/food/catalogue?food=N{o}")
    assert '"porridge" now means' in html.unescape(page.text) and "porridge" in owner_sql("SELECT aliases FROM foods WHERE id = %s", o)[0]["aliases"]
    act(web, "/web/food/words", id=f"N{o}", act="remove", word="porridge")
    assert "porridge" not in (owner_sql("SELECT aliases FROM foods WHERE id = %s", o)[0]["aliases"] or [])
    assert "Barcode 4000540003109 saved" in act(web, "/web/food/barcode", id=f"N{o}", barcode="4000 5400 03109").text
    assert "8 to 14 digits" in act(web, "/web/food/barcode", id=f"N{o}", barcode="12").text
    assert owner_sql("SELECT barcode FROM foods WHERE id = %s", o)[0]["barcode"] == "4000540003109"
    act(web, "/web/food/reviewed", id=f"N{o}")
    assert owner_sql("SELECT needs_review FROM foods WHERE id = %s", o)[0]["needs_review"] is False


def test_a_usual_meal_changed_renamed_and_deleted(web, fake):
    o, s = oats(), food("Test skyr", 63, 11, 0.2, 4)
    uid = owner_sql("INSERT INTO usual_meals (name) VALUES ('usual breakfast') RETURNING id")[0]["id"]
    owner_sql("INSERT INTO usual_meal_items (meal_id, food_id, grams, sort_order) VALUES (%s, %s, 60, 10), (%s, %s, 200, 20)", uid, o, uid, s)
    assert "usual breakfast" not in web[0].get("/web/food/catalogue").text  # usual meals have their own tab
    listed = web[0].get("/web/food/usual")
    assert 'href="/web/food/usual" aria-current="page">Usual meals' in listed.text and "usual breakfast" in listed.text
    page = web[0].get(f"/web/food/usual?um=U{uid}")
    assert 'action="/web/food/usualmeal"' in page.text and 'name="g_1" min="0" step="any" value="200"' in page.text
    form = dict(id=f"U{uid}", rows="2", name="big breakfast", food_0=str(o), g_0="70", food_1=str(s), g_1="200", rm_1="1", act="save")
    page = act(web, "/web/food/usualmeal", **form)
    assert "please confirm" in page.text and "Test oats: 60 → 70" in page.text and "Test skyr removed" in page.text
    act(web, "/web/food/usualmeal", **form, confirmed="1")
    assert owner_sql("SELECT name FROM usual_meals")[0]["name"] == "big breakfast"
    assert [(r["food_id"], float(r["grams"])) for r in owner_sql("SELECT food_id, grams FROM usual_meal_items")] == [(o, 70)]
    assert "Yes, delete" in act(web, "/web/food/usualmeal", id=f"U{uid}", act="delete").text
    act(web, "/web/food/usualmeal", id=f"U{uid}", act="delete", confirmed="1")
    assert n("SELECT count(*) AS n FROM usual_meals") == 0


def test_bad_input_changes_nothing(web, fake):
    d = day()
    o = oats()
    eid = eat_on(d, "08:10", o, 60)
    assert "Unknown entry" in act(web, "/web/food/entry", id="f1; drop", amount="1").text
    assert "not there any more" in act(web, "/web/food/entry", id="f999999", amount="1").text
    assert "above 0" in act(web, "/web/food/entry", id=f"f{eid}", food=str(o), amount="lots", act="save").text
    assert "hh:mm" in act(web, "/web/food/entry", id=f"f{eid}", food=str(o), amount="60", time="25:61", act="save").text
    assert "Nothing changed" in act(web, "/web/food/entry", id=f"f{eid}", food=str(o), amount="60", time="08:10", act="save").text
    assert "Unknown food" in act(web, "/web/food/values", id="N0", kcal="1").text
    assert "a number of 0 or more" in act(web, "/web/food/values", id=f"N{o}", kcal="-5").text
    assert "Unknown day" in act(web, "/web/food/copy", day="1999-01-01", meal="1").text
    assert "Give the usual meal a name" in act(web, "/web/food/usualnew", day=d.isoformat(), meal="1", name="x").text
    assert "Type the food's name" in html.unescape(act(web, "/web/food/lookup", name=" ").text)
    r = web[0].post("/web/food/note", data={"csrf": web[1], "id": f"f{eid}", "note": "x", "back": "https://evil.example/"},
                    headers={"Origin": "https://app.example.test"})
    assert r.headers["location"] == "/web/food"  # back only to the Food pages
    assert float(entry(eid)["amount"]) == 60 and n("SELECT count(*) AS n FROM usual_meals") == 0
