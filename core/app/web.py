"""Web app: sign-in with the owner's password and authenticator code, then a cookie for 30 days per device.
Served only on the app host name (Caddy, home network and tailnet); never through the public tunnel.
Every form post does its work, keeps the reply in the session ("flash") and sends the browser back to the page with a
303, so reloading a page never repeats an action."""
import hashlib
import hmac
import html
import pathlib
import secrets
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import (auth, foodpage, healthpage, ideaspage, logbook, privatepage, todaypage, webactions, webentry, webfood, webideas,
               usagepage, webprivate, webquestions)
from .ai.provider import ProviderError
from .config import APP_HOST
from .db import connect

COOKIE = "__Host-logger_session"  # the __Host- prefix: Secure, this host only, no Domain
SESSION_DAYS = 30
# Referrer-Policy same-origin, not no-referrer: with no-referrer browsers send "Origin: null" on form posts,
# and _origin_ok needs the real origin. Other sites still get no referrer.
_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
                                       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}
_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
_STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8"}
_STATIC_V = hashlib.sha256(b"".join((_STATIC_DIR / n).read_bytes() for n in sorted(_STATIC))).hexdigest()[:10]


def is_app_request(request: Request) -> bool:
    """Only the app host name, and never a request that came through Cloudflare."""
    host = request.headers.get("host", "").split(":")[0].lower()
    via_cloudflare = any(h in request.headers for h in ("cf-ray", "cf-connecting-ip", "cf-visitor"))
    return host == APP_HOST and not via_cloudflare


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_session(request: Request):
    """The signed-in device's session row, or None."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with connect() as c:
        row = c.execute("SELECT * FROM web_sessions WHERE token_hash=%s AND expires_at > now()", (_h(token),)).fetchone()
        if row:
            c.execute("UPDATE web_sessions SET last_seen=now() WHERE token_hash=%s", (row["token_hash"],))
    return row


def _origin_ok(request: Request) -> bool:
    """A form post must come from this host's own pages."""
    return request.headers.get("origin") == f"https://{APP_HOST}"


def csrf_ok(session, form) -> bool:
    return bool(session) and hmac.compare_digest(str(form.get("csrf", "")), session["csrf"])


def _set_flash(session, resp: dict) -> None:
    with connect() as c:
        c.execute("UPDATE web_sessions SET flash = %s WHERE token_hash = %s", (Jsonb(resp), session["token_hash"]))


def _pop_flash(session):
    with connect() as c:
        row = c.execute("SELECT flash FROM web_sessions WHERE token_hash = %s FOR UPDATE", (session["token_hash"],)).fetchone()
        if row and row["flash"] is not None:
            c.execute("UPDATE web_sessions SET flash = NULL WHERE token_hash = %s", (session["token_hash"],))
    return row["flash"] if row else None


def page(title: str, body: str, status: int = 200, cls: str = "") -> HTMLResponse:
    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{html.escape(title)}</title><link rel="stylesheet" href="/web/static/app.css?v={_STATIC_V}">'
           f'<script src="/web/static/app.js?v={_STATIC_V}" defer></script></head><body class="{cls}">{body}</body></html>')
    return HTMLResponse(doc, status_code=status, headers=_HEADERS)


def _login_page(msg: str = "", status: int = 200) -> HTMLResponse:
    box = f'<p class="err">{html.escape(msg)}</p>' if msg else ""
    return page("Logger sign-in", f"""<main class="login"><h1>Logger sign-in</h1>{box}
<form method="post" action="/web/login">
<label>Password<input type="password" name="password" autocomplete="current-password" required autofocus></label>
<label>Authenticator code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label>
<button class="primary" type="submit">Sign in</button></form></main>""", status)


def shell(session, body: str, status: int = 200, active: str = "") -> HTMLResponse:
    """A signed-in page: the top bar (with the number of open questions), the entry box, the last reply, then the body."""
    open_q = webquestions.count()
    links = [("today", "/web/", "Today"), ("food", "/web/food", "Food"), ("ideas", "/web/ideas", "Ideas and tasks"), ("health", "/web/health", "Health"), ("questions", "/web/questions", "Questions"),
             ("private", "/web/private", "Private"), ("usage", "/web/usage", "Usage")]
    current = ' aria-current="page"'
    items = "".join(f'<a href="{href}"{current if k == active else ""}>{label}'
                    + (f'<span class="badge">{open_q}</span>' if k == "questions" and open_q else "") + "</a>"
                    for k, href, label in links)
    csrf = session["csrf"]
    bar = (f'<header><div class="bar"><span class="brand">Logger</span><nav>{items}</nav>'
           f'<span class="small date">{datetime.now(logbook.BERLIN):%a %d.%m.%Y}</span>'
           f'<form method="post" action="/web/logout"><input type="hidden" name="csrf" value="{html.escape(csrf)}">'
           '<button class="ghost dim" type="submit">Sign out</button></form></div></header>')
    flash = _pop_flash(session)
    reply = webentry.reply_html(flash, csrf) if flash else ""
    return page("Logger", f"{bar}<main>{webentry.entry_form(csrf)}{reply}{body}</main>", status)


def home(session) -> HTMLResponse:
    csrf = session["csrf"]
    return shell(session, webquestions.today_section(csrf) + todaypage.render(csrf), active="today")


async def _run(work) -> dict:
    """Run one logger call (it may wait for the AI) off the event loop."""
    try:
        return await run_in_threadpool(work)
    except ProviderError:
        return {"outcome": "rejected", "reason": "The AI service did not answer. Nothing was saved; try again in a moment."}


def add_routes(mcp) -> None:
    def hidden(request: Request) -> Response | None:
        return None if is_app_request(request) else Response("Not found", status_code=404)

    def back_to(form) -> str:
        b = str(form.get("back", ""))
        return b if b in webquestions.BACKS else "/web/"

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request) -> Response:
        return hidden(request) or RedirectResponse("/web/", status_code=302, headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/web/static/{name}", methods=["GET"])
    async def static(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        name = request.path_params["name"]
        if name not in _STATIC:
            return Response("Not found", status_code=404)
        return Response((_STATIC_DIR / name).read_bytes(), media_type=_STATIC[name],
                        headers={"Cache-Control": "public, max-age=31536000, immutable" if request.query_params.get("v") == _STATIC_V
                                 else "no-cache", "X-Content-Type-Options": "nosniff"})

    @mcp.custom_route("/web/", methods=["GET"])
    async def web_home(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return home(session)

    @mcp.custom_route("/web/questions", methods=["GET"])
    async def questions_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return shell(session, webquestions.render(session["csrf"]), active="questions")

    @mcp.custom_route("/web/food", methods=["GET"])
    async def food_log(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return shell(session, foodpage.render_log(session["csrf"], request.query_params), active="food")

    @mcp.custom_route("/web/food/catalogue", methods=["GET"])
    async def food_catalogue(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return shell(session, foodpage.render_catalogue(session["csrf"], request.query_params), active="food")

    @mcp.custom_route("/web/food/usual", methods=["GET"])
    async def food_usual(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return shell(session, foodpage.render_usual(session["csrf"], request.query_params), active="food")

    @mcp.custom_route("/web/ideas", methods=["GET"])
    async def ideas_thoughts(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return shell(session, ideaspage.render_thoughts(session["csrf"], request.query_params), active="ideas")

    @mcp.custom_route("/web/ideas/todoist", methods=["GET"])
    async def ideas_todoist(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        body = await run_in_threadpool(lambda: ideaspage.render_todoist(session["csrf"], request.query_params))
        return shell(session, body, active="ideas")

    @mcp.custom_route("/web/private", methods=["GET"])
    async def private_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return shell(session, privatepage.render(session["csrf"], request.query_params), active="private")

    @mcp.custom_route("/web/usage", methods=["GET"])
    async def usage_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        return shell(session, usagepage.render(request.query_params.get("r", "7")), active="usage")

    @mcp.custom_route("/web/health", methods=["GET"])
    async def health_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=302, headers={"Cache-Control": "no-store"})
        r = request.query_params.get("r", "")
        days = int(r) if r in {str(x) for x in healthpage.RANGES} else healthpage.DEFAULT_RANGE
        return shell(session, healthpage.render(session["csrf"], days, request.query_params.get("all") == "1"), active="health")

    @mcp.custom_route("/web/login", methods=["GET"])
    async def login_form(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        if current_session(request):
            return RedirectResponse("/web/", status_code=302, headers={"Cache-Control": "no-store"})
        return _login_page()

    @mcp.custom_route("/web/login", methods=["POST"])
    async def login_submit(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        if not _origin_ok(request):
            return Response("Forbidden", status_code=403)
        form = await request.form()
        password, code = str(form.get("password", "")), str(form.get("code", ""))
        with connect() as c:
            c.execute("DELETE FROM web_sessions WHERE expires_at < now()")
            c.execute("DELETE FROM login_failures WHERE at < now() - interval '1 day'")
            fails = c.execute("SELECT count(*) AS n FROM login_failures WHERE at > now() - make_interval(mins => %s)",
                              (auth.FAILURE_WINDOW_MIN,)).fetchone()["n"]
            if fails >= auth.MAX_FAILURES:
                return _login_page(f"Too many failed attempts. Try again in {auth.FAILURE_WINDOW_MIN} minutes.", 429)
            owner = c.execute("SELECT * FROM owner_credentials WHERE id=1").fetchone()
            ok_pw = auth.check_password(password, owner["password_hash"] if owner else "x$0$0$0$00$00")
            step = auth.check_totp(owner["totp_secret"], code, owner["last_totp_step"]) if owner else None
            if not (owner and ok_pw and step):
                c.execute("INSERT INTO login_failures DEFAULT VALUES")
                return _login_page("Wrong password or code.", 401)
            c.execute("UPDATE owner_credentials SET last_totp_step=%s WHERE id=1", (step,))
            c.execute("DELETE FROM login_failures")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            c.execute("INSERT INTO web_sessions (token_hash, csrf, expires_at) VALUES (%s,%s,%s)",
                      (_h(token), csrf, datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)))
        resp = RedirectResponse("/web/", status_code=303, headers={"Cache-Control": "no-store"})
        resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, path="/", secure=True, httponly=True, samesite="strict")
        return resp

    async def post_form(request: Request):
        """(session, form) for a signed-in, same-origin form post, else (the response that refuses it, None)."""
        if (r := hidden(request)) is not None:
            return r, None
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=303, headers={"Cache-Control": "no-store"}), None
        form = await request.form()
        if not _origin_ok(request) or not csrf_ok(session, form):
            return Response("Forbidden", status_code=403), None
        return session, form

    def done(session, resp: dict, back: str = "/web/") -> Response:
        _set_flash(session, resp)
        return RedirectResponse(back, status_code=303, headers={"Cache-Control": "no-store"})

    def form_key(form):
        return str(form.get("key", ""))[:64] or None

    @mcp.custom_route("/web/log", methods=["POST"])
    async def log_entry(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        text = str(form.get("text", "")).strip()[:webentry.MAX_TEXT]
        key = form_key(form)
        return done(session, await _run(lambda: logbook.log(text, key, channel=logbook.WEB)))

    @mcp.custom_route("/web/answer", methods=["POST"])
    async def answer_question(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        question_id = str(form.get("question_id", ""))[:64]
        reply = str(form.get("quick") or form.get("reply", "")).strip()[:webentry.MAX_REPLY]
        if not question_id or not reply:
            return done(session, {"outcome": "rejected", "reason": "Type an answer first."}, back_to(form))
        return done(session, await _run(lambda: logbook.answer(question_id, reply, channel=logbook.WEB)), back_to(form))

    @mcp.custom_route("/web/lookup", methods=["POST"])
    async def lookup_food(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        question_id = str(form.get("question_id", ""))[:64]
        name = str(form.get("reply", "")).strip()[:200]
        if not name:  # no word typed: look up the word of the question
            with connect() as c:
                row = c.execute("SELECT payload FROM pending WHERE id = %s AND tool = 'log'", (question_id,)).fetchone()
            name = ((row or {}).get("payload") or {}).get("item", {}).get("food", "") if row else ""
        if not name:
            return done(session, {"outcome": "rejected", "reason": "Type the food's name first."}, back_to(form))
        return done(session, await _run(lambda: logbook.food_lookup(None, name, question_id)), back_to(form))

    @mcp.custom_route("/web/habit", methods=["POST"])
    async def habit(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        name, action, eid = str(form.get("name", ""))[:64], str(form.get("action", "")), str(form.get("eid", ""))[:20]
        return done(session, await _run(lambda: webactions.habit(name, action, eid, form_key(form))))

    @mcp.custom_route("/web/usual", methods=["POST"])
    async def usual_meal(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        return done(session, await _run(lambda: webactions.usual(str(form.get("meal", "")), form, form_key(form))))

    @mcp.custom_route("/web/health", methods=["POST"])
    async def health(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        row, ref = str(form.get("row", "")), str(form.get("id", ""))[:20]
        return done(session, await _run(lambda: webactions.health(row, ref, form, form_key(form))))

    @mcp.custom_route("/web/healthday", methods=["POST"])
    async def health_day(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        r = str(form.get("r", ""))
        r = r if r in {str(x) for x in healthpage.RANGES} else str(healthpage.DEFAULT_RANGE)
        day = str(form.get("day", ""))[:10]
        return done(session, await _run(lambda: webactions.health_day(day, r, form, form_key(form))), f"/web/health?r={r}")

    def food_form(fn, back):
        """One form of the Food page (webfood.ACTIONS): signed in, same origin, then the reply and back to the page."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            k = form_key(form)
            return done(session, await _run(lambda: fn(form, k)), back or foodpage.safe_back(form.get("back")))
        return handler

    for _name, (_fn, _back) in webfood.ACTIONS.items():
        mcp.custom_route(f"/web/food/{_name}", methods=["POST"])(food_form(_fn, _back))

    def ideas_form(fn):
        """One form of the Ideas and tasks page (webideas.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            k = form_key(form)
            return done(session, await _run(lambda: fn(form, k)), ideaspage.safe_back(form.get("back")))
        return handler

    for _name, (_fn, _back) in webideas.ACTIONS.items():
        mcp.custom_route(f"/web/ideas/{_name}", methods=["POST"])(ideas_form(_fn))

    def private_form(fn):
        """One form of the Private page (webprivate.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            k = form_key(form)
            return done(session, await _run(lambda: fn(form, k)), privatepage.safe_back(form.get("back")))
        return handler

    for _name, _fn in webprivate.ACTIONS.items():
        mcp.custom_route(f"/web/private/{_name}", methods=["POST"])(private_form(_fn))

    @mcp.custom_route("/web/review", methods=["POST"])
    async def review(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        ref, act = str(form.get("ref", ""))[:20], str(form.get("act", ""))
        return done(session, await _run(lambda: webactions.review(ref, act, form)))

    @mcp.custom_route("/web/logout", methods=["POST"])
    async def logout(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        form = await request.form()
        if not _origin_ok(request) or not csrf_ok(session, form):
            return Response("Forbidden", status_code=403)
        with connect() as c:
            c.execute("DELETE FROM web_sessions WHERE token_hash=%s", (session["token_hash"],))
        resp = RedirectResponse("/web/login", status_code=303, headers={"Cache-Control": "no-store"})
        resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return resp
