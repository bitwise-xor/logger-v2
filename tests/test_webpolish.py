"""Small web app improvements of 29.10.2026: a message that was not saved comes back in the box, the entry box returns to
its own page, Undo under a reply, a friendly unknown-page, page titles and a skip link, the catalogue's orders, links from
Today's food rows, the Gratitude date picker and the limit lines on the Health charts."""
from datetime import timedelta
import re

from app import logbook
from app.ai.provider import ProviderError
from conftest import owner_sql
from test_log import fake, off, skyr  # noqa: F401 - fixtures reused
from test_web import ORIGIN, act, client, n, web  # noqa: F401
from test_webfood import eat_on, oats  # noqa: F401
from test_webhealth import charts
from test_webtoday import day, food, no_targets, raw  # noqa: F401


def send(web, text, back, key):
    c, csrf = web
    return c.post("/web/log", data={"csrf": csrf, "text": text, "key": key, "back": back}, headers=ORIGIN)


# ---- the entry box ---------------------------------------------------------------------------------

def test_the_entry_box_learns_which_page_it_is_on(web):
    page = web[0].get("/web/food").text
    assert re.search(r'<form class="entry".*?name="back" value="/web/" data-here>', page, re.S)


def test_a_message_that_was_not_saved_comes_back_in_the_box(web, skyr):
    class Down:
        def json(self, *a, **k):
            raise ProviderError("down")
    logbook.PROVIDER = Down()
    try:
        page = act(web, "/web/log", text="150 g skyr, weight 82.4 <b>", key="p1", back="/web/")
    finally:
        logbook.PROVIDER = None
    assert "Nothing saved." in page.text and "back in the box" in page.text
    assert re.search(r'<textarea id="entry-text"[^>]* autofocus [^>]*>150 g skyr, weight 82.4 &lt;b&gt;</textarea>', page.text)
    assert web[0].get("/web/").text.count("autofocus") == 0  # the text is shown once, not on the next page


def test_logging_returns_to_the_page_it_came_from(web, fake, skyr):
    c, _ = web
    r = send(web, "150 g skyr, weight 82.4", "/web/food/catalogue?q=oat+milk&s=kcal", "p2")
    assert r.status_code == 303 and r.headers["location"] == "/web/food/catalogue?q=oat+milk&s=kcal"
    assert "Saved." in c.get(r.headers["location"]).text


def test_any_other_back_address_means_today(web, fake):
    for bad in ("https://evil.example/", "//evil.example/web/", "/web/../etc", "/elsewhere", "javascript:alert(1)", "", "/web/login"):
        r = send(web, " ", bad, "p3")  # an empty message is refused, so nothing else decides where the browser goes
        assert r.status_code == 303 and r.headers["location"] == "/web/", bad


def test_a_message_with_a_question_goes_to_today_where_the_question_is(web, fake):
    r = send(web, "30 g zzqfood", "/web/food", "p4")
    assert r.status_code == 303 and r.headers["location"] == "/web/"
    assert "Waiting for your answer" in web[0].get("/web/").text


# ---- undo ------------------------------------------------------------------------------------------

def raw_id(page):
    return re.search(r'action="/web/undo".*?name="raw_id" value="(\d+)"', page.text, re.S)[1]


def test_undo_under_a_reply_removes_that_message(web, fake, skyr):
    page = act(web, "/web/log", text="150 g skyr, weight 82.4", key="u1", back="/web/")
    assert n("SELECT count(*) AS n FROM food_entries") == 1 and n("SELECT count(*) AS n FROM measurements") == 1
    done = act(web, "/web/undo", raw_id=raw_id(page), back="/web/")
    assert "Undone." in done.text
    assert n("SELECT count(*) AS n FROM food_entries") == 0 and n("SELECT count(*) AS n FROM measurements") == 0
    again = act(web, "/web/undo", raw_id=raw_id(page), back="/web/")  # a second press finds nothing to undo
    assert "Nothing undone." in again.text and "Undone." not in again.text


def test_undo_refuses_when_something_newer_was_logged(web, fake, skyr):
    first = act(web, "/web/log", text="150 g skyr, weight 82.4", key="u2", back="/web/")
    act(web, "/web/log", text="idea: a weekly food chart", key="u3", back="/web/")
    page = act(web, "/web/undo", raw_id=raw_id(first), back="/web/")
    assert "Nothing undone." in page.text and "Something newer was logged" in page.text
    assert n("SELECT count(*) AS n FROM food_entries") == 1 and n("SELECT count(*) AS n FROM measurements") == 1
    assert n("SELECT count(*) AS n FROM thoughts") == 1


def test_undo_wants_a_number_and_a_session(web, fake):
    c, csrf = web
    unknown = act(web, "/web/undo", raw_id="x", back="/web/").text
    assert "Nothing undone." in unknown and "Unknown message." in unknown
    assert c.post("/web/undo", data={"raw_id": "1"}, headers=ORIGIN).status_code == 403
    assert client().post("/web/undo", data={"raw_id": "1", "csrf": csrf}, headers=ORIGIN).status_code == 303


def test_a_repeated_form_shows_no_undo(web, fake, skyr):
    act(web, "/web/log", text="150 g skyr, weight 82.4", key="u4", back="/web/")
    again = act(web, "/web/log", text="150 g skyr, weight 82.4", key="u4", back="/web/")
    assert "Already saved earlier" in again.text and "/web/undo" not in again.text


# ---- pages -----------------------------------------------------------------------------------------

def test_an_unknown_page_says_so_inside_the_app(web):
    c, _ = web
    r = c.get("/web/nothing-here")
    assert r.status_code == 404 and "Page not found" in r.text and 'href="/web/"' in r.text and 'id="entry-text"' in r.text
    assert client().get("/web/nothing-here").status_code == 404  # signed out: a plain 404, no hint which pages exist
    assert client("log.example.test").get("/web/nothing-here").status_code == 404
    assert c.get("/web/static/app.css").status_code == 200  # the files are still served


def test_pages_have_a_title_a_skip_link_and_a_labelled_menu(web):
    c, _ = web
    page = c.get("/web/food").text
    assert "<title>Food · Logger</title>" in page and 'class="skip" href="#main"' in page and '<main id="main">' in page
    assert '<nav class="top" aria-label="Main">' in page and '<nav class="tabs" aria-label="Main menu">' in page
    assert "<title>Health · Logger</title>" in c.get("/web/health").text


# ---- Today, the catalogue, Gratitude, Health -------------------------------------------------------

def test_today_opens_each_food_in_place_and_an_earlier_day_links_to_the_food_log(web):  # in place since 31.10.2026
    d = day()
    eid = eat_on(d, "08:10", oats(), 60, 240)
    page = web[0].get("/web/")
    assert f'name="id" value="f{eid}"' in page.text and 'action="/web/food/entry"' in page.text
    owner_sql("UPDATE food_entries SET day = day - 1")
    page = web[0].get(f"/web/?day={d - timedelta(days=1)}")
    assert f'href="/web/food?day={(d - timedelta(days=1)).isoformat()}&amp;edit=f{eid}#f{eid}"' in page.text
    d = d - timedelta(days=1)
    assert 'action="/web/food/entry"' in web[0].get(f"/web/food?day={d.isoformat()}&edit=f{eid}").text


def test_the_catalogue_sorts_by_eaten_added_calories_protein_and_name(web):
    a, b, c_ = food("Aaa test", 100, 30, 1, 1), food("Bbb test", 500, 5, 30, 1), food("Ccc test", 300, 10, 10, 1)
    d = day()
    eat_on(d, "08:00", c_, 10)
    eat_on(d, "09:00", c_, 10)
    eat_on(d, "10:00", b, 10)

    def order(s=None):
        page = web[0].get("/web/food/catalogue?q=test" + (f"&s={s}" if s else "")).text
        return [m[:3] for m in re.findall(r"(?:Aaa|Bbb|Ccc) test", page)]

    assert order() == ["Ccc", "Bbb", "Aaa"] == order("eaten") == order("recent")
    assert order("kcal") == ["Bbb", "Ccc", "Aaa"] and order("kcal_low") == ["Aaa", "Ccc", "Bbb"]
    assert order("protein") == ["Aaa", "Ccc", "Bbb"] and order("name") == ["Aaa", "Bbb", "Ccc"]
    assert order("added") == ["Ccc", "Bbb", "Aaa"] and order("last") == ["Bbb", "Ccc", "Aaa"]
    assert order("nonsense") == ["Ccc", "Bbb", "Aaa"]  # an unknown order is the default one
    page = web[0].get("/web/food/catalogue?q=test&s=kcal").text
    assert '<option value="kcal" selected>' in page and "Sorted by: most calories" in page and 's=kcal' in page
    assert 'class="autosubmit"' in page and "<th class=\"hide-sm\">Added</th>" in page


def test_gratitude_has_a_date_picker(web):
    page = web[0].get("/web/private?view=grat").text
    assert 'type="date" name="day"' in page and 'class="autosubmit"' in page and 'name="view" value="grat"' in page
    assert 'value="2026-01-05"' in web[0].get("/web/private?view=grat&day=2026-01-05").text
    assert 'name="mode" value="week"' in web[0].get("/web/private?view=grat&mode=week").text


def test_the_health_charts_carry_the_limit_lines(web):
    ch = charts(web[0].get("/web/health"))
    assert ch["c-bp"]["lines"] == [[140, "r", "140"], [130, "w", "130"], [90, "r", "90"], [80, "w", "80"]]
    assert ch["c-sugar"]["lines"] == [[126, "r", "126"], [100, "w", "100"]]
    assert ch["c-sleep"]["lines"] == [[7, "w", "7 h"], [6, "r", "6 h"]]
    assert "lines" not in ch["c-weight"]
    assert "amber from 130/80" in web[0].get("/web/health").text


def test_the_script_and_style_carry_the_new_behaviour(web):
    js = web[0].get("/web/static/app.js").text
    for want in ("data-here", "Saving…", "showTip", "select.autosubmit", "o.lines"):
        assert want in js
    css = web[0].get("/web/static/app.css").text
    for want in (".skip", ".chart .ref", ".chart .tip", "pointer:coarse"):
        assert want in css
