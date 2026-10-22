import json
from datetime import datetime, timedelta

from app import service
from conftest import owner_sql


def rows(sql, *a):
    return owner_sql(sql, *a)


def test_saved_with_receipt_and_verbatim_text():
    r = service.record_weight(82.4, "2026-10-20T08:30", text="weight 82,4 this morning", idempotency_key="k1")
    assert r["outcome"] == "saved" and r["receipt"]["value"] == 82.4 and r["receipt"]["at"] == "20.10. 08:30"
    assert "20.10. 08:30" in r["message"]
    raw = rows("SELECT channel, content, metadata FROM raw_inbox")
    assert raw[0]["content"] == "weight 82,4 this morning" and raw[0]["channel"] == "mcp" and raw[0]["metadata"]["synthesized"] is False
    m = rows("SELECT value1, time_known, extractor_version FROM measurements")
    assert float(m[0]["value1"]) == 82.4 and m[0]["time_known"] is True and m[0]["extractor_version"] == "v2-pilot-1"


def test_text_missing_is_marked_synthesized():
    service.record_weight(80, "2026-10-19T08:00")
    assert rows("SELECT metadata FROM raw_inbox")[0]["metadata"]["synthesized"] is True


def test_out_of_range_asks_and_nothing_saved_until_confirmed():
    r = service.record_weight(500, "2026-10-19T08:00")
    assert r["outcome"] == "needs_confirmation" and r["pending_id"].startswith("p") and "outside the plausible range" in r["reasons"][0]
    assert rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 0
    ok = service.resolve(r["pending_id"], True)
    assert ok["outcome"] == "saved" and rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 1
    again = service.resolve(r["pending_id"], True)
    assert again["outcome"] == "rejected"
    assert rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 1


def test_second_weight_same_day_asks_and_cancel_saves_nothing():
    service.record_weight(82.0, "2026-10-19T08:00")
    r = service.record_weight(83.0, "2026-10-19T20:00")
    assert r["outcome"] == "needs_confirmation" and "already a weight" in r["reasons"][0]
    c = service.resolve(r["pending_id"], False)
    assert c["outcome"] == "cancelled" and rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 1


def test_bad_inputs_rejected_and_nothing_written():
    for kg, at in ((0, None), (-3, None), (float("inf"), None), (80, "tomorrowish")):
        assert service.record_weight(kg, at)["outcome"] == "rejected"
    assert rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 0
    assert rows("SELECT count(*) AS n FROM raw_inbox")[0]["n"] == 0


def test_idempotent_retry_creates_no_duplicate():
    a = service.record_weight(82.4, "2026-10-18T08:30", idempotency_key="same")
    b = service.record_weight(82.4, "2026-10-18T08:30", idempotency_key="same")
    assert a["outcome"] == "saved" and b["replayed"] is True and b["receipt"]["id"] == a["receipt"]["id"]
    assert rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 1
    other = service.record_weight(90, "2026-10-18T08:30", idempotency_key="same")
    assert other["outcome"] == "rejected" and "different request" in other["message"]


def test_retry_of_a_question_returns_the_same_pending_id():
    a = service.record_weight(500, "2026-10-17T08:00", idempotency_key="q")
    b = service.record_weight(500, "2026-10-17T08:00", idempotency_key="q")
    assert a["pending_id"] == b["pending_id"]
    assert rows("SELECT count(*) AS n FROM pending")[0]["n"] == 1


def test_date_only_is_noon_without_time():
    r = service.record_weight(81, "2026-10-16")
    assert r["receipt"]["time_known"] is False and r["receipt"]["at"] == "16.10. (no time)"


def test_health_day_is_local_time_minus_five_hours():
    r = service.record_weight(81, "2026-10-20T02:30")
    assert r["receipt"]["day"] == "2026-10-19"
    r2 = service.record_weight(81, "2026-10-21T05:30")
    assert r2["receipt"]["day"] == "2026-10-21"


def test_future_time_asks():
    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT10:00")
    r = service.record_weight(81, soon)
    assert r["outcome"] == "needs_confirmation" and "future" in r["reasons"][0]


def test_recent_weights_newest_first_and_window():
    service.record_weight(80, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT09:00"))
    service.record_weight(81, (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT09:00"))
    service.record_weight(82, (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT09:00"))
    r = service.recent_weights(14)
    assert [w["kg"] for w in r["weights"]] == [82.0, 81.0]


def test_undo_only_removes_own_rows():
    r = service.record_weight(82, "2026-10-15T08:00")
    assert service.undo(r["receipt"]["id"])["outcome"] == "saved"
    assert rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 0
    assert service.undo(r["receipt"]["id"])["outcome"] == "rejected"
    assert service.undo("abc")["outcome"] == "rejected"
    owner_sql("""INSERT INTO raw_inbox (channel, content) VALUES ('import','x')""")
    owner_sql("""INSERT INTO measurements (raw_id, type_id, day, event_at, value1, extractor_version)
                 SELECT (SELECT max(id) FROM raw_inbox), id, '2026-01-01', now(), 70, 'v1-import' FROM log_types WHERE name='weight'""")
    mid = rows("SELECT id FROM measurements")[0]["id"]
    assert service.undo(f"m{mid}")["outcome"] == "rejected"
    assert rows("SELECT count(*) AS n FROM measurements")[0]["n"] == 1


def test_expired_question_is_rejected():
    r = service.record_weight(500, "2026-10-14T08:00")
    owner_sql("UPDATE pending SET expires_at = now() - interval '1 minute'")
    assert service.resolve(r["pending_id"], True)["outcome"] == "rejected"


def test_audit_log_has_outcomes_but_no_health_values():
    service.record_weight(82.4, "2026-10-13T08:30", text="secret-looking 82.4")
    service.record_weight(500, "2026-10-13T09:30")
    a = rows("SELECT tool, outcome, detail FROM audit_log ORDER BY id")
    assert [x["outcome"] for x in a] == ["saved", "needs_confirmation"]
    blob = json.dumps([x["detail"] for x in a])
    assert "82.4" not in blob and "500" not in blob
