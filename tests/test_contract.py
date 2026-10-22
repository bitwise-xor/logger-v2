"""Contract tests: every write tool, every outcome, through the MCP interface the AI app uses.
The cases are plain data (tool, arguments, expected outcome), so they can be replayed against another front end later."""
import json

import pytest
from starlette.testclient import TestClient

from app.mcp_app import build_app

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "log.example.test"}
OUTCOMES = {"saved", "needs_confirmation", "rejected", "cancelled", "ok"}


@pytest.fixture
def call(bearer):
    with TestClient(build_app(), base_url="http://log.example.test") as c:
        def _call(name, **args):
            r = c.post("/mcp-testpath", headers={**HDR, **bearer}, content=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}))
            assert r.status_code == 200, r.text
            res = r.json()["result"]
            body = res.get("structuredContent") or json.loads(res["content"][0]["text"])
            assert body["outcome"] in OUTCOMES and isinstance(body["message"], str) and body["message"]
            return body
        yield _call


def check_shape(body):
    if body["outcome"] == "saved" and "receipt" in body:
        r = body["receipt"]
        assert {"id", "type", "day", "at", "time_known"} <= set(r)
    if body["outcome"] == "needs_confirmation":
        assert body["pending_id"].startswith("p") and body["reasons"] and "resolve" in body["message"]


# (tool, arguments, expected outcome) for the first call on an empty day
FIRST_CALLS = [
    ("record_weight", {"kg": 82.4, "at": "2026-10-20T08:00"}, "saved"),
    ("record_weight", {"kg": 500, "at": "2026-10-20T08:00"}, "needs_confirmation"),
    ("record_weight", {"kg": 0, "at": "2026-10-20T08:00"}, "rejected"),
    ("record_weight", {"kg": 80, "at": "not a time"}, "rejected"),
    ("record_bp", {"systolic": 120, "diastolic": 80, "period": "morning", "at": "2026-10-20T07:00"}, "saved"),
    ("record_bp", {"systolic": 320, "diastolic": 80, "at": "2026-10-20T07:00"}, "needs_confirmation"),
    ("record_bp", {"systolic": 80, "diastolic": 120, "at": "2026-10-20T07:00"}, "needs_confirmation"),
    ("record_bp", {"systolic": 120, "diastolic": 80, "period": "noon"}, "rejected"),
    ("record_bp", {"systolic": -1, "diastolic": 80}, "rejected"),
    ("record_wakeup", {"at": "2026-10-20T06:30"}, "saved"),
    ("record_wakeup", {"at": "yesterdayish"}, "rejected"),
    ("amend", {"receipt_id": "m99999", "kg": 80}, "rejected"),
    ("amend", {"receipt_id": "nonsense", "kg": 80}, "rejected"),
    ("undo", {"receipt_id": "m99999"}, "rejected"),
    ("undo", {"receipt_id": "e99999"}, "rejected"),
    ("resolve", {"pending_id": "pdeadbeef", "confirm": True}, "rejected"),
]


@pytest.mark.parametrize("tool,args,expected", FIRST_CALLS, ids=[f"{t}-{e}-{i}" for i, (t, _, e) in enumerate(FIRST_CALLS)])
def test_first_call_outcomes(call, tool, args, expected):
    body = call(tool, **args)
    assert body["outcome"] == expected
    check_shape(body)


@pytest.mark.parametrize("tool,args", [
    ("record_weight", {"kg": 82.4, "at": "2026-10-20T08:00"}),
    ("record_bp", {"systolic": 120, "diastolic": 80, "period": "morning", "at": "2026-10-20T07:00"}),
    ("record_wakeup", {"at": "2026-10-20T06:30"}),
])
def test_second_entry_the_same_day_asks_then_resolve_saves_or_cancels(call, tool, args):
    first = call(tool, **args)
    assert first["outcome"] == "saved"
    second = call(tool, **args)
    assert second["outcome"] == "needs_confirmation"
    check_shape(second)
    cancelled = call("resolve", pending_id=second["pending_id"], confirm=False)
    assert cancelled["outcome"] == "cancelled"
    third = call(tool, **args)
    saved = call("resolve", pending_id=third["pending_id"], confirm=True)
    assert saved["outcome"] == "saved"
    check_shape(saved)
    assert call("resolve", pending_id=third["pending_id"], confirm=True)["outcome"] == "rejected"   # answered once only


@pytest.mark.parametrize("tool,args", [
    ("record_weight", {"kg": 82.4, "at": "2026-10-20T08:00"}),
    ("record_bp", {"systolic": 120, "diastolic": 80, "at": "2026-10-20T07:00"}),
    ("record_wakeup", {"at": "2026-10-20T06:30"}),
])
def test_idempotent_retry_replays_and_a_changed_request_with_the_same_key_is_rejected(call, tool, args):
    a = call(tool, idempotency_key="k-" + tool, **args)
    b = call(tool, idempotency_key="k-" + tool, **args)
    assert a["outcome"] == "saved" and b.get("replayed") is True and b["receipt"]["id"] == a["receipt"]["id"]
    changed = {**args, "at": "2026-10-19T07:00"}
    assert call(tool, idempotency_key="k-" + tool, **changed)["outcome"] == "rejected"


def test_amend_and_undo_by_receipt(call):
    w = call("record_weight", kg=82.4, at="2026-10-20T08:00")["receipt"]["id"]
    b = call("record_bp", systolic=125, diastolic=82, at="2026-10-20T07:00")["receipt"]["id"]
    e = call("record_wakeup", at="2026-10-20T06:30")["receipt"]["id"]
    assert call("amend", receipt_id=w, kg=81.9)["receipt"]["value"] == 81.9
    assert call("amend", receipt_id=b, diastolic=79)["receipt"]["value"] == "125/79"
    assert call("amend", receipt_id=e, at="2026-10-20T06:50")["receipt"]["at"] == "20.10. 06:50"
    assert call("amend", receipt_id=w, systolic=120)["outcome"] == "rejected"      # wrong field for this entry
    assert call("amend", receipt_id=w)["outcome"] == "rejected"                    # nothing to change
    ask = call("amend", receipt_id=w, kg=500)
    assert ask["outcome"] == "needs_confirmation"
    check_shape(ask)
    assert call("resolve", pending_id=ask["pending_id"], confirm=True)["receipt"]["value"] == 500
    for rid in (w, b, e):
        assert call("undo", receipt_id=rid)["outcome"] == "saved"
        assert call("undo", receipt_id=rid)["outcome"] == "rejected"
    assert call("recent_weights")["weights"] == [] and call("recent_wakeups")["wakeups"] == []


def test_reads_are_ok_and_newest_first(call):
    call("record_weight", kg=80, at="2026-10-18T08:00")
    assert call("recent_weights", days=3650)["outcome"] == "ok"


def test_tool_schemas_are_stable(bearer):
    with TestClient(build_app(), base_url="http://log.example.test") as c:
        r = c.post("/mcp-testpath", headers={**HDR, **bearer}, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}))
        tools = {t["name"]: t["inputSchema"] for t in r.json()["result"]["tools"]}
    required = {"record_weight": ["kg"], "record_bp": ["systolic", "diastolic"], "record_wakeup": [], "amend": ["receipt_id"],
                "resolve": ["pending_id", "confirm"], "undo": ["receipt_id"], "recent_weights": [], "recent_blood_pressure": [], "recent_wakeups": []}
    assert {k: sorted(v.get("required", [])) for k, v in tools.items()} == {k: sorted(v) for k, v in required.items()}
    for name in ("record_weight", "record_bp", "record_wakeup"):
        assert "idempotency_key" in tools[name]["properties"] and "at" in tools[name]["properties"]
