import json

from starlette.testclient import TestClient

from app.mcp_app import build_app
from conftest import owner_sql

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "log.example.test"}


def rpc(c, method, params=None, path="/mcp-testpath", headers=HDR):
    return c.post(path, headers=headers, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}))


def client():
    return TestClient(build_app(), base_url="http://log.example.test")


def test_health_and_unknown_paths(bearer):
    with client() as c:
        assert c.get("/health").json()["status"] == "ok"
        assert c.get("/docs").status_code == 404
        assert rpc(c, "tools/list", path="/mcp", headers={**HDR, **bearer}).status_code in (404, 405)


def test_wrong_host_is_refused(bearer):
    with client() as c:
        r = rpc(c, "tools/list", headers={**HDR, **bearer, "Host": "evil.example.org"})
        assert r.status_code in (400, 403, 421)


def test_tools_list_and_a_call(bearer):
    with client() as c:
        tools = rpc(c, "tools/list", headers={**HDR, **bearer}).json()["result"]["tools"]
        assert {t["name"] for t in tools} == {"record_weight", "recent_weights", "record_bp", "recent_blood_pressure", "record_wakeup", "recent_wakeups", "amend", "resolve", "undo", "log", "answer", "add_food", "food_words", "food_lookup", "lab_report"}
        r = rpc(c, "tools/call", {"name": "record_weight", "arguments": {"kg": 82.4, "at": "2026-10-12T08:30", "idempotency_key": "mcp1"}}, headers={**HDR, **bearer})
        res = r.json()["result"]
        assert res.get("isError") in (False, None)
        body = res.get("structuredContent") or json.loads(res["content"][0]["text"])
        assert body["outcome"] == "saved" and body["receipt"]["at"] == "12.10. 08:30"
        assert owner_sql("SELECT count(*) AS n FROM measurements")[0]["n"] == 1
