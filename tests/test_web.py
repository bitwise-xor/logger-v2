import pytest
from starlette.testclient import TestClient

from app import auth
from app import web as webapp
from app.mcp_app import build_app
from conftest import owner_sql
from test_auth import PW, code_now, set_owner

ORIGIN = {"Origin": "https://app.example.test"}


def client(host="app.example.test"):
    return TestClient(build_app(), base_url=f"https://{host}", follow_redirects=False)


def sign_in(c, password=PW, code=None, headers=ORIGIN):
    return c.post("/web/login", data={"password": password, "code": code or code_now()}, headers=headers)


@pytest.fixture
def web():
    """A signed-in client and the session's CSRF token."""
    set_owner()
    c = client()
    sign_in(c)
    return c, owner_sql("SELECT csrf FROM web_sessions")[0]["csrf"]


def act(web, path, **data):
    """A form post as the page sends it: the action runs, the browser is sent back, the page shows the reply."""
    c, csrf = web
    r = c.post(path, data={"csrf": csrf, **data}, headers=ORIGIN)
    assert r.status_code == 303, r.text
    return c.get(r.headers["location"])


def n(sql, *args):
    return owner_sql(sql, *args)[0]["n"]


def test_login_page_and_redirects_when_signed_out():
    c = client()
    assert c.get("/").headers["location"] == "/web/"
    assert c.get("/web/").headers["location"] == "/web/login"
    r = c.get("/web/login")
    assert r.status_code == 200 and "Authenticator code" in r.text and r.headers["cache-control"] == "no-store"


def test_pages_let_the_browser_send_the_origin_and_load_only_their_own_files():
    # With Referrer-Policy no-referrer, browsers send "Origin: null" on form posts and every sign-in is refused.
    r = client().get("/web/login")
    assert r.headers["referrer-policy"] == "same-origin"
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "default-src 'none'" in csp and "unsafe" not in csp
    assert '/web/static/app.css?v=' in r.text and '/web/static/app.js?v=' in r.text


def test_the_stylesheet_and_script_are_served_on_the_app_host_only():
    c = client()
    css, js = c.get("/web/static/app.css"), c.get("/web/static/app.js")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css") and ".hrow" in css.text
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript") and "entry-text" in js.text
    assert c.get("/web/static/../web.py").status_code == 404 and c.get("/web/static/other.js").status_code == 404
    assert client("log.example.test").get("/web/static/app.css").status_code == 404


def test_sign_in_sets_a_strict_cookie_and_stores_only_a_hash():
    set_owner()
    c = client()
    r = sign_in(c)
    assert r.status_code == 303 and r.headers["location"] == "/web/"
    cookie = r.headers["set-cookie"]
    for want in ("__Host-logger_session=", "Secure", "HttpOnly", "SameSite=strict", "Path=/", "Max-Age=2592000"):
        assert want.lower() in cookie.lower()
    token = c.cookies.get(webapp.COOKIE)
    rows = owner_sql("SELECT token_hash, expires_at - created_at AS span FROM web_sessions")
    assert len(rows) == 1 and rows[0]["token_hash"] == webapp._h(token) and token != rows[0]["token_hash"]
    assert rows[0]["span"].days == 30
    home = c.get("/web/")
    assert home.status_code == 200 and "Sign out" in home.text and "Food and drinks" in home.text


def test_wrong_password_or_code_is_refused_and_counted():
    set_owner()
    c = client()
    assert sign_in(c, password="wrong password here").status_code == 401
    assert sign_in(c, code="000000").status_code == 401
    assert n("SELECT count(*) AS n FROM login_failures") == 2
    assert n("SELECT count(*) AS n FROM web_sessions") == 0
    assert c.get("/web/").status_code == 302


def test_a_code_works_once():
    set_owner()
    code = code_now()
    assert sign_in(client(), code=code).status_code == 303
    assert sign_in(client(), code=code).status_code == 401


def test_lockout_after_too_many_failures():
    set_owner()
    c = client()
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(c, password="nope nope nope nope").status_code == 401
    assert sign_in(c).status_code == 429
    assert n("SELECT count(*) AS n FROM web_sessions") == 0


def test_no_owner_set_up_refuses():
    assert sign_in(client()).status_code == 401


def test_login_post_needs_the_app_origin():
    set_owner()
    c = client()
    assert sign_in(c, headers={}).status_code == 403
    assert sign_in(c, headers={"Origin": "null"}).status_code == 403
    assert sign_in(c, headers={"Origin": "https://evil.example"}).status_code == 403
    assert n("SELECT count(*) AS n FROM web_sessions") == 0


def test_expired_session_is_not_accepted():
    set_owner()
    c = client()
    sign_in(c)
    owner_sql("UPDATE web_sessions SET expires_at = now() - interval '1 minute'")
    assert c.get("/web/").headers["location"] == "/web/login"


def test_logout_needs_the_csrf_token_then_ends_the_session(web):
    c, csrf = web
    assert csrf in c.get("/web/").text
    assert c.post("/web/logout", data={"csrf": "wrong"}, headers=ORIGIN).status_code == 403
    assert c.post("/web/logout", data={"csrf": csrf}, headers={}).status_code == 403
    assert n("SELECT count(*) AS n FROM web_sessions") == 1
    r = c.post("/web/logout", data={"csrf": csrf}, headers=ORIGIN)
    assert r.status_code == 303 and r.headers["location"] == "/web/login"
    assert n("SELECT count(*) AS n FROM web_sessions") == 0


def test_other_hosts_and_cloudflare_requests_get_404():
    set_owner()
    for host in ("log.example.test", "today.example.test"):
        c = client(host)
        assert c.get("/web/login").status_code == 404
        assert sign_in(c).status_code == 404
    c = client()
    for h in ("CF-Connecting-IP", "CF-Ray", "CF-Visitor"):
        assert c.get("/web/login", headers={h: "x"}).status_code == 404
        assert sign_in(c, headers={**ORIGIN, h: "x"}).status_code == 404
    assert n("SELECT count(*) AS n FROM web_sessions") == 0


def test_a_session_cookie_does_not_open_the_connector(web):
    r = web[0].post("/mcp-testpath", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_every_action_needs_a_session_the_token_and_the_app_origin(web):
    c, csrf = web
    paths = ("/web/log", "/web/answer", "/web/lookup", "/web/habit", "/web/usual", "/web/health", "/web/review")
    for p in paths:
        assert c.post(p, data={"csrf": "wrong"}, headers=ORIGIN).status_code == 403, p
        assert c.post(p, data={"csrf": csrf}, headers={}).status_code == 403, p
        assert c.post(p, data={"csrf": csrf}, headers={"Origin": "https://evil.example"}).status_code == 403, p
        assert c.post(p, data={"csrf": csrf}, headers={**ORIGIN, "CF-Ray": "x"}).status_code == 404, p
        out = client().post(p, data={"csrf": csrf}, headers=ORIGIN)
        assert out.status_code == 303 and out.headers["location"] == "/web/login", p
    assert n("SELECT count(*) AS n FROM raw_inbox") == 0 and n("SELECT count(*) AS n FROM audit_log") == 0


def test_the_reply_shows_once(web):
    page = act(web, "/web/answer", question_id="p1", reply="")
    assert "Type an answer first." in page.text
    assert "Type an answer first." not in web[0].get("/web/").text
