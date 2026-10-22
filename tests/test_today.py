from datetime import datetime, timedelta

from starlette.testclient import TestClient

from app import service
from app.mcp_app import build_app


def get(host, headers=None):
    with TestClient(build_app(), base_url=f"http://{host}") as c:
        return c.get("/today", headers=headers or {})


def test_private_host_shows_entries():
    d = lambda n, h: (datetime.now() - timedelta(days=n)).strftime(f"%Y-%m-%dT{h}")
    service.record_weight(81.5, d(0, "07:00"))
    service.record_bp(124, 81, "morning", d(0, "07:05"))
    service.record_wakeup(d(1, "06:40"))
    service.record_weight(70, d(30, "07:00"))
    r = get("today.example.test")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    for want in ("Weight 81.5 kg", "Blood pressure 124/81 (morning)", "Wake-up", "07:05"):
        assert want in r.text
    assert "70 kg" not in r.text


def test_empty_day_says_so():
    assert "nothing logged" in get("today.example.test").text


def test_public_host_and_cloudflare_requests_get_404():
    service.record_weight(81.5, datetime.now().strftime("%Y-%m-%dT07:00"))
    r = get("log.example.test")
    assert r.status_code == 404 and "81.5" not in r.text
    r2 = get("today.example.test", {"CF-Connecting-IP": "1.2.3.4"})
    assert r2.status_code == 404 and "81.5" not in r2.text
    assert get("today.example.test", {"CF-Ray": "abc"}).status_code == 404
