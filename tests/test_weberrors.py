"""Web item 26 (08.11.2026): errors in the app's layout. Signed in, the full frame; signed out, the bare frame without menus or
data. The status codes stay as they were, and no detail of the error ever reaches the page."""
import logging

from starlette.requests import Request
from starlette.testclient import TestClient

from app import todaypage
from app import web as webapp
from app.mcp_app import build_app
from test_web import ORIGIN, client, sign_in, web  # noqa: F401 - fixture reused
from test_auth import set_owner

HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}  # what a browser sends when it opens a page
SECRET = "detail-that-must-not-show"


def lenient(host="app.example.test"):
    """A client that gets the 500 page instead of the exception, as a browser would."""
    return TestClient(build_app(), base_url=f"https://{host}", follow_redirects=False, raise_server_exceptions=False)


def full_frame(text):
    return 'aria-label="Main"' in text and 'id="entry-text"' in text and "Sign out" in text


def bare_frame(text):
    return ('class="brand"' in text and "data-theme-toggle" in text and 'href="/web/"' in text
            and 'aria-label="Main"' not in text and 'id="entry-text"' not in text and "Sign out" not in text and "csrf" not in text)


def boom(*_a, **_k):
    raise RuntimeError(SECRET)


def no_detail(text):
    return SECRET not in text and "Traceback" not in text and "RuntimeError" not in text


# ---- 404 -------------------------------------------------------------------------------------------

def test_an_unknown_page_signed_in_is_in_the_full_frame(web):
    c, _ = web
    r = c.get("/web/nothing")
    assert r.status_code == 404 and "This page does not exist." in r.text and "Back to Today" in r.text and full_frame(r.text)
    assert r.headers["cache-control"] == "no-store" and "default-src 'none'" in r.headers["content-security-policy"]


def test_an_unknown_page_signed_out_is_in_the_bare_frame_the_same_for_every_path():
    c = client()
    r = c.get("/web/nothing")
    assert r.status_code == 404 and "This page does not exist." in r.text and bare_frame(r.text)
    assert c.get("/web/a/b/c").text == r.text  # nothing tells which pages exist
    assert "app.css" in r.text and "theme.js" in r.text  # light and dark, like every page


def test_the_status_codes_the_deploy_check_expects_stay():
    c = client()
    assert c.get("/web/nothing").status_code == 404
    r = c.get("/web/")
    assert r.status_code == 302 and r.headers["location"] == "/web/login"
    other = client("log.example.test").get("/web/login")
    assert other.status_code == 404 and other.text == "Not found"  # the other host: no app frame, as before
    assert client("log.example.test").get("/web/nothing").text == "Not found"


# ---- 500 -------------------------------------------------------------------------------------------

def test_a_failing_page_signed_in_shows_the_error_in_the_full_frame(monkeypatch, caplog):
    set_owner()
    c = lenient()
    sign_in(c)
    monkeypatch.setattr(todaypage, "render", boom)
    with caplog.at_level(logging.ERROR, logger="logger.web"):
        r = c.get("/web/", headers=HTML)
    assert r.status_code == 500 and "Something went wrong. Nothing was lost; try again." in r.text and full_frame(r.text)
    assert no_detail(r.text) and "Back to Today" in r.text
    assert "a web page failed: RuntimeError" in caplog.text and SECRET not in caplog.text  # the kind goes to the log


def test_a_failing_page_signed_out_shows_the_error_in_the_bare_frame(monkeypatch):
    monkeypatch.setattr(webapp, "_login_page", boom)
    r = lenient().get("/web/login", headers=HTML)
    assert r.status_code == 500 and "Something went wrong" in r.text and bare_frame(r.text) and no_detail(r.text)


def test_when_the_database_is_the_error_the_bare_frame_still_shows(monkeypatch):
    set_owner()
    c = lenient()
    sign_in(c)
    monkeypatch.setattr(webapp, "current_session", boom)
    r = c.get("/web/food", headers=HTML)
    assert r.status_code == 500 and "Something went wrong" in r.text and bare_frame(r.text) and no_detail(r.text)


def test_the_full_frame_failing_falls_back_to_the_bare_one(monkeypatch):
    set_owner()
    c = lenient()
    sign_in(c)
    monkeypatch.setattr(todaypage, "render", boom)
    monkeypatch.setattr(webapp.webquestions, "count", boom)  # the menus cannot be made either
    r = c.get("/web/", headers=HTML)
    assert r.status_code == 500 and bare_frame(r.text) and no_detail(r.text)


def test_fetches_and_other_hosts_keep_the_plain_500(monkeypatch):
    set_owner()
    c = lenient()
    sign_in(c)
    monkeypatch.setattr(webapp, "connect", boom)  # foods.json reads the database directly
    monkeypatch.setattr(webapp, "current_session", lambda _r: {"csrf": "x"})
    r = c.get("/web/foods.json")  # the page's own fetch: no text/html
    assert r.status_code == 500 and r.text == "Internal Server Error"
    for host, path in (("log.example.test", "/web/"), ("app.example.test", "/mcp"), ("app.example.test", "/elsewhere")):
        req = Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "scheme": "https",
                       "headers": [(b"host", host.encode()), (b"accept", HTML["Accept"].encode())]})
        out = webapp.server_error(req, RuntimeError(SECRET))
        assert out.status_code == 500 and out.body == b"Internal Server Error"


# ---- 403 -------------------------------------------------------------------------------------------

def test_a_form_from_elsewhere_is_refused_with_the_error_page_and_no_data(web):
    c, csrf = web
    r = c.post("/web/logout", data={"csrf": csrf}, headers={"Origin": "https://elsewhere.example.test"})
    assert r.status_code == 403 and "nothing was saved" in r.text and bare_frame(r.text)
    r = c.post("/web/log", data={"csrf": csrf, "text": "x"}, headers={"Origin": "https://elsewhere.example.test"})
    assert r.status_code == 403 and bare_frame(r.text)
    r = client().post("/web/login", data={"password": "x", "code": "1"}, headers={"Origin": "https://elsewhere.example.test"})
    assert r.status_code == 403 and bare_frame(r.text)
    assert c.get("/web/", headers=ORIGIN).status_code == 200  # still signed in
