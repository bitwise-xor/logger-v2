"""One-tap Again chips on Today (08.11.2026): "+ Food 60 g" logs the food at the amount used last and the time now; the pencil
next to it opens the amount and time first, as the chip did before; the same food again within 30 minutes asks first."""
import re

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, web  # noqa: F401
from test_webfood import eat_on, oats
from test_webtoday import day


def n(sql: str) -> int:
    return owner_sql(sql)[0]["n"]


def chip(page: str, oid: int) -> str:
    """The one-tap form of a food's Again chip."""
    return re.search(rf'<form class="uq aq[^"]*" method="post" action="/web/food/again">(?:(?!</form>).)*?name="food" value="{oid}".*?</form>',
                     page, re.S)[0]


def test_one_tap_logs_the_last_amount_now(web, fake):
    oid = oats()
    eat_on(day(), "08:10", oid, 60)
    page = web[0].get("/web/").text
    tap = chip(page, oid)
    assert 'name="amount" value="60"' in tap and 'name="time"' not in tap and '+ Test oats <span class="small num">60 g</span></button>' in tap
    done = act(web, "/web/food/again", food=str(oid), amount="60", key="aq1")
    assert "Saved." in done.text and fake.calls == [] and 'action="/web/undo"' in done.text  # a wrong tap is one Undo away
    assert n("SELECT count(*) AS n FROM food_entries WHERE amount = 60 AND abs(extract(epoch FROM eaten_at - now())) < 300") == 1
    again = act(web, "/web/food/again", food=str(oid), amount="60", key="aq1")  # a double tap: the same key saves once
    assert n("SELECT count(*) AS n FROM food_entries") == 2 and "Already saved" in again.text


def test_the_same_food_again_within_30_minutes_asks_first(web, fake):
    oid = oats()
    eat_on(day(), "08:10", oid, 60)
    act(web, "/web/food/again", food=str(oid), amount="60", key="aq2")
    second = act(web, "/web/food/again", food=str(oid), amount="60", key="aq3")
    assert "Nothing saved yet" in second.text and n("SELECT count(*) AS n FROM food_entries") == 2
    assert n("SELECT count(*) AS n FROM pending WHERE tool = 'log_repeat'") == 1


def test_the_pencil_opens_the_amount_and_time_first(web, fake):
    oid = oats()
    eat_on(day(), "08:10", oid, 60)
    page = web[0].get("/web/").text
    edit = page[page.index('<details class="usual uq-edit again'):]
    edit = edit[:edit.index("</details>")]
    assert "Change Test oats" in edit and f'id="ag-a-{oid}"' in edit and 'value="60"' in edit and f'id="ag-t-{oid}"' in edit
    act(web, "/web/food/again", food=str(oid), amount="45", time="09:30", key="aq4")
    assert sorted(float(r["amount"]) for r in owner_sql("SELECT amount FROM food_entries")) == [45.0, 60.0]
