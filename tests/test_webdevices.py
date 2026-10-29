"""The phone menu, the app icon and manifest, the devices list, the sign-in lockout per device, and a Todoist due date from
the Ideas page (29.10.2026)."""
import json
import re

import pytest

from app import auth, todos
from app import web as webapp
from conftest import owner_sql
from test_auth import code_now, set_owner
from test_web import ORIGIN, act, client, n, sign_in, web  # noqa: F401
from test_webideas import day, text, todo  # noqa: F401


@pytest.fixture
def fake_question():
    owner_sql("""INSERT INTO pending (id, tool, payload, reason, expires_at)
                 VALUES ('pq1', 'log_confirm', '{}'::jsonb, 'Save this?', now() + interval '1 day')""")


IPHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"}


# ---- menu, icon, manifest --------------------------------------------------------------------------

def test_wide_screens_get_every_link_and_phones_a_tab_bar_with_more(web):
    page = web[0].get("/web/health").text
    top = re.search(r'<nav class="top" aria-label="Main">(.*?)</nav>', page, re.S)[1]
    for label in ("Today", "Food", "Ideas and tasks", "Health", "Questions", "Private", "Usage", "Labs and medication", "Settings"):
        assert f">{label}" in top
    tabs = re.search(r'<nav class="tabs" aria-label="Main menu">(.*?)</nav>', page, re.S)[1]
    bar, panel = tabs.split('<details class="more">')
    assert [m for m in re.findall(r">(Today|Food|Health|Ideas)<", bar)] == ["Today", "Food", "Health", "Ideas"]
    for label in ("Questions", "Private", "Usage", "Labs and medication", "Settings"):
        assert f">{label}" in panel and f">{label}" not in bar
    assert "<summary>More</summary>" in panel and 'aria-current="page">Health' in bar


def test_more_is_marked_when_the_page_is_in_it_and_shows_the_open_questions(web, fake_question):
    page = web[0].get("/web/settings").text
    assert re.search(r'<summary aria-current="page">More<span class="badge">1', page)
    assert 'aria-current="page">Settings' in page


def test_the_app_has_an_icon_a_manifest_and_the_phone_tags(web):
    c, _ = web
    page = c.get("/web/").text
    for want in ('rel="manifest" href="/web/static/manifest.webmanifest"', 'rel="apple-touch-icon" href="/web/static/apple-touch-icon.png"',
                 'name="apple-mobile-web-app-capable" content="yes"', 'name="apple-mobile-web-app-title" content="Logger"', "viewport-fit=cover",
                 'rel="icon" href="/web/static/icon.svg"'):
        assert want in page, want
    assert "manifest-src 'self'" in client().get("/web/login").headers["content-security-policy"]
    m = client().get("/web/static/manifest.webmanifest")  # fetched without a sign-in, as browsers do
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    data = json.loads(m.text)
    assert data["display"] == "standalone" and data["scope"] == "/web/" and data["start_url"] == "/web/" and data["short_name"] == "Logger"
    assert {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        r = client().get(f"/web/static/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    svg = client().get("/web/static/icon.svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml") and b"<svg" in svg.content
    assert client("log.example.test").get("/web/static/manifest.webmanifest").status_code == 404


# ---- sign-in lockout per device, devices list ------------------------------------------------------

def test_signing_in_marks_the_device_and_keeps_what_the_browser_is():
    set_owner()
    c = client()
    r = c.post("/web/login", data={"password": "correct horse battery staple", "code": code_now()}, headers={**ORIGIN, **IPHONE})
    assert r.status_code == 303
    cookie = r.headers["set-cookie"] if isinstance(r.headers["set-cookie"], str) else ""
    token = c.cookies.get(webapp.DEVICE_COOKIE)
    assert token and len(token) >= 40
    assert [x["device_hash"] for x in owner_sql("SELECT device_hash FROM trusted_devices")] == [webapp._h(token)]
    row = owner_sql("SELECT user_agent, device_hash FROM web_sessions")[0]
    assert "iPhone" in row["user_agent"] and row["device_hash"] == webapp._h(token)


def test_a_strangers_failed_tries_do_not_lock_out_a_known_device(web):
    c, _ = web  # signed in once, so it carries the device cookie
    stranger = client()
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(stranger, password="nope nope nope nope").status_code == 401
    assert sign_in(stranger).status_code == 429  # the stranger is locked out, even with the right password
    assert sign_in(c, code=code_now(1)).status_code == 303  # the owner's own device still gets in
    assert n("SELECT count(*) AS n FROM login_failures WHERE device_hash IS NULL") == auth.MAX_FAILURES


def test_a_known_device_counts_its_own_failures_and_does_not_lock_a_stranger(web):
    c, _ = web
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(c, password="nope nope nope nope").status_code == 401
    assert sign_in(c, code=code_now(1)).status_code == 429
    assert sign_in(client(), code=code_now(1)).status_code == 303  # a new device is not affected
    assert n("SELECT count(*) AS n FROM login_failures WHERE device_hash IS NOT NULL") == auth.MAX_FAILURES


def test_the_devices_list_shows_each_device_and_signs_others_out(web):
    c, csrf = web
    other = client()
    other.post("/web/login", data={"password": "correct horse battery staple", "code": code_now(1)}, headers={**ORIGIN, **IPHONE})
    page = c.get("/web/settings").text
    assert "Signed-in devices" in page and "iPhone, Safari" in page and "this device" in page and "Sign out all 1 other device" in page
    sid = re.search(r'name="sid" value="([0-9a-f-]{36})"', page)[1]
    assert "Signed that device out." in act(web, "/web/settings/device", sid=sid).text
    assert other.get("/web/").headers["location"] == "/web/login"
    assert "already signed out" in act(web, "/web/settings/device", sid=sid).text
    own = owner_sql("SELECT sid FROM web_sessions")[0]["sid"]
    assert "use Sign out at the top" in act(web, "/web/settings/device", sid=str(own)).text
    assert "Unknown device." in act(web, "/web/settings/device", sid="x").text


def test_sign_out_all_the_others_keeps_this_device(web):
    c, _ = web
    assert client().post("/web/login", data={"password": "correct horse battery staple", "code": code_now(1)}, headers=ORIGIN).status_code == 303
    assert n("SELECT count(*) AS n FROM web_sessions") == 2
    assert "Signed out 1 other device." in act(web, "/web/settings/others").text
    assert n("SELECT count(*) AS n FROM web_sessions") == 1 and c.get("/web/").status_code == 200
    assert "No other device was signed in." in act(web, "/web/settings/others").text


# ---- Todoist due date ------------------------------------------------------------------------------

def due_of(todo, tid):
    return (todo.tasks[tid].get("due") or {}).get("date")


def test_the_edit_form_has_a_due_date_and_the_change_waits_for_a_yes(web, todo):
    page = web[0].get("/web/ideas/todoist?edit=k1").text
    assert 'type="date" name="due"' in page and 'name="no_due"' in page
    assert 'type="date" name="due"' in web[0].get("/web/ideas/todoist?edit=k2").text and 'name="no_due"' not in web[0].get("/web/ideas/todoist?edit=k2").text
    form = dict(task="k2", act="edit", content="Descale the kettle", place="s:l2", due="2026-11-05", back="/web/ideas/todoist")
    shown = act(web, "/web/ideas/task", **form)
    assert "please confirm" in shown.text and "Due date: none → 05.11.2026" in text(shown)
    assert due_of(todo, "k2") is None
    done = act(web, "/web/ideas/task", **form, confirmed="1")
    assert "Changed in Todoist: Due date: none → 05.11.2026" in text(done)
    assert due_of(todo, "k2") == "2026-11-05" and todo.log[-1] == ("due", "k2", "2026-11-05")


def test_a_due_date_can_be_moved_or_removed(web, todo):
    old = due_of(todo, "k3")
    form = dict(task="k3", act="edit", content="Look into monitors", place=f"p:{todos.INBOX}", due="2026-12-24", confirmed="1")
    act(web, "/web/ideas/task", **form)
    assert due_of(todo, "k3") == "2026-12-24" and old != "2026-12-24"
    act(web, "/web/ideas/task", **{**form, "no_due": "1"})
    assert due_of(todo, "k3") is None and todo.log[-1] == ("due", "k3", None)
    assert "Nothing changed" in text(act(web, "/web/ideas/task", **{**form, "due": ""}))  # nothing left to change


def test_a_bad_date_or_a_missing_field_changes_no_date(web, todo):
    base = dict(task="k1", act="edit", content="Renew the passport", place="s:l1", confirmed="1")
    before = due_of(todo, "k1")
    assert "Give the due date as a date." in text(act(web, "/web/ideas/task", **base, due="soon"))
    assert "Nothing changed" in text(act(web, "/web/ideas/task", **base))  # an older form without the field
    assert due_of(todo, "k1") == before


def test_a_repeating_task_or_one_with_a_time_keeps_its_date(web, todo):
    todo.tasks["k2"]["due"] = {"date": "2026-11-01", "is_recurring": True, "string": "every week"}
    todo.tasks["k3"]["due"] = {"date": "2026-11-02T10:00:00"}
    page = web[0].get("/web/ideas/todoist?edit=k2").text
    assert "Repeats (every week); change its date in Todoist." in page and 'name="due"' not in page
    assert "Has a time of day; change its date in Todoist." in web[0].get("/web/ideas/todoist?edit=k3").text
    for tid, content in (("k2", "Descale the kettle"), ("k3", "Look into monitors")):
        same = act(web, "/web/ideas/task", task=tid, act="edit", content=content, place=ideaspage_place(todo, tid), due="2027-01-01", confirmed="1")
        assert "Nothing changed" in text(same)
    assert [x for x in todo.log if x[0] == "due"] == []


def ideaspage_place(todo, tid):
    from app import ideaspage
    return ideaspage.place_of(todo.tasks[tid])
