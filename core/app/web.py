"""Web app: sign-in with the owner's password and authenticator code, then a cookie for 30 days per device.
Served only on the app host name (Caddy, home network and tailnet); never through the public tunnel.
Every form post does its work, keeps the reply in the session ("flash") and sends the browser back to the page with a
303, so reloading a page never repeats an action."""
import hashlib
import hmac
import html
import logging
import pathlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import (activitypage, auth, export, foodpage, gratitude, gratitudepage, gym, gympage, habitspage, healthpage, home as home_actions, homepage, coach, coachpage, ideaspage, insightspage, labelread, labspage, logbook, privatepage, recentpage, searchpage, settingspage, todaypage,
               trendspage, usagepage, webactions, weekpage, webentry, webfood, webheld, webideas, weblabs, webprivate, webhabits, websettings,
               webquestions, webpush, webundo)
from .ai.provider import ProviderError
from .config import APP_HOST
from .db import connect

log = logging.getLogger("logger.web")
COOKIE = "__Host-logger_session"  # the __Host- prefix: Secure, this host only, no Domain
DEVICE_COOKIE = "__Host-logger_device"  # marks a device that signed in once; its failed sign-ins are counted on their own
SESSION_DAYS = 30
DEVICE_DAYS = 400
# Referrer-Policy same-origin, not no-referrer: with no-referrer browsers send "Origin: null" on form posts,
# and _origin_ok needs the real origin. Other sites still get no referrer.
_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; "
                                       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; manifest-src 'self'"}
# Where a form on the entry box or the reply may send the browser back to: any signed-in page of the app, with its query.
BACK_ANY = re.compile(r"/web(/(questions|food(/catalogue|/usual|/trends)?|ideas(/todoist)?|health|habits|gym(/exercises|/plan|/progress|/activity|/history)?|home(/zones|/history|/edit|/pick|/chore)?|coach|week|search|private|gratitude|usage|labs(/tests|/meds)?|settings|recent|insights))?/?"
                      r"(\?[A-Za-z0-9=&._%+-]*)?")
_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
_STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "theme.js": "text/javascript; charset=utf-8",
           "inter.woff2": "font/woff2", "zxing.min.js": "text/javascript; charset=utf-8",  # zxing: the barcode reader, loaded only when scanning
           "manifest.webmanifest": "application/manifest+json", "gratitude.webmanifest": "application/manifest+json", "icon.svg": "image/svg+xml", "icon-192.png": "image/png",
           "icon-512.png": "image/png", "apple-touch-icon.png": "image/png"}
# Where sign-in may send the browser after it (?next=): a page of this app by its path only: /web/, then lowercase letters, digits,
# "-" and "_" in parts split by single "/"; no "//", "..", scheme, backslash, query or encoded character (14.11.2026)
NEXT = re.compile(r"/web/[a-z0-9_-]+(/[a-z0-9_-]+)*")
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
            row = {**row, "token": token}  # for _renew; never shown or stored
    return row


def _origin_ok(request: Request) -> bool:
    """A form post must come from this host's own pages."""
    return request.headers.get("origin") == f"https://{APP_HOST}"


def csrf_ok(session, form) -> bool:
    return bool(session) and hmac.compare_digest(str(form.get("csrf", "")), session["csrf"])


def _set_flash(session, resp: dict) -> None:
    with connect() as c:
        c.execute("UPDATE web_sessions SET flash = %s WHERE token_hash = %s", (Jsonb(resp), session["token_hash"]))


def _set_undo(session, rec) -> None:
    """What the Undo bar offers on this device until its next action (None: no bar)."""
    with connect() as c:
        c.execute("UPDATE web_sessions SET undo = %s WHERE token_hash = %s", (Jsonb(rec) if rec else None, session["token_hash"]))


def _pop_flash(session):
    with connect() as c:
        row = c.execute("SELECT flash FROM web_sessions WHERE token_hash = %s FOR UPDATE", (session["token_hash"],)).fetchone()
        if row and row["flash"] is not None:
            c.execute("UPDATE web_sessions SET flash = NULL WHERE token_hash = %s", (session["token_hash"],))
    return row["flash"] if row else None


def page(title: str, body: str, status: int = 200, cls: str = "", app: str = "Logger", manifest: str = "manifest.webmanifest") -> HTMLResponse:
    """app, manifest: the name and the manifest "Add to Home Screen" uses (the Gratitude page has its own, 14.11.2026)."""
    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
           f'<meta name="theme-color" content="#2f6f4f"><link rel="manifest" href="/web/static/{manifest}">'
           '<link rel="icon" href="/web/static/icon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/web/static/apple-touch-icon.png">'
           '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
           f'<meta name="apple-mobile-web-app-title" content="{html.escape(app)}"><meta name="apple-mobile-web-app-status-bar-style" content="default">'
           f'<title>{html.escape(title)}</title><script src="/web/static/theme.js?v={_STATIC_V}"></script>'  # light or dark before the first paint
           f'<link rel="stylesheet" href="/web/static/app.css?v={_STATIC_V}">'
           f'<script src="/web/static/app.js?v={_STATIC_V}" defer></script></head><body class="{cls}">{body}</body></html>')
    return HTMLResponse(doc, status_code=status, headers=_HEADERS)


def safe_next(value) -> str:
    """The page to open after sign-in: a strict path of this app (NEXT), else ""."""
    v = str(value or "")
    return v if len(v) <= 100 and NEXT.fullmatch(v) else ""


def to_login(request: Request) -> RedirectResponse:
    """A signed-out GET of a page: to the sign-in, which then opens this page again (its path only, if it is a plain one)."""
    nxt = safe_next(request.url.path)
    return RedirectResponse("/web/login" + (f"?next={nxt}" if nxt else ""), status_code=302, headers={"Cache-Control": "no-store"})


def _login_page(msg: str = "", status: int = 200, nxt: str = "") -> HTMLResponse:
    box = f'<p class="err">{html.escape(msg)}</p>' if msg else ""
    keep = f'<input type="hidden" name="next" value="{html.escape(nxt)}">' if safe_next(nxt) else ""
    return page("Logger sign-in", f"""<main class="login"><h1>Logger sign-in</h1>{box}
<form method="post" action="/web/login">{keep}
<label>Password<input type="password" name="password" autocomplete="current-password" required autofocus></label>
<label>Authenticator code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label>
<button class="primary" type="submit">Sign in</button></form></main>""", status)


def shell(session, body: str, status: int = 200, active: str = "", entry: bool = True, title: str = "", app: str = "Logger",
          manifest: str = "manifest.webmanifest") -> HTMLResponse:
    """A signed-in page: the top bar (the number of open questions on the Today link), the entry box, the last reply, then the body.
    Since 04.11.2026 (web item 55) there is no Questions page: every open question is on Today.
    entry=False: no general entry box (the Gratitude page has one text box only); title, app, manifest: see page()."""
    open_q = webquestions.count()
    links = [("today", "/web/", "Today"), ("food", "/web/food", "Food"), ("ideas", "/web/ideas", "Ideas"), ("health", "/web/health", "Health"),
             ("habits", "/web/habits", "Habits"), ("gym", "/web/gym", "Gym"), ("home", "/web/home", "Home"), ("week", "/web/week", "Week"),
             ("recent", "/web/recent", "Recent"), ("insights", "/web/insights", "Insights"), ("coach", "/web/coach", "Coach"), ("private", "/web/private", "Private"), ("usage", "/web/usage", "Usage"),
             ("labs", "/web/labs", "Medical"), ("search", "/web/search", "Search"), ("settings", "/web/settings", "Settings")]
    current = ' aria-current="page"'
    badge = f'<span class="badge">{open_q}<span class="sr"> open</span></span>'
    icon = webentry.icon
    # the icon comes after the label in the markup and is drawn before it (CSS order), so the label stays the link's first text
    link = lambda k, href, label: (f'<a href="{href}"{current if k == active else ""}>{label}'
                                   + (badge if k == "today" and open_q else "") + icon(k) + "</a>")
    by_key = {k: (href, label) for k, href, label in links}

    csrf = session["csrf"]
    signout = (f'<form class="signout" method="post" action="/web/logout"><input type="hidden" name="csrf" value="{html.escape(csrf)}">'
               '<button class="ghost dim" type="submit">Sign out</button></form>')

    def menu(shown, short=False):
        """The given links, then a More menu with all the others (on phones with Sign out at its end)."""
        rest = [k for k, _h, _l in links if k not in shown]
        return ("".join(link(k, by_key[k][0], "Ideas" if short and k == "ideas" else by_key[k][1]) for k in shown)
                + f'<details class="more"><summary{current if active in rest else ""}>More{icon("more")}</summary>'
                + f'<div class="panel">{"".join(link(k, *by_key[k]) for k in rest)}{signout if short else ""}</div></details>')

    # Wide screens: the main links in the top bar, the rest under More. Phones: Today, Food, Health, Ideas, Gym, Home and More in a bar at
    # the bottom. The first six are in the same order on both since 04.11.2026 (Gym since 02.11.2026, Home right after Gym since 04.11.2026).
    items = menu(("today", "food", "health", "ideas", "gym", "home", "habits", "week", "search"))
    tabs = menu(("today", "food", "health", "ideas", "gym", "home"), short=True)
    bar = (f'<header><div class="bar"><a class="brand" href="/web/">{icon("logo")}Logger</a><nav class="top" aria-label="Main">{items}</nav>'
           f'<button class="iconbtn themebtn" type="button" data-theme-toggle aria-label="Switch between light and dark">'
           f'{icon("moon", "moon")}{icon("sun", "sun")}</button>{signout}</div></header>')
    flash = _pop_flash(session)
    reply = webentry.reply_html(flash, csrf) if flash else ""
    undo = webentry.undo_bar(session.get("undo"), csrf)
    title = title or next((label for k, _href, label in links if k == active), "")
    box = webentry.entry_form(csrf, (flash or {}).get("retry_text", "")) if entry else ""
    resp = page(f"{title} · Logger" if title else "Logger",
                f'<a class="skip" href="#main">Skip to content</a>{bar}<main id="main"{"" if entry else " class=noentry"}>'
                f'{box}{reply}{undo}{body}</main>'
                f'<nav class="tabs" aria-label="Main menu">{tabs}</nav>', status, app=app, manifest=manifest)
    _renew(session, resp)
    return resp


def bare_frame(title: str, body: str, status: int = 200) -> HTMLResponse:
    """A page for someone who may not be signed in: the top bar with the name and the light/dark switch only (no menus, no data)."""
    icon = webentry.icon
    bar = (f'<header><div class="bar"><a class="brand" href="/web/">{icon("logo")}Logger</a>'
           f'<button class="iconbtn themebtn" type="button" data-theme-toggle aria-label="Switch between light and dark">'
           f'{icon("moon", "moon")}{icon("sun", "sun")}</button></div></header>')
    return page(title, f'<a class="skip" href="#main">Skip to content</a>{bar}<main id="main">{body}</main>', status)


# Web item 26 (08.11.2026): every error page of the app in its layout, with a short plain message and the way back to Today.
# Never a stack trace or any detail: the kind of error goes to the log only.
ERRORS = {404: ("Page not found", "This page does not exist."),
          403: ("Nothing was sent", "This form did not come from the app's own page, so nothing was saved. Open the page again and try once more."),
          500: ("Something went wrong", "Something went wrong. Nothing was lost; try again.")}


def error_page(session, status: int) -> HTMLResponse:
    """The error page: signed in, the full frame (menus, entry box); signed out, the bare frame. If the full frame cannot be
    made (the database is the error, say), the bare one."""
    head, msg = ERRORS[status]
    body = (f'<section class="errpage"><h1>{head}</h1><p>{html.escape(msg)}</p>'
            f'<p><a class="primary" href="/web/">Back to Today</a></p></section>')
    if session:
        try:
            return shell(session, body, status)
        except Exception as e:  # noqa: BLE001 - the kind only
            log.error("the error page could not show the menus: %s", type(e).__name__)
    return bare_frame(f"{head} · Logger", body, status)


def server_error(request: Request, exc: Exception) -> Response:
    """The app's handler for an error nobody caught (Starlette still passes it on to the server's log, traceback included).
    A page of the app opened in the browser gets the error page; everything else (the MCP endpoint, other hosts, the page's
    own fetches for JSON) the plain 500 as before."""
    if not (is_app_request(request) and request.url.path.startswith("/web/") and "text/html" in request.headers.get("accept", "")):
        return PlainTextResponse("Internal Server Error", status_code=500)
    log.error("a web page failed: %s", type(exc).__name__)
    try:
        session = current_session(request)
    except Exception:  # noqa: BLE001 - the database may be the error
        session = None
    return error_page(session, 500)


def _renew(session, resp) -> None:
    """A device in use stays signed in: once a day a page view moves the end to SESSION_DAYS from now, cookie included (until
    04.11.2026 a session ended 30 days after signing in, however often it was used)."""
    token, ends = session.get("token"), session.get("expires_at")
    if not token or not ends or ends > datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS - 1):
        return
    with connect() as c:
        c.execute("UPDATE web_sessions SET expires_at = now() + make_interval(days => %s) WHERE token_hash = %s", (SESSION_DAYS, session["token_hash"]))
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, path="/", secure=True, httponly=True, samesite="strict")


def home(session, asked=None) -> HTMLResponse:
    csrf = session["csrf"]
    day, today = todaypage.pick_day(asked)
    # unread messages and open questions belong to today
    questions = webheld.section(csrf) + webquestions.today_section(csrf) if day == today else ""
    return shell(session, questions + todaypage.render(csrf, day), active="today")


async def _run(work) -> dict:
    """Run one logger call (it may wait for the AI) off the event loop. A typed message is kept before the AI reads it
    (held.py), so an error here means nothing was kept or changed: the reply says so (the kind of error goes to the log)."""
    try:
        return await run_in_threadpool(work)
    except ProviderError as e:
        log.warning("the AI service did not answer (%s)", type(e).__name__)
        return {"outcome": "rejected", "reason": "The AI service did not answer. Nothing was saved; try again in a moment."}
    except Exception as e:  # noqa: BLE001 - one line with the kind only, never what the user typed
        log.error("a web action failed: %s", type(e).__name__)
        return {"outcome": "rejected", "reason": "Something went wrong (the database or a service did not answer). Nothing was saved; try again in a moment."}


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
            return to_login(request)
        return home(session, request.query_params.get("day"))

    @mcp.custom_route("/web/questions", methods=["GET"])
    async def questions_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return RedirectResponse("/web/", status_code=302, headers={"Cache-Control": "no-store"})  # the questions are on Today since 04.11.2026

    @mcp.custom_route("/web/food", methods=["GET"])
    async def food_log(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, foodpage.render_log(session["csrf"], request.query_params), active="food")

    @mcp.custom_route("/web/food/catalogue", methods=["GET"])
    async def food_catalogue(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, foodpage.render_catalogue(session["csrf"], request.query_params), active="food")

    @mcp.custom_route("/web/food/usual", methods=["GET"])
    async def food_usual(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, foodpage.render_usual(session["csrf"], request.query_params), active="food")

    @mcp.custom_route("/web/search", methods=["GET"])
    async def search_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, searchpage.render(session["csrf"], request.query_params), active="search")

    @mcp.custom_route("/web/food/trends", methods=["GET"])
    async def food_trends(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, foodpage._tabs("trends") + trendspage.render(session["csrf"], request.query_params), active="food")

    @mcp.custom_route("/web/week", methods=["GET"])
    async def week_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, weekpage.render(session["csrf"], request.query_params), active="week")

    @mcp.custom_route("/web/ideas", methods=["GET"])
    async def ideas_thoughts(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, ideaspage.render_thoughts(session["csrf"], request.query_params), active="ideas")

    @mcp.custom_route("/web/ideas/todoist", methods=["GET"])
    async def ideas_todoist(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        body = await run_in_threadpool(lambda: ideaspage.render_todoist(session["csrf"], request.query_params))
        return shell(session, body, active="ideas")

    @mcp.custom_route("/web/private", methods=["GET"])
    async def private_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, privatepage.render(session["csrf"], request.query_params), active="private")

    @mcp.custom_route("/web/usage", methods=["GET"])
    async def usage_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, usagepage.render(request.query_params.get("r", "7")), active="usage")

    def labs_tab(render):
        """One tab of the Medical page."""
        async def handler(request: Request) -> Response:
            if (r := hidden(request)) is not None:
                return r
            session = current_session(request)
            if not session:
                return to_login(request)
            return shell(session, render(session["csrf"], request.query_params), active="labs")
        return handler

    for _path, _render in (("/web/labs", labspage.render_reports), ("/web/labs/tests", labspage.render_tests),
                           ("/web/labs/meds", labspage.render_meds)):
        mcp.custom_route(_path, methods=["GET"])(labs_tab(_render))

    @mcp.custom_route("/web/health", methods=["GET"])
    async def health_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        r = request.query_params.get("r", "")
        days = int(r) if r in {str(x) for x in healthpage.RANGES} else healthpage.DEFAULT_RANGE
        return shell(session, healthpage.render(session["csrf"], days, request.query_params.get("all") == "1", request.query_params), active="health")

    @mcp.custom_route("/web/login", methods=["GET"])
    async def login_form(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        nxt = safe_next(request.query_params.get("next"))
        if current_session(request):
            return RedirectResponse(nxt or "/web/", status_code=302, headers={"Cache-Control": "no-store"})
        return _login_page(nxt=nxt)

    @mcp.custom_route("/web/login", methods=["POST"])
    async def login_submit(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        if not _origin_ok(request):
            return error_page(None, 403)
        form = await request.form()
        password, code = str(form.get("password", "")), str(form.get("code", ""))
        nxt = safe_next(form.get("next"))
        device_token = request.cookies.get(DEVICE_COOKIE)
        with connect() as c:
            c.execute("DELETE FROM web_sessions WHERE expires_at < now()")
            c.execute("DELETE FROM login_failures WHERE at < now() - interval '1 day'")
            # A device that signed in before counts its own failures; every other attempt shares one pool, so a stranger's
            # tries can lock out strangers but never the owner's own phone or PC.
            known = bool(device_token) and c.execute("SELECT 1 FROM trusted_devices WHERE device_hash = %s", (_h(device_token),)).fetchone()
            pool = _h(device_token) if known else None
            fails = c.execute("""SELECT count(*) AS n FROM login_failures WHERE device_hash IS NOT DISTINCT FROM %s
                                 AND at > now() - make_interval(mins => %s)""", (pool, auth.FAILURE_WINDOW_MIN)).fetchone()["n"]
            if fails >= auth.MAX_FAILURES:
                return _login_page(f"Too many failed attempts. Try again in {auth.FAILURE_WINDOW_MIN} minutes.", 429, nxt)
            owner = c.execute("SELECT * FROM owner_credentials WHERE id=1").fetchone()
            ok_pw = auth.check_password(password, owner["password_hash"] if owner else "x$0$0$0$00$00")
            step = auth.check_totp(owner["totp_secret"], code, owner["last_totp_step"]) if owner else None
            if not (owner and ok_pw and step):
                c.execute("INSERT INTO login_failures (device_hash) VALUES (%s)", (pool,))
                return _login_page("Wrong password or code.", 401, nxt)
            c.execute("UPDATE owner_credentials SET last_totp_step=%s WHERE id=1", (step,))
            c.execute("DELETE FROM login_failures WHERE device_hash IS NOT DISTINCT FROM %s", (pool,))
            device_token = device_token if known else secrets.token_urlsafe(32)
            c.execute("""INSERT INTO trusted_devices (device_hash) VALUES (%s)
                         ON CONFLICT (device_hash) DO UPDATE SET last_seen = now()""", (_h(device_token),))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            c.execute("INSERT INTO web_sessions (token_hash, csrf, expires_at, user_agent, device_hash) VALUES (%s,%s,%s,%s,%s)",
                      (_h(token), csrf, datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
                       request.headers.get("user-agent", "")[:300], _h(device_token)))
        resp = RedirectResponse(nxt or "/web/", status_code=303, headers={"Cache-Control": "no-store"})
        resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, path="/", secure=True, httponly=True, samesite="strict")
        resp.set_cookie(DEVICE_COOKIE, device_token, max_age=DEVICE_DAYS * 86400, path="/", secure=True, httponly=True, samesite="strict")
        return resp

    async def post_form(request: Request):
        """(session, form) for a signed-in, same-origin form post, else (the response that refuses it, None)."""
        if (r := hidden(request)) is not None:
            return r, None
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=303, headers={"Cache-Control": "no-store"}), None
        form = await request.form()
        if not _origin_ok(request):
            return error_page(None, 403), None
        if not csrf_ok(session, form):  # a page from before signing in again: say so, and give typed words back (04.11.2026)
            _set_flash(session, {"outcome": "rejected", "head": "Nothing saved.",
                                 "reason": "That page was out of date. It is fresh now: please do it again.",
                                 **({"retry_text": str(form.get("text"))[:webentry.MAX_TEXT]} if form.get("text") else {})})
            return RedirectResponse(back_any(form), status_code=303, headers={"Cache-Control": "no-store"}), None
        return session, form

    def done(session, resp: dict, back: str = "/web/") -> Response:
        rows = resp.pop("undo_rows", None)  # the rows before a change: kept for Undo, not shown
        _set_flash(session, resp)
        new = webundo.record(resp, rows)
        # every action replaces the last one's Undo (or clears it), except a double tap's repeat or a refusal: they changed
        # nothing, and wiping the Undo of the change just made lost it (until 04.11.2026)
        if new is not None or not (resp.get("replayed") or resp.get("outcome") == "rejected"):
            _set_undo(session, new)
        return RedirectResponse(back, status_code=303, headers={"Cache-Control": "no-store"})

    def form_key(form):
        return str(form.get("key", ""))[:64] or None

    def back_any(form, default: str = "/web/") -> str:
        """The page the form was sent from, if it is one of the app's pages."""
        b = str(form.get("back", ""))
        return b if BACK_ANY.fullmatch(b) else default

    @mcp.custom_route("/web/log", methods=["POST"])
    async def log_entry(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        text = str(form.get("text", "")).strip()[:webentry.MAX_TEXT]
        key = form_key(form)
        resp = await _run(lambda: logbook.log(text, key, channel=logbook.WEB))
        if resp.get("outcome") == "rejected" and text:
            resp = {**resp, "retry_text": text}  # nothing was saved: the words come back in the box
        # "held": the words are kept and shown under "Not read yet" on Today; not put back in the box, so they are not sent twice
        # An open question is shown on Today and on Questions only, so a message that asks one goes to Today.
        return done(session, resp, "/web/" if resp.get("questions") else back_any(form))

    @mcp.custom_route("/web/undo", methods=["POST"])
    async def undo_last(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        raw_id = str(form.get("raw_id", ""))[:12]
        return done(session, await _run(lambda: webactions.undo(raw_id)), back_any(form))

    @mcp.custom_route("/web/undo/last", methods=["POST"])
    async def undo_bar(request: Request) -> Response:
        """The Undo bar of a delete or a correction: only the change this device made last, named by its token."""
        session, form = await post_form(request)
        if form is None:
            return session
        rec = session.get("undo") or {}
        if not webundo.fresh(rec) or not hmac.compare_digest(str(form.get("token", "")), str(rec.get("token", ""))):
            return done(session, {"outcome": "rejected", "head": "Nothing undone.", "reason": "There is nothing to undo any more."}, back_any(form))
        return done(session, await _run(lambda: webundo.apply(rec)), back_any(form))

    @mcp.custom_route("/web/recent", methods=["GET"])
    async def recent_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        csrf = session["csrf"]
        return shell(session, recentpage.render(csrf, webheld.section(csrf, "/web/recent")), active="recent")

    def held_form(fn):
        """Try again or Delete on a message not read yet (webheld.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            return done(session, await _run(lambda: fn(form, form_key(form))), back_any(form))
        return handler

    for _name, _fn in webheld.ACTIONS.items():
        mcp.custom_route(f"/web/held/{_name}", methods=["POST"])(held_form(_fn))

    def gym_tab(render, active="gym"):
        """One tab of the Gym section (02.11.2026)."""
        async def handler(request: Request) -> Response:
            if (r := hidden(request)) is not None:
                return r
            session = current_session(request)
            if not session:
                return to_login(request)
            return shell(session, render(session["csrf"], request.query_params), active=active)
        return handler

    for _path, _render in (("/web/gym", gympage.render_workout), ("/web/gym/plan", gympage.render_plan), ("/web/gym/progress", gympage.render_progress),
                          ("/web/gym/activity", activitypage.render), ("/web/gym/history", gympage.render_history),
                          ("/web/gym/exercises", gympage.render_exercises)):
        mcp.custom_route(_path, methods=["GET"])(gym_tab(_render))

    def gym_form(fn):
        """One form of the Gym section (gym.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            return done(session, await _run(lambda: fn(form, form_key(form), session)), back_any(form, "/web/gym"))
        return handler

    for _name, _fn in gym.ACTIONS.items():
        mcp.custom_route(f"/web/gym/{_name}", methods=["POST"])(gym_form(_fn))

    for _path, _render in (("/web/home", homepage.render_now), ("/web/home/zones", homepage.render_zones), ("/web/home/history", homepage.render_history),
                           ("/web/home/edit", homepage.render_edit), ("/web/home/pick", homepage.render_pick),
                           ("/web/home/chore", homepage.render_chore)):  # the Home page (03.11.2026; a chore's page since 04.11.2026)
        mcp.custom_route(_path, methods=["GET"])(gym_tab(_render, active="home"))

    mcp.custom_route("/web/coach", methods=["GET"])(gym_tab(coachpage.render, active="coach"))  # coaching (web item 52, 04.11.2026)

    @mcp.custom_route("/web/coach/start", methods=["POST"])
    async def coach_start(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        return done(session, await _run(lambda: coach.start(form, form_key(form), session)), back_any(form, "/web/"))

    @mcp.custom_route("/web/coach/status", methods=["GET"])
    async def coach_status(request: Request) -> Response:
        """Whether a coaching is still being read (app.js asks every few seconds while a card says "Coaching…")."""
        if (r := hidden(request)) is not None:
            return r
        if not current_session(request):
            return JSONResponse({"status": "signed out"}, status_code=403)
        cid = str(request.query_params.get("id", ""))[:12]
        return JSONResponse({"status": await _run(lambda: coach.status(int(cid))) if cid.isdigit() else "gone"}, headers={"Cache-Control": "no-store"})

    def home_form(fn):
        """One form of the Home page (home_actions.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            return done(session, await _run(lambda: fn(form, form_key(form), session)), back_any(form, "/web/home"))
        return handler

    for _name, _fn in home_actions.ACTIONS.items():
        mcp.custom_route(f"/web/home/{_name}", methods=["POST"])(home_form(_fn))

    @mcp.custom_route("/web/foods.json", methods=["GET"])
    async def foods_json(request: Request) -> Response:
        """The catalogue for the entry box's suggestions: active foods, how often each was eaten, its last amount and when
        it was eaten last (t, seconds since 1970; the most recent come first since 02.11.2026)."""
        if (r := hidden(request)) is not None:
            return r
        if not current_session(request):
            return Response("Not found", status_code=404)
        with connect() as c:
            rows = c.execute("""SELECT f.name, f.basis, f.aliases, x.amount, x.last, coalesce(x.n, 0) AS n FROM foods f
                                LEFT JOIN (SELECT food_id, count(*) AS n, (array_agg(amount ORDER BY eaten_at DESC, id DESC))[1] AS amount,
                                                  max(eaten_at) AS last
                                           FROM food_entries GROUP BY food_id) x ON x.food_id = f.id
                                WHERE f.active ORDER BY n DESC, f.name""").fetchall()
        out = [{"n": r["name"], "b": r["basis"], "w": list(r["aliases"] or []), "a": float(r["amount"]) if r["amount"] is not None else None,
                "c": r["n"], "t": int(r["last"].timestamp()) if r["last"] else 0} for r in rows]
        return JSONResponse(out, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @mcp.custom_route("/web/insights", methods=["GET"])
    async def insights_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, insightspage.render(session["csrf"]), active="insights")

    @mcp.custom_route("/web/foods/barcode.json", methods=["GET"])
    async def food_by_barcode(request: Request) -> Response:
        """The scanner on Today: the catalogue's food with this barcode (and the amount eaten last), or null."""
        if (r := hidden(request)) is not None:
            return r
        if not current_session(request):
            return Response("Not found", status_code=404)
        code = re.sub(r"\D", "", request.query_params.get("code", ""))[:14]
        food = None
        if len(code) >= 8:
            same = {code, code.zfill(13) if len(code) == 12 else code, code[1:] if len(code) == 13 and code[0] == "0" else code}  # UPC-A = EAN-13 with a 0
            with connect() as c:
                r = c.execute("""SELECT f.id, f.name, f.basis, (SELECT e.amount FROM food_entries e WHERE e.food_id = f.id
                                                               ORDER BY e.eaten_at DESC, e.id DESC LIMIT 1) AS amount
                                 FROM foods f WHERE f.active AND f.barcode = ANY(%s) ORDER BY f.id LIMIT 1""", (list(same),)).fetchone()
            if r:
                food = {"id": r["id"], "name": r["name"], "basis": r["basis"], "amount": float(r["amount"]) if r["amount"] is not None else 100.0}
        return JSONResponse({"code": code, "food": food}, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @mcp.custom_route("/web/foods/off.json", methods=["GET"])
    async def food_off(request: Request) -> Response:
        """The scanner's "Look it up": Open Food Facts' values for a barcode, to check in the dialog (nothing saved, 04.11.2026)."""
        if (r := hidden(request)) is not None:
            return r
        if not current_session(request):
            return Response("Not found", status_code=404)
        out = await _run(lambda: webfood.off_values(request.query_params.get("code", "")))
        return JSONResponse(out, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @mcp.custom_route("/web/food/label", methods=["POST"])
    async def label_photo(request: Request) -> Response:
        """The scanner's "Photo of the label": Claude reads the values (labelread.py); the page shows them to check, nothing is saved here."""
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session or not _origin_ok(request) or not hmac.compare_digest(request.headers.get("x-csrf", ""), session["csrf"]):
            return JSONResponse({"ok": False}, status_code=403)
        try:
            form = await request.form(max_files=1, max_fields=5)
            photo = form.get("photo")
            data, media_type = await photo.read(labelread.MAX_BYTES + 1), (photo.content_type or "").split(";")[0].strip().lower()
        except (AttributeError, ValueError, KeyError):
            return JSONResponse({"ok": False, "reason": "No photo came with the request."}, status_code=400)
        return JSONResponse(await _run(lambda: labelread.read(data, media_type)), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/web/sw.js", methods=["GET"])
    async def service_worker(request: Request) -> Response:
        """The service worker for the nudges (static/sw.js), served under /web/ so it may show notifications for the app."""
        if (r := hidden(request)) is not None:
            return r
        return Response((_STATIC_DIR / "sw.js").read_bytes(), media_type="text/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    @mcp.custom_route("/web/push/subscribe", methods=["POST"])
    async def push_subscribe(request: Request) -> Response:
        """Settings > Notifications on a device: keep its push address (only of a known push service) and keys."""
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session or not _origin_ok(request) or not hmac.compare_digest(request.headers.get("x-csrf", ""), session["csrf"]):
            return JSONResponse({"ok": False}, status_code=403)
        try:
            body = await request.json()
            endpoint, keys = str(body.get("endpoint", ""))[:1000], body.get("keys") or {}
            p256dh, auth_key = str(keys.get("p256dh", ""))[:200], str(keys.get("auth", ""))[:100]
        except (ValueError, AttributeError):
            return JSONResponse({"ok": False, "reason": "Unreadable request."}, status_code=400)
        if not webpush.allowed(endpoint) or not webpush.valid_keys(p256dh, auth_key):
            return JSONResponse({"ok": False, "reason": "Not a known push service."}, status_code=400)
        with connect() as c:
            c.execute("""INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent, session_sid) VALUES (%s, %s, %s, %s, %s)
                         ON CONFLICT (endpoint) DO UPDATE SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                           user_agent = EXCLUDED.user_agent, session_sid = EXCLUDED.session_sid, failures = 0""",
                      (endpoint, p256dh, auth_key, request.headers.get("user-agent", "")[:300], session["sid"]))
            logbook._audit(c.cursor(), "web_change", "changed", {"what": "notifications on"}, logbook.WEB)
        return JSONResponse({"ok": True})

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
        return done(session, await _run(lambda: webactions.habit(name, action, eid, form_key(form))), back_any(form))

    @mcp.custom_route("/web/habit/day", methods=["POST"])
    async def habit_day(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        name, day = str(form.get("name", ""))[:64], str(form.get("day", ""))[:10]
        return done(session, await _run(lambda: webactions.habit_day(name, day, form_key(form))), back_any(form))

    @mcp.custom_route("/web/habits", methods=["GET"])
    async def habits_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, habitspage.render(session["csrf"], request.query_params), active="habits")

    def habits_form(fn):
        """One form of the Habits page's list (webhabits.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            k = form_key(form)
            return done(session, await _run(lambda: fn(form, k, session)), back_any(form, "/web/habits"))
        return handler

    for _name, _fn in webhabits.ACTIONS.items():
        mcp.custom_route(f"/web/habits/{_name}", methods=["POST"])(habits_form(_fn))

    @mcp.custom_route("/web/usual", methods=["POST"])
    async def usual_meal(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        return done(session, await _run(lambda: webactions.usual(str(form.get("meal", "")), form, form_key(form))))

    @mcp.custom_route("/web/checkin", methods=["POST"])
    async def checkin(request: Request) -> Response:
        session, form = await post_form(request)
        if form is None:
            return session
        return done(session, await _run(lambda: webactions.checkin(form, form_key(form))))

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
        back = f"/web/health?r={r}" + ("&all=1" if form.get("all") == "1" else "")  # an older row stays shown (04.11.2026)
        return done(session, await _run(lambda: webactions.health_day(day, r, form, form_key(form))), back)

    @mcp.custom_route("/web/body", methods=["POST"])
    async def body_form(request: Request) -> Response:
        """The Health page's Body measurements form (03.11.2026)."""
        session, form = await post_form(request)
        if form is None:
            return session
        r = str(form.get("r", ""))
        r = r if r in {str(x) for x in healthpage.RANGES} else str(healthpage.DEFAULT_RANGE)
        return done(session, await _run(lambda: webactions.body_save(form, form_key(form))), f"/web/health?r={r}")

    @mcp.custom_route("/web/body/skip", methods=["POST"])
    async def body_skip(request: Request) -> Response:
        """"Not today" on Today's Body measurements line."""
        session, form = await post_form(request)
        if form is None:
            return session
        return done(session, await _run(webactions.body_skip))

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
        """One form of the Ideas page (webideas.ACTIONS), back to the page it came from."""
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

    # ---- the gratitude list (step B, 14.11.2026): gratitudepage.py, gratitude.py. The words of a line travel only in a POST
    # body, into private.entries, and back in lines.json to the signed-in owner who pressed Show; never in a URL, a log line,
    # the audit log, a flash or another table.
    @mcp.custom_route("/web/gratitude", methods=["GET"])
    async def gratitude_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, gratitudepage.render(session["csrf"]), entry=False, title="Gratitude", app="Gratitude",
                     manifest="gratitude.webmanifest")

    def grat_back(form) -> str:
        b = str(form.get("back", ""))
        return b if b in ("/web/", "/web/gratitude") else "/web/gratitude"

    async def grat_json(request: Request, work, max_fields: int = 6) -> Response:
        """A fetch from app.js (data-grat): signed in, same origin, the CSRF token in the X-CSRF header; a JSON answer."""
        session = current_session(request)
        if not session or not _origin_ok(request) or not hmac.compare_digest(request.headers.get("x-csrf", ""), session["csrf"]):
            return JSONResponse({"ok": False, "message": "Not saved: open the page again (signed out or out of date)."}, status_code=403,
                                headers={"Cache-Control": "no-store"})
        try:
            form = await request.form(max_files=0, max_fields=max_fields)
        except Exception:  # noqa: BLE001 - an unreadable body
            return JSONResponse({"ok": False, "message": "Not saved, try again."}, status_code=400, headers={"Cache-Control": "no-store"})
        try:
            out = await run_in_threadpool(lambda: work(form))
        except Exception as e:  # noqa: BLE001 - the kind only, never the words
            log.error("a gratitude action failed: %s", type(e).__name__)
            out = {"ok": False, "message": "Not saved, try again."}
        return JSONResponse(out, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    def grat_save(form) -> dict:
        out = gratitude.save(str(form.get("text", ""))[:webentry.MAX_TEXT], form_key(form))
        if out["ok"]:  # ids, time, count, goal and the key for the next line; never the words
            return {"ok": True, "ids": out["ids"], "at": out["at"], "count": out["count"], "goal": out["goal"], "key": webentry.key()}
        return {"ok": False, "message": out["message"], **({"key": webentry.key()} if out.get("key_used") else {})}

    @mcp.custom_route("/web/gratitude/line", methods=["POST"])
    async def gratitude_line(request: Request) -> Response:
        """Save a line: app.js's fetch (X-CSRF header, JSON answer), or the plain form without script (flash, back to the page)."""
        if (r := hidden(request)) is not None:
            return r
        if "x-csrf" in request.headers:
            return await grat_json(request, grat_save)
        session = current_session(request)
        if not session:
            return RedirectResponse("/web/login", status_code=303, headers={"Cache-Control": "no-store"})
        form = await request.form()
        if not _origin_ok(request):
            return error_page(None, 403)
        back = grat_back(form)
        if not csrf_ok(session, form):  # unlike post_form: the words are not kept in the flash
            _set_flash(session, {"outcome": "rejected", "head": "Nothing saved.", "reason": "That page was out of date. It is fresh now: please type it again."})
            return RedirectResponse(back, status_code=303, headers={"Cache-Control": "no-store"})
        try:
            out = await run_in_threadpool(lambda: gratitude.save(str(form.get("text", ""))[:webentry.MAX_TEXT], form_key(form)))
        except Exception as e:  # noqa: BLE001 - the kind only
            log.error("a gratitude save failed: %s", type(e).__name__)
            out = {"ok": False, "message": "Not saved, try again."}
        if not out["ok"]:
            return done(session, {"outcome": "rejected", "head": "Nothing saved.", "reason": out["message"]}, back)
        return done(session, {"outcome": "saved", "head": f"Saved privately. {out['count']} of {out['goal']} today."}, back)

    @mcp.custom_route("/web/gratitude/edit", methods=["POST"])
    async def gratitude_edit(request: Request) -> Response:
        """Change the words of one of today's lines (app.js only: Edit exists only where the script runs)."""
        if (r := hidden(request)) is not None:
            return r
        if "x-csrf" not in request.headers:
            return JSONResponse({"ok": False, "message": "Change a line on the Private page."}, status_code=400, headers={"Cache-Control": "no-store"})
        return await grat_json(request, lambda form: gratitude.edit(str(form.get("id", ""))[:20], str(form.get("text", ""))[:webentry.MAX_TEXT]))

    @mcp.custom_route("/web/gratitude/lines.json", methods=["GET"])
    async def gratitude_lines(request: Request) -> Response:
        """Show: today's lines with their words, for the signed-in owner only, never cached."""
        if (r := hidden(request)) is not None:
            return r
        if not current_session(request):
            return JSONResponse({"ok": False}, status_code=403, headers={"Cache-Control": "no-store"})
        try:
            rows = await run_in_threadpool(gratitude.lines)
        except Exception as e:  # noqa: BLE001 - the kind only
            log.error("the gratitude lines failed: %s", type(e).__name__)
            return JSONResponse({"ok": False}, status_code=500, headers={"Cache-Control": "no-store"})
        return JSONResponse({"ok": True, "lines": rows}, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    def labs_form(fn):
        """One form of the Medical page (weblabs.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            k = form_key(form)
            return done(session, await _run(lambda: fn(form, k)), labspage.safe_back(form.get("back")))
        return handler

    for _name, _fn in weblabs.ACTIONS.items():
        mcp.custom_route(f"/web/labs/{_name}", methods=["POST"])(labs_form(_fn))

    @mcp.custom_route("/web/settings", methods=["GET"])
    async def settings_page(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        if not session:
            return to_login(request)
        return shell(session, settingspage.render(session, request.query_params), active="settings")

    def settings_form(fn):
        """One form of the Settings page (websettings.ACTIONS), back to the page it came from."""
        async def handler(request: Request) -> Response:
            session, form = await post_form(request)
            if form is None:
                return session
            k = form_key(form)
            return done(session, await _run(lambda: fn(form, k, session)), settingspage.safe_back(form.get("back")))
        return handler

    for _name, _fn in websettings.ACTIONS.items():
        mcp.custom_route(f"/web/settings/{_name}", methods=["POST"])(settings_form(_fn))

    @mcp.custom_route("/web/settings/export", methods=["POST"])
    async def settings_export(request: Request) -> Response:
        """Settings > Export: the ZIP of everything logged comes back as the answer itself (a download, no page change)."""
        session, form = await post_form(request)
        if form is None:
            return session
        try:
            data, name = await run_in_threadpool(lambda: export.download(form, session))
        except Exception as e:  # noqa: BLE001 - the kind only
            log.error("the export failed: %s", type(e).__name__)
            return done(session, {"outcome": "rejected", "reason": "The export could not be made (the database did not answer). Try again in a moment."},
                        "/web/settings?tab=export")
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
                                 "X-Content-Type-Options": "nosniff"})

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
        if not _origin_ok(request):
            return error_page(None, 403)
        if not session:  # already signed out (e.g. "Sign out all other devices" elsewhere): it showed "Forbidden" until 04.11.2026
            resp = RedirectResponse("/web/login", status_code=303, headers={"Cache-Control": "no-store"})
            resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
            return resp
        if not csrf_ok(session, form):
            return error_page(None, 403)
        with connect() as c:
            c.execute("DELETE FROM web_sessions WHERE token_hash=%s", (session["token_hash"],))
        resp = RedirectResponse("/web/login", status_code=303, headers={"Cache-Control": "no-store"})
        resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return resp

    @mcp.custom_route("/web/{rest:path}", methods=["GET"])  # last: every page above is matched first
    async def not_found(request: Request) -> Response:
        if (r := hidden(request)) is not None:
            return r
        session = current_session(request)
        return error_page(session, 404)  # signed out: the bare frame, the same for every path, so no hint which pages exist
