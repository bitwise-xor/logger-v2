"""Changes of 02.11.2026 (the owner's answers in the artifact "Logger Next Changes"): 12 Again chips on the PC (8 on phones),
a food's words under its name in the catalogue and a Close button on its box, suggestions with the time last eaten,
the hours slept with Woke up on Today, and a 7-day average line on the Health page's Sleep chart."""
import html
import json
import re
from datetime import datetime, timedelta

from app import todaypage
from app.when import BERLIN
from conftest import owner_sql
from test_web import web  # noqa: F401 - fixture reused
from test_webfood import eat_on
from test_webhealth import event
from test_webtoday import day


def food(name, aliases=()):
    return owner_sql("INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, aliases) VALUES (%s, 'g', 100, 5, 2, 10, 'label', %s) RETURNING id",
                     name, list(aliases))[0]["id"]


def test_twelve_again_chips_and_the_last_four_hidden_on_phones(web):
    for i in range(14):
        eat_on(day(), f"07:{i:02d}", food(f"Food {i:02d}"), 50)  # Food 13 is the newest
    page = web[0].get("/web/").text
    chips = re.findall(r'<form class="uq aq( more)?"[^>]*>.*?<button[^>]*>\+ (Food \d\d)', page)  # one tap since 08.11.2026
    assert [c[1] for c in chips] == [f"Food {i:02d}" for i in range(13, 1, -1)]  # the 12 newest, newest first
    assert [bool(c[0]) for c in chips] == [False] * todaypage.AGAIN_PHONE + [True] * 4
    css = web[0].get("/web/static/app.css").text
    assert "@media (max-width:1080px){.again-row>.more{display:none}}" in css


def test_the_catalogue_shows_a_foods_words_and_its_box_closes(web):
    fid = food("Muesli, Basis (Aldi)", ["muesli", "basis muesli"])
    food("Plain apple")
    page = web[0].get("/web/food/catalogue").text
    assert '<span class="small words">muesli, basis muesli</span>' in page
    opened = web[0].get(f"/web/food/catalogue?food=N{fid}").text
    closes = re.findall(r'<a class="ghost sm" href="([^"]+)"[^>]*>Close</a>', opened)
    assert len(closes) == 2 and all(html.unescape(c) == f"/web/food/catalogue#N{fid}" for c in closes)


def test_the_suggestions_know_when_each_food_was_eaten_last(web):
    a, _b = food("Knuspermüsli"), food("Protein muesli")
    eid = eat_on(day(), "08:00", a, 50)
    eaten = owner_sql("SELECT eaten_at FROM food_entries WHERE id = %s", eid)[0]["eaten_at"]
    foods = {f["n"]: f for f in json.loads(web[0].get("/web/foods.json").text)}
    assert foods["Protein muesli"]["t"] == 0 and foods["Knuspermüsli"]["t"] == int(eaten.timestamp())


def test_today_shows_the_hours_slept_with_woke_up(web):
    event("bed", day() - timedelta(days=1), "23:00")
    event("wake", day(), "06:05")
    page = web[0].get("/web/").text
    row = re.search(r'name="row" value="wake".*?</form>', page, re.S)[0]
    assert "slept 7 h 05 min" in row


def test_the_sleep_chart_has_a_seven_day_average_line(web):
    d = day()
    for back, bed, up in ((4, "23:00", "05:00"), (3, "23:00", "07:00"), (2, "23:00", "06:00")):  # 6 h, 8 h, 7 h
        event("bed", d - timedelta(days=back), bed)
        event("wake", d - timedelta(days=back - 1), up)
    page = web[0].get("/web/health").text
    chart = json.loads(html.unescape(re.search(r'id="c-sleep" data-chart="([^"]+)"', page)[1]))
    line = next(s for s in chart["series"] if s["mark"] == "line")
    assert [p[1] for p in line["pts"]] == [6, 7, 7]  # the average of the nights so far in each 7-day window
    assert "7-day average to" in line["pts"][-1][2]
