"""Single-owner OAuth 2.1 server for the connector: password + authenticator code, tokens stored hashed."""
import base64
import hashlib
import hmac
import html
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError, RefreshToken,
                                      RegistrationError, TokenError, construct_redirect_uri)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .config import ALLOWED_REDIRECTS
from .db import connect

SCOPE = "log"
ACCESS_SECONDS = 3600
REFRESH_DAYS = 30
CODE_SECONDS = 300
REQUEST_SECONDS = 600
MAX_CLIENTS = 50
MAX_FAILURES = 5
FAILURE_WINDOW_MIN = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- password and authenticator code -------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 15, 8, 1
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, want = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                            maxmem=64 * 1024 * 1024, dklen=32)
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), want)


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), "sha1").digest()
    o = digest[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", digest[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000)


def check_totp(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """The matching time step (window of one step either side) if it is newer than last_step, else None."""
    code = code.strip().replace(" ", "")
    step_now = int((now if now is not None else time.time()) // 30)
    found = None
    for step in (step_now - 1, step_now, step_now + 1):
        if hmac.compare_digest(_totp(secret, step), code) and step > last_step:
            found = step
    return found


# ---- the OAuth provider --------------------------------------------------------------------------

class OwnerProvider:
    def __init__(self, public_url: str):
        self.public_url = public_url.rstrip("/")

    async def get_client(self, client_id: str):
        with connect() as c:
            row = c.execute("SELECT info FROM oauth_clients WHERE client_id=%s", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate(row["info"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if str(uri) not in ALLOWED_REDIRECTS:
                raise RegistrationError("invalid_redirect_uri", "redirect URI not allowed")
        if not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri", "redirect URI required")
        with connect() as c:
            n = c.execute("SELECT count(*) AS n FROM oauth_clients").fetchone()["n"]
            if n >= MAX_CLIENTS:
                raise RegistrationError("invalid_client_metadata", "too many registered clients")
            c.execute("INSERT INTO oauth_clients (client_id, info) VALUES (%s,%s)",
                      (client_info.client_id, client_info.model_dump_json()))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        rid = secrets.token_urlsafe(24)
        with connect() as c:
            c.execute("DELETE FROM oauth_requests WHERE expires_at < now()")
            c.execute("INSERT INTO oauth_requests (id, client_id, params, expires_at) VALUES (%s,%s,%s,%s)",
                      (rid, client.client_id, params.model_dump_json(), _now() + timedelta(seconds=REQUEST_SECONDS)))
        return f"{self.public_url}/login?req={rid}"

    async def load_authorization_code(self, client, authorization_code: str):
        with connect() as c:
            row = c.execute("SELECT data FROM oauth_codes WHERE code_hash=%s AND expires_at > now()", (_h(authorization_code),)).fetchone()
        if not row or row["data"]["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate({**row["data"], "code": authorization_code})

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        with connect() as c:
            gone = c.execute("DELETE FROM oauth_codes WHERE code_hash=%s", (_h(authorization_code.code),)).rowcount
            if not gone:
                raise TokenError("invalid_grant", "code already used")
            return self._issue(c, client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token: str):
        with connect() as c:
            row = c.execute("SELECT * FROM oauth_tokens WHERE token_hash=%s AND kind='refresh' AND expires_at > now()",
                            (_h(refresh_token),)).fetchone()
        if not row or row["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=row["client_id"], scopes=row["scopes"],
                            expires_at=int(row["expires_at"].timestamp()), resource=row["resource"])

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        scopes = scopes or refresh_token.scopes
        if not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "scope not granted")
        with connect() as c:
            gone = c.execute("DELETE FROM oauth_tokens WHERE token_hash=%s AND kind='refresh'", (_h(refresh_token.token),)).rowcount
            if not gone:
                raise TokenError("invalid_grant", "refresh token already used")
            return self._issue(c, client.client_id, scopes, refresh_token.resource)

    async def load_access_token(self, token: str):
        with connect() as c:
            row = c.execute("SELECT * FROM oauth_tokens WHERE token_hash=%s AND kind='access' AND expires_at > now()", (_h(token),)).fetchone()
        if not row:
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=row["scopes"],
                           expires_at=int(row["expires_at"].timestamp()), resource=row["resource"])

    def _issue(self, c, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        c.execute("DELETE FROM oauth_tokens WHERE expires_at < now()")
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = _now()
        for tok, kind, ttl in ((access, "access", timedelta(seconds=ACCESS_SECONDS)), (refresh, "refresh", timedelta(days=REFRESH_DAYS))):
            c.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, scopes, resource, expires_at) VALUES (%s,%s,%s,%s,%s,%s)",
                      (_h(tok), kind, client_id, scopes, resource, now + ttl))
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_SECONDS,
                          refresh_token=refresh, scope=" ".join(scopes))

    # ---- login page ----

    def create_code(self, req: dict) -> str:
        p = req["params"]
        code = secrets.token_urlsafe(32)
        data = {"scopes": p["scopes"] or [SCOPE], "expires_at": time.time() + CODE_SECONDS, "client_id": req["client_id"],
                "code_challenge": p["code_challenge"], "redirect_uri": p["redirect_uri"],
                "redirect_uri_provided_explicitly": p["redirect_uri_provided_explicitly"], "resource": p.get("resource")}
        with connect() as c:
            c.execute("INSERT INTO oauth_codes (code_hash, data, expires_at) VALUES (%s,%s,%s)",
                      (_h(code), __import__("json").dumps(data), _now() + timedelta(seconds=CODE_SECONDS)))
        return code


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Logger sign-in</title><style>body{{font-family:system-ui,sans-serif;max-width:22rem;margin:3rem auto;padding:0 1rem}}
input,button{{width:100%;box-sizing:border-box;font-size:1rem;padding:.6rem;margin:.3rem 0 1rem}}.err{{color:#b00020}}</style></head>
<body><h1>Logger sign-in</h1><p>Allow the connector to access your logger.</p>{msg}
<form method="post" action="/login"><input type="hidden" name="req" value="{req}">
<label>Password<input type="password" name="password" autocomplete="current-password" required></label>
<label>Authenticator code<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label>
<button type="submit">Sign in</button></form></body></html>"""

_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'; default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://claude.ai https://claude.com"}


def _page(req: str, msg: str = "", status: int = 200) -> HTMLResponse:
    box = f'<p class="err">{html.escape(msg)}</p>' if msg else ""
    return HTMLResponse(_PAGE.format(req=html.escape(req), msg=box), status_code=status, headers=_HEADERS)


def _load_request(req_id: str):
    with connect() as c:
        return c.execute("SELECT * FROM oauth_requests WHERE id=%s AND expires_at > now()", (req_id,)).fetchone()


def add_login_routes(mcp, provider: OwnerProvider) -> None:
    @mcp.custom_route("/login", methods=["GET"])
    async def login_form(request: Request) -> Response:
        req = request.query_params.get("req", "")
        if not _load_request(req):
            return HTMLResponse("This sign-in link has expired. Start again from the app.", status_code=400, headers=_HEADERS)
        return _page(req)

    @mcp.custom_route("/login", methods=["POST"])
    async def login_submit(request: Request) -> Response:
        form = await request.form()
        req_id, password, code = str(form.get("req", "")), str(form.get("password", "")), str(form.get("code", ""))
        req = _load_request(req_id)
        if not req:
            return HTMLResponse("This sign-in link has expired. Start again from the app.", status_code=400, headers=_HEADERS)
        with connect() as c:
            c.execute("DELETE FROM login_failures WHERE at < now() - interval '1 day'")
            fails = c.execute("SELECT count(*) AS n FROM login_failures WHERE at > now() - make_interval(mins => %s)", (FAILURE_WINDOW_MIN,)).fetchone()["n"]
            if fails >= MAX_FAILURES:
                return _page(req_id, f"Too many failed attempts. Try again in {FAILURE_WINDOW_MIN} minutes.", 429)
            owner = c.execute("SELECT * FROM owner_credentials WHERE id=1").fetchone()
            ok_pw = check_password(password, owner["password_hash"] if owner else "x$0$0$0$00$00")
            step = check_totp(owner["totp_secret"], code, owner["last_totp_step"]) if owner else None
            if not (owner and ok_pw and step):
                c.execute("INSERT INTO login_failures DEFAULT VALUES")
                return _page(req_id, "Wrong password or code.", 401)
            c.execute("UPDATE owner_credentials SET last_totp_step=%s WHERE id=1", (step,))
            c.execute("DELETE FROM oauth_requests WHERE id=%s", (req_id,))
            c.execute("DELETE FROM login_failures")
        auth_code = provider.create_code(req)
        redirect = construct_redirect_uri(req["params"]["redirect_uri"], code=auth_code, state=req["params"]["state"])
        return RedirectResponse(redirect, status_code=302, headers={"Cache-Control": "no-store"})
