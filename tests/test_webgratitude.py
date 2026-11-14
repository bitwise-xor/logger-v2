"""The gratitude list (step B, 14.11.2026): the page /web/gratitude and the Today card show the count and the dots, never the
words of a saved line; a line is saved through logbook.log without the AI (app.js: X-CSRF and a JSON answer without words;
without script: a plain form, a flash and back); Show reads today's lines; Edit changes only today's gratitude lines; the goal
is a setting; the card can be hidden; a second manifest for the home screen; sign-in opens the page it was asked for."""
import json
import re
from datetime import datetime, timedelta

import pytest

from app import cycle, gratitude, uiprefs
from app import web as webapp
from app.when import BERLIN
from conftest import owner_sql
from test_auth import PW, code_now, set_owner
from test_log import fake  # noqa: F401 - fixture reused
from test_privateline import wake
from test_web import ORIGIN, act, client, n, web  # noqa: F401 - web is a fixture
from test_webtoday import day

WORD = "tealantern"  # an invented word that must stay in the private store and the answers that were asked for it
ELSEWHERE = ("private.incoming", "raw_inbox", "audit_log", "idempotency_keys", "pending", "web_sessions", "change_log")


def jpost(web, path, csrf=None, headers=None, **data):
    """A fetch as app.js sends it: the token in the X-CSRF header, the fields as a form body."""
    c, token = web
    return c.post(path, data=data, headers={**ORIGIN, "X-CSRF": token if csrf is None else csrf, **(headers or {})})


def contents():
    return [r["content"] for r in owner_sql("SELECT content FROM private.entries ORDER BY id")]


def nowhere_else(*also: str) -> None:
    for t in ELSEWHERE:
        assert WORD not in str(owner_sql(f"SELECT * FROM {t}")), t  # t is one of the fixed names above
    for x in also:
        assert WORD not in x


def entry(label, content, at):
    return owner_sql("""INSERT INTO private.entries (channel, label, content, summary, thought_type, area, tags, event_at)
                        VALUES ('test', %s, %s, 'x', 'journal', 'personal', '{}', %s) RETURNING id""", label, content, at)[0]["id"]


# ---- saving a line (the script's way)

def test_a_line_is_saved_without_the_ai_and_the_answer_has_no_words(web, fake):
    r = jpost(web, "/web/gratitude/line", text=f"the {WORD} by the door", key="k1")
    j = r.json()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert j["ok"] and len(j["ids"]) == 1 and j["count"] == 1 and j["goal"] == 10 and re.fullmatch(r"\d\d:\d\d", j["at"])
    assert j["key"] and j["key"] != "k1" and WORD not in r.text
    assert contents() == [f"gratitude: the {WORD} by the door"] and fake.calls == []
    audit = owner_sql("SELECT tool, outcome, detail FROM audit_log")
    assert [(a["tool"], a["outcome"]) for a in audit] == [("log", "saved")] and audit[0]["detail"]["private_ids"] == j["ids"]
    nowhere_else()


def test_the_same_key_twice_saves_once(web, fake):
    first = jpost(web, "/web/gratitude/line", text=f"a {WORD}", key="k2").json()
    again = jpost(web, "/web/gratitude/line", text=f"a {WORD}", key="k2").json()
    assert again["ok"] and again["ids"] == first["ids"] and again["count"] == 1 and len(contents()) == 1
    changed = jpost(web, "/web/gratitude/line", text=f"another {WORD}", key="k2").json()  # changed after a failed try: a new key
    assert not changed["ok"] and changed["message"] == "Not saved, try again." and changed["key"] and len(contents()) == 1
    assert jpost(web, "/web/gratitude/line", text=f"another {WORD}", key=changed["key"]).json()["count"] == 2
    assert fake.calls == []
    nowhere_else()


def test_a_pasted_text_gives_one_entry_per_line(web, fake):
    j = jpost(web, "/web/gratitude/line", text=f"the {WORD}\r\n\r\n  warm   bread \ng: a quiet street", key="k3").json()
    assert j["ok"] and len(j["ids"]) == 3 and j["count"] == 3
    assert contents() == [f"gratitude: the {WORD}", "gratitude: warm bread", "gratitude: a quiet street"] and fake.calls == []


def test_empty_and_too_many_lines_are_refused(web, fake):
    for text, why in (("   ", "Type something first."), ("g:", "Type something first."),
                      ("\n".join(f"{WORD} {i}" for i in range(21)), "At most 20 lines at once.")):
        j = jpost(web, "/web/gratitude/line", text=text, key="k4").json()
        assert j == {"ok": False, "message": why}, text
    assert contents() == [] and fake.calls == []
    nowhere_else()


def test_the_script_needs_the_session_the_origin_and_the_token(web, fake):
    c, csrf = web
    assert jpost(web, "/web/gratitude/line", csrf="wrong", text=WORD, key="k5").status_code == 403
    assert c.post("/web/gratitude/line", data={"text": WORD, "key": "k5"}, headers={"X-CSRF": csrf}).status_code == 403  # no Origin
    assert c.post("/web/gratitude/line", data={"text": WORD, "key": "k5"},
                  headers={"X-CSRF": csrf, "Origin": "https://elsewhere.example.test"}).status_code == 403
    assert client().post("/web/gratitude/line", data={"text": WORD}, headers={**ORIGIN, "X-CSRF": csrf}).status_code == 403
    assert client("log.example.test").post("/web/gratitude/line", data={"text": WORD}, headers={**ORIGIN, "X-CSRF": csrf}).status_code == 404
    assert contents() == []
    nowhere_else()


# ---- without script

def test_the_plain_form_saves_with_a_flash_and_goes_back(web, fake):
    c, csrf = web
    r = c.post("/web/gratitude/line", data={"csrf": csrf, "key": "f1", "text": f"the {WORD}", "back": "/web/"}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == "/web/"
    page = c.get("/web/").text
    assert "Saved privately. 1 of 10 today." in page and WORD not in page
    r = c.post("/web/gratitude/line", data={"csrf": csrf, "key": "f2", "text": "second line", "back": "https://evil.example/"}, headers=ORIGIN)
    assert r.headers["location"] == "/web/gratitude"
    assert "Saved privately. 2 of 10 today." in c.get("/web/gratitude").text
    page = act(web, "/web/gratitude/line", key="f3", text=" ", back="/web/gratitude").text
    assert "Nothing saved." in page and "Type something first." in page
    assert len(contents()) == 2 and fake.calls == []
    nowhere_else(page)


def test_an_out_of_date_plain_form_keeps_no_words(web, fake):
    c, _ = web
    r = c.post("/web/gratitude/line", data={"csrf": "old", "key": "f4", "text": f"the {WORD}", "back": "/web/gratitude"}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == "/web/gratitude"
    nowhere_else()  # the flash is in web_sessions now: without the words
    page = c.get("/web/gratitude").text
    assert "That page was out of date." in page and WORD not in page and contents() == []


# ---- what the pages show

def test_the_page_shows_the_count_and_dots_never_the_words(web, fake):
    c, _ = web
    for i, k in enumerate(("p1", "p2", "p3")):
        jpost(web, "/web/gratitude/line", text=f"{WORD} {i}", key=k)
    page = c.get("/web/gratitude")
    text = page.text
    assert page.status_code == 200 and WORD not in text
    assert "<b data-grat-count>3</b> of <span data-grat-goal>10</span>" in text
    assert text.count('class="gdot on"') == 3 and text.count('class="gdot"') == 7
    assert text.count("Saved at ") == 3 and text.count('class="gempty"') == 6 and "data-grat-show" in text
    assert 'id="entry-text"' not in text and text.count("<textarea") == 1 and "autofocus" in text  # one text box
    assert '<link rel="manifest" href="/web/static/gratitude.webmanifest">' in text and "manifest.webmanifest" not in text
    assert '<meta name="apple-mobile-web-app-title" content="Gratitude">' in text and "<title>Gratitude · Logger</title>" in text
    assert f'href="/web/private?view=grat&amp;day={day().isoformat()}"' in text and "Older days on the Private page" in text
    assert 'style="' not in text and "<script>" not in text  # the CSP: no inline styles or scripts


def test_the_today_card_comes_right_after_the_check_in_without_words(web, fake):
    c, _ = web
    jpost(web, "/web/gratitude/line", text=WORD, key="t1")
    text = c.get("/web/").text
    aside = text.split("<aside>")[1]
    assert aside.startswith('<div class="card checkin">') or aside.startswith('<section class="card grat"')
    card = aside[aside.index('<section class="card grat"'):]
    assert '<h2 class="gt"><a href="/web/gratitude">Gratitude</a></h2>' in card and "<b data-grat-count>1</b>" in card
    assert 'name="back" value="/web/"' in card and WORD not in text and "Saved at" not in card
    assert text.count('id="entry-text"') == 1  # Today keeps its entry box
    yesterday = c.get(f"/web/?day={(day() - timedelta(days=1)).isoformat()}").text
    assert 'class="card grat"' not in yesterday


def test_the_card_can_be_hidden_in_settings(web):
    c, _ = web
    assert 'name="show_card_gratitude" value="1" checked' in c.get("/web/settings?tab=today").text
    uiprefs.save(["card:gratitude"], {})
    assert 'class="card grat"' not in c.get("/web/").text
    act(web, "/web/settings/today", show_card_gratitude="1")
    assert 'class="card grat"' in c.get("/web/").text


# ---- Show and Edit

def test_lines_json_is_for_the_signed_in_owner_and_never_cached(web, fake):
    jpost(web, "/web/gratitude/line", text=f"the {WORD}\nsecond", key="s1")
    out = client().get("/web/gratitude/lines.json")
    assert out.status_code == 403 and WORD not in out.text
    r = web[0].get("/web/gratitude/lines.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    lines = r.json()["lines"]
    assert [x["text"] for x in lines] == [f"the {WORD}", "second"] and all(re.fullmatch(r"\d\d:\d\d", x["at"]) for x in lines)
    assert client("log.example.test").get("/web/gratitude/lines.json").status_code == 404


def test_edit_changes_only_todays_gratitude_lines_and_audits_ids(web, fake):
    gid = jpost(web, "/web/gratitude/line", text="first try", key="e1").json()["ids"][0]
    r = jpost(web, "/web/gratitude/edit", id=f"P{gid}", text=f"  the   {WORD}  ")
    assert r.json()["ok"] and r.headers["cache-control"] == "no-store" and WORD not in r.text
    row = owner_sql("SELECT content, summary FROM private.entries WHERE id = %s", gid)[0]
    assert row == {"content": f"gratitude: the {WORD}", "summary": f"the {WORD}"}
    audit = owner_sql("SELECT outcome, detail FROM audit_log WHERE tool = 'web_change'")
    assert audit == [{"outcome": "changed", "detail": {"what": "private save", "ids": [f"P{gid}"]}}]
    private = entry("private", "private: not a list line", datetime.now(BERLIN))
    older = entry("gratitude", "gratitude: an older line", datetime.now(BERLIN) - timedelta(days=2))
    for ref, text, why in ((f"P{private}", "x", "Only today's gratitude lines can be changed here."),
                           (f"P{older}", "x", "Only today's gratitude lines can be changed here."),
                           (f"P{gid}", "   ", "A line cannot be empty. Delete it on the Private page."),
                           ("P99999", "x", "Only today's gratitude lines can be changed here."), ("abc", "x", "Unknown line.")):
        assert jpost(web, "/web/gratitude/edit", id=ref, text=text).json() == {"ok": False, "message": why}, ref
    assert owner_sql("SELECT content FROM private.entries WHERE id = %s", private)[0]["content"] == "private: not a list line"
    assert owner_sql("SELECT content FROM private.entries WHERE id = %s", older)[0]["content"] == "gratitude: an older line"
    c, csrf = web
    assert c.post("/web/gratitude/edit", data={"csrf": csrf, "id": f"P{gid}", "text": "y"}, headers=ORIGIN).status_code == 400
    assert jpost(web, "/web/gratitude/edit", csrf="wrong", id=f"P{gid}", text="y").status_code == 403
    assert fake.calls == []
    nowhere_else()


# ---- the day follows the wake-up

def test_the_count_and_edit_follow_log_day(web, monkeypatch):
    d0 = day() - timedelta(days=6)
    at = lambda days, h, m=0: datetime(d0.year, d0.month, d0.day, h, m, tzinfo=BERLIN) + timedelta(days=days)  # noqa: E731
    wake(at(0, 14))  # up at 14:00 on d0
    late = entry("gratitude", "gratitude: owl hour tea", at(1, 6, 30))  # 06:30 the next morning, before the next wake-up: d0's day
    wake(at(1, 9))
    entry("gratitude", "gratitude: morning walk", at(1, 10))  # after that wake-up: the next day
    with webapp.connect() as c, c.cursor() as cur:
        monkeypatch.setattr(cycle, "today", lambda cur: d0)
        assert gratitude.count(cur) == 1
        monkeypatch.setattr(cycle, "today", lambda cur: d0 + timedelta(days=1))
        assert gratitude.count(cur) == 1
    assert not gratitude.edit(f"P{late}", "changed")["ok"]  # not "today" (d0 + 1) for that line
    monkeypatch.setattr(cycle, "today", lambda cur: d0)
    assert gratitude.edit(f"P{late}", "changed")["ok"] and [x["text"] for x in gratitude.lines()] == ["changed"]


# ---- the goal

def test_the_goal_is_a_setting_from_1_to_50(web):
    c, _ = web
    assert 'name="goal" min="1" max="50"' in c.get("/web/settings?tab=today").text and "Gratitude lines a day" in c.get("/web/settings?tab=today").text
    for bad in ("0", "51", "x", "", "2.5"):
        page = act(web, "/web/settings/gratitude", goal=bad, back="/web/settings?tab=today").text
        assert "Gratitude lines a day: give a whole number from 1 to 50." in page, bad
    assert n("SELECT count(*) AS n FROM ui_settings WHERE key = 'gratitude'") == 0
    page = act(web, "/web/settings/gratitude", goal="12", back="/web/settings?tab=today").text
    assert "counts to 12 a day" in page and 'name="goal" min="1" max="50" step="1" inputmode="numeric" value="12"' in page
    assert owner_sql("SELECT value FROM ui_settings WHERE key = 'gratitude'")[0]["value"] == {"goal": 12}
    assert owner_sql("SELECT detail FROM audit_log WHERE tool = 'web_change'")[-1]["detail"] == {"what": "gratitude goal"}
    g = c.get("/web/gratitude").text
    assert "<b data-grat-count>0</b> of <span data-grat-goal>12</span>" in g and g.count('class="gdot"') == 12
    owner_sql("UPDATE ui_settings SET value = '{\"goal\": 500}' WHERE key = 'gratitude'")  # a stored value out of range: the default
    assert "of <span data-grat-goal>10</span>" in c.get("/web/gratitude").text


# ---- the home screen

def test_the_manifest_has_a_shortcut_and_the_page_its_own_manifest():
    c = client()
    main = c.get("/web/static/manifest.webmanifest")
    assert main.headers["content-type"].startswith("application/manifest+json")
    assert [s["url"] for s in json.loads(main.text)["shortcuts"]] == ["/web/gratitude"]
    g = c.get("/web/static/gratitude.webmanifest")
    assert g.status_code == 200 and g.headers["content-type"].startswith("application/manifest+json")
    m = json.loads(g.text)
    assert (m["id"], m["start_url"], m["scope"], m["name"], m["short_name"], m["display"]) == (
        "/web/gratitude", "/web/gratitude", "/web/", "Gratitude", "Gratitude", "standalone")
    assert [i["src"] for i in m["icons"]] == [i["src"] for i in json.loads(main.text)["icons"]]


# ---- sign-in opens the page it was asked for

@pytest.mark.parametrize("bad", ["//evil.example", "https://evil.example", "/web/../x", "/web/%2e%2e/x", "/web//evil.example",
                                 "/web/%2F%2Fevil.example", "/web/x\\y", "/web/Gratitude", "/web/gratitude?x=1", "/web/", "/web",
                                 "/WEB/gratitude", "javascript:alert(1)", "/web/gratitude/", "/web/a b", "", None, "/web/" + "a" * 120,
                                 "/web/gratitude#x", "\\\\evil.example", "/web/\u00e4"])
def test_next_accepts_only_plain_paths_of_the_app(bad):
    assert webapp.safe_next(bad) == ""


def test_next_accepts_the_apps_pages():
    for good in ("/web/gratitude", "/web/food/catalogue", "/web/home/zones", "/web/gym/history"):
        assert webapp.safe_next(good) == good


def test_a_signed_out_page_goes_to_sign_in_and_back():
    set_owner()
    c = client()
    assert c.get("/web/gratitude?day=2026-01-01").headers["location"] == "/web/login?next=/web/gratitude"  # the path only
    assert c.get("/web/").headers["location"] == "/web/login"
    form = c.get("/web/login?next=/web/gratitude").text
    assert '<input type="hidden" name="next" value="/web/gratitude">' in form
    assert 'name="next"' not in c.get("/web/login?next=//evil.example").text
    wrong = c.post("/web/login", data={"password": "nope", "code": "000000", "next": "/web/gratitude"}, headers=ORIGIN)
    assert wrong.status_code == 401 and 'name="next" value="/web/gratitude"' in wrong.text
    r = c.post("/web/login", data={"password": PW, "code": code_now(), "next": "/web/gratitude"}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == "/web/gratitude"
    assert c.get("/web/login?next=/web/gratitude").headers["location"] == "/web/gratitude"  # signed in already
    assert c.get("/web/login?next=//evil.example").headers["location"] == "/web/"


@pytest.mark.parametrize("bad", ["//evil.example", "https://evil.example", "/web/../x", "/web/%2F%2Fevil.example", "/web//x"])
def test_sign_in_never_sends_the_browser_elsewhere(bad):
    set_owner()
    r = client().post("/web/login", data={"password": PW, "code": code_now(), "next": bad}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == "/web/"
