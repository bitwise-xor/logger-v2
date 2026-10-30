"""Round 3 (31.10.2026): the barcode scanner on Today (a catalogue food by its barcode, the zxing reader served by the app)
and the Insights page (statements from the owner's numbers once both groups have enough days)."""
from datetime import date, datetime, timedelta

from app import insightspage
from app.when import BERLIN
from conftest import owner_sql
from test_web import client, web  # noqa: F401
from test_webfood import eat_on, oats
from test_webtoday import day, no_targets  # noqa: F401


# ---- the scanner -----------------------------------------------------------------------------------

def test_today_has_the_scanner_and_the_reader_is_served(web):
    page = web[0].get("/web/").text
    assert "data-scan hidden" in page and 'id="scan-dlg"' in page and 'action="/web/food/lookup"' in page and 'action="/web/food/again"' in page
    assert "data-scan" not in web[0].get(f"/web/?day={day() - timedelta(days=1)}").text  # an earlier day: no scanner
    r = web[0].get("/web/static/zxing.min.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript") and "ZXing" in r.text[:400]


def test_a_barcode_finds_the_catalogue_food_with_its_last_amount(web):
    fid = oats()
    owner_sql("UPDATE foods SET barcode = '4006040123456' WHERE id = %s", fid)
    eat_on(day(), "08:10", fid, 65)
    j = web[0].get("/web/foods/barcode.json?code=4006040123456").json()
    assert j["food"] == {"id": fid, "name": "Test oats", "basis": "g", "amount": 65.0}
    owner_sql("UPDATE foods SET barcode = '0012345678905' WHERE id = %s", fid)  # stored as EAN-13, read as UPC-A
    assert web[0].get("/web/foods/barcode.json?code=012345678905").json()["food"]["id"] == fid
    assert web[0].get("/web/foods/barcode.json?code=99999999").json()["food"] is None
    assert web[0].get("/web/foods/barcode.json?code=12;drop").json()["food"] is None
    assert client().get("/web/foods/barcode.json?code=4006040123456").status_code == 404  # signed out


# ---- Insights --------------------------------------------------------------------------------------

def tz(d: date, hm: str) -> datetime:
    return datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), BERLIN)


def sample(n: int) -> dict:
    """n days: after an even day the last food is 1 h before bed (02:00), the night is 5 h, the next fasting sugar 115 and
    weight goes up 0.4; after an odd day the gap is 5 h (bed 23:00), the night 8 h, sugar 100, weight down 0.1.
    Even days have the higher carb share."""
    d0 = date(2026, 9, 1)
    sugar, weight, wake, bed, last, macros = {}, {d0: 80.0}, {}, {}, {}, {}
    w = 80.0
    for i in range(n):
        d, nxt = d0 + timedelta(days=i), d0 + timedelta(days=i + 1)
        even = i % 2 == 0
        if even:
            bed[nxt], last[d] = tz(nxt, "02:00"), tz(nxt, "01:00")
        else:
            bed[nxt], last[d] = tz(d, "23:00"), tz(d, "18:00")
        sugar[nxt] = 115.0 if even else 100.0
        wake[nxt] = tz(nxt, "07:00")
        w += 0.4 if even else -0.1
        weight[nxt] = round(w, 1)
        macros[d] = (100.0, 300.0 if even else 120.0, 60.0)
    return {"today": d0 + timedelta(days=n), "sugar": sugar, "weight": weight, "wake": wake, "bed": bed, "last": last, "macros": macros}


def test_late_food_and_carbs_are_compared_with_the_next_mornings_sugar():
    ins = {i["key"]: i for i in insightspage.insights(sample(12))}
    late = ins["late"]["pairs"]
    assert sum(1 for g, _ in late if g) == 6 and {v for g, v in late if g} == {115.0} and {v for g, v in late if not g} == {100.0}
    html = insightspage.card(ins["late"])
    assert "On the 6 days your last food was less than 3 hours before bed" in html and "<b>115 mg/dL</b>" in html and "+15 mg/dL" in html
    carbs = ins["carbs"]["pairs"]
    assert {v for g, v in carbs if g} == {115.0} and {v for g, v in carbs if not g} == {100.0}


def test_short_nights_are_compared_with_the_weight_change():
    ins = {i["key"]: i for i in insightspage.insights(sample(12))}
    html = insightspage.card(ins["sleep"])
    assert "nights with less than 6 hours of sleep" in html and 'class="ibar"' in html


def test_too_few_days_say_what_is_missing():
    ins = {i["key"]: i for i in insightspage.insights(sample(5))}
    html = insightspage.card(ins["late"])
    assert "Not enough days yet" in html and "each needs 4" in html


def test_the_page_renders_with_no_data_and_is_in_the_menu(web):
    page = web[0].get("/web/insights").text
    assert "<h1>Insights</h1>" in page and page.count("Not enough days yet") == 3 and 'aria-current="page">Insights' in page
    assert "not proof" in page
