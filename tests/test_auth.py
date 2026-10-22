import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from app import auth
from app.mcp_app import build_app
from conftest import owner_sql

CB = "https://claude.ai/api/mcp/auth_callback"
PW = "correct horse battery staple"
SECRET = auth.new_totp_secret()


def set_owner(last=0):
    owner_sql("DELETE FROM owner_credentials")
    owner_sql("INSERT INTO owner_credentials (id,password_hash,totp_secret,last_totp_step) VALUES (1,%s,%s,%s)", auth.hash_password(PW), SECRET, last)


def code_now(offset=0):
    return auth._totp(SECRET, int(time.time() // 30) + offset)


def client():
    return TestClient(build_app(), base_url="https://log.example.test", follow_redirects=False)


def register(c, redirect=CB):
    return c.post("/register", json={"redirect_uris": [redirect], "token_endpoint_auth_method": "none",
                                     "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "client_name": "t"})


def start(c, cid, verifier):
    ch = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = c.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": CB, "code_challenge": ch,
                                    "code_challenge_method": "S256", "state": "st1", "scope": "log"})
    assert r.status_code == 302, r.text
    return parse_qs(urlparse(r.headers["location"]).query)["req"][0]


def full_login(c):
    set_owner()
    cid = register(c).json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    req = start(c, cid, verifier)
    r = c.post("/login", data={"req": req, "password": PW, "code": code_now()})
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["st1"]
    t = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": CB, "client_id": cid, "code_verifier": verifier})
    assert t.status_code == 200, t.text
    return cid, t.json()


def test_totp_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth._totp(secret, 59 // 30) == "287082"
    assert auth.check_totp(secret, "287082", 0, now=59) == 1
    assert auth.check_totp(secret, "287082", 1, now=59) is None  # a code works once


def test_discovery_and_unauthenticated_mcp_is_refused():
    with client() as c:
        assert c.get("/.well-known/oauth-authorization-server").status_code == 200
        assert c.get("/.well-known/oauth-protected-resource/mcp-testpath").status_code == 200
        r = c.post("/mcp-testpath", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401


def test_register_rejects_foreign_redirect():
    with client() as c:
        assert register(c, "https://evil.example.org/cb").status_code == 400
        assert register(c, "https://claude.com/api/mcp/auth_callback").status_code == 201


def test_full_flow_refresh_and_single_use():
    with client() as c:
        cid, tok = full_login(c)
        h = {"Authorization": "Bearer " + tok["access_token"], "Accept": "application/json, text/event-stream"}
        r = c.post("/mcp-testpath", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert r.status_code == 200 and "record_weight" in r.text
        r2 = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
        assert r2.status_code == 200 and r2.json()["access_token"] != tok["access_token"]
        again = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
        assert again.status_code == 400  # rotated, old one is dead


def test_wrong_password_wrong_code_replay_and_lockout():
    with client() as c:
        set_owner()
        cid = register(c).json()["client_id"]
        req = start(c, cid, "v" * 50)
        assert c.post("/login", data={"req": req, "password": "nope", "code": code_now()}).status_code == 401
        assert c.post("/login", data={"req": req, "password": PW, "code": "000000"}).status_code == 401
        ok = c.post("/login", data={"req": req, "password": PW, "code": code_now()})
        assert ok.status_code == 302
        req2 = start(c, cid, "w" * 50)
        assert c.post("/login", data={"req": req2, "password": PW, "code": code_now()}).status_code == 401  # same code twice
        for _ in range(5):
            c.post("/login", data={"req": req2, "password": "bad", "code": "111111"})
        assert c.post("/login", data={"req": req2, "password": PW, "code": code_now(1)}).status_code == 429


def test_no_owner_set_means_no_login():
    with client() as c:
        owner_sql("DELETE FROM owner_credentials")
        cid = register(c).json()["client_id"]
        req = start(c, cid, "x" * 50)
        assert c.post("/login", data={"req": req, "password": PW, "code": "123456"}).status_code == 401
