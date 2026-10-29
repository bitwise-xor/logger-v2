"""Core improvements of 29.10.2026: a singular finds a plural food, look-alike suggestions are not offered or learned, the
audit log tells cached from uncached tokens and what the larger model's second read did, /ready, a log line for a failed tool,
the daily clean-up, and the provider's shorter retry."""
import json
import logging
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from app import foods, logbook, maintenance, mcp_app, record
from app.ai.provider import AnthropicProvider
from app.db import connect
from app.mcp_app import build_app
from app.v1_food import _food_cat
from conftest import owner_sql
from test_log import ANSWERS, F, P, fake, off  # noqa: F401 - fixtures reused


def add(name, kcal=100, aliases=()):
    return owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, aliases) VALUES (%s, 'g', %s, 5, 5, 10, 'label', %s)
                        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id""", name, kcal, list(aliases))[0]["id"]


def pick(term):
    with connect() as c, c.cursor() as cur:
        cat = _food_cat(cur)
        f, how, cands = foods.pick(cur, cat, term)
    return (f["id"] if f else None), how, [x["id"] for x in cands]


# ---- food matching ---------------------------------------------------------------------------------

def test_plural_forms_of_the_last_word():
    assert foods._plurals("oat cookie") == ["oat cookies"]
    assert foods._plurals("berry") == ["berries", "berrys"]
    assert foods._plurals("box") == ["boxes"] and foods._plurals("peach") == ["peaches"]
    assert foods._plurals("cookies") == [] and foods._plurals("") == []


def test_a_singular_finds_the_plural_food_and_does_not_fall_to_pasta():
    cookies = add("Oat cookies, Oaties (Biscotto)")
    for name in ("Pasta, cooked", "Cod, cooked", "Quinoa, cooked", "Peas, cooked"):
        add(name)
    for term in ("oat cookie", "oats cookie", "oat cookies", "oats cookies", "Oat Cookie"):
        food, how, _ = pick(term)
        assert food == cookies and how == "close", term


def test_look_alikes_are_not_offered_when_nothing_shares_a_word():
    for name in ("Pasta, cooked", "Cod, cooked", "Quinoa, cooked", "Peas, cooked"):
        add(name)
    food, how, cands = pick("oat cookie")
    assert food is None and how == "none" and cands == []  # v1 offered all four "cooked" foods


def test_a_misspelling_is_still_offered_its_food():
    yog = add("Yogurt, plain")
    add("Pasta, cooked")
    food, how, cands = pick("yoghurt")
    assert food is None and how == "ask" and cands == [yog]


def test_related_needs_a_shared_word_or_a_near_spelling():
    pasta = {"name": "Pasta, cooked", "aliases": []}
    assert not foods.related("oat cookie", pasta) and not foods.related("cookie", pasta)
    assert foods.related("cooked pasta", pasta) and foods.related("pasta", pasta) and foods.related("pastas", pasta)
    assert foods.related("yoghurt", {"name": "Yogurt, plain", "aliases": []})
    assert foods.related("frischkäse", {"name": "Cottage cheese", "aliases": ["frischkäse"]})


def question(word, candidates, via=None):
    raw = owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('web', %s) RETURNING id", f"20 g {word}")[0]["id"]
    now = datetime.now(timezone.utc)
    item = dict(food=word, food_en=word, amount=20, unit="g", grams_per_unit=None, cooked=False, drink=None, when=None, note=None)
    payload = dict(kind="food", text="?", item=item, candidates=candidates, raw_id=raw, now=now.isoformat(), **({"via": via} if via else {}))
    owner_sql("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES ('pq', 'log', %s, 'Which?', now() + interval '1 day')",
              json.dumps(payload))


def answer(food_id):
    with connect() as c, c.cursor() as cur:
        return record.answer(cur, "pq", f"N{food_id}", datetime.now(timezone.utc))


def aliases(fid):
    return owner_sql("SELECT aliases FROM foods WHERE id = %s", fid)[0]["aliases"]


def test_a_pick_among_look_alikes_does_not_teach_a_word():
    pasta = add("Pasta, cooked")
    question("oat cookie", [pasta])
    assert answer(pasta)["outcome"] == "saved"
    assert aliases(pasta) == []  # before: ["oat cookie"], and the next message matched pasta without asking


def test_a_pick_that_shares_a_word_still_teaches_it():
    yog = add("Yogurt, plain")
    question("yoghurt", [yog])
    out = answer(yog)
    assert out["outcome"] == "saved" and aliases(yog) == ["yoghurt"]


def test_the_english_name_suggestion_still_teaches_the_users_word():
    cc = add("Cream cheese")
    question("frischkaese", [cc], via="en")
    assert answer(cc)["outcome"] == "saved" and aliases(cc) == ["frischkaese"]


# ---- audit log -------------------------------------------------------------------------------------

def test_the_audit_log_separates_cached_tokens_and_records_the_second_read(fake):
    ANSWERS["30 g zzqfood"] = P(food=[F("zzqfood", 30)])
    logbook.log("30 g zzqfood", "k-esc")
    row = owner_sql("SELECT detail FROM audit_log WHERE tool = 'log' ORDER BY id DESC LIMIT 1")[0]["detail"]
    assert row["escalated"] is True and row["escalation"] == {"before": ["food"], "after": ["food"], "kept": "first"}
    assert len(row["ai"]) == 2 and all({"model", "in", "out", "cache_read", "cache_write", "usd"} <= set(a) for a in row["ai"])
    assert "zzqfood" not in json.dumps(row)  # kinds of question only, never the words


def test_a_message_that_needs_no_second_read_has_no_escalation_entry(fake, skyr_food):
    logbook.log("150 g skyr, weight 82.4", "k-one")
    row = owner_sql("SELECT detail FROM audit_log WHERE tool = 'log' ORDER BY id DESC LIMIT 1")[0]["detail"]
    assert row["escalated"] is False and row["escalation"] is None


@pytest.fixture
def skyr_food():
    return add("Skyr natural", 63)


# ---- running it ------------------------------------------------------------------------------------

def test_ready_needs_the_database(monkeypatch):
    c = TestClient(build_app(), base_url="http://log.example.test")
    ok = c.get("/ready")
    assert ok.status_code == 200 and ok.json() == {"status": "ok", "database": "ok"}
    monkeypatch.setattr(mcp_app, "_db_reachable", lambda: False)
    down = c.get("/ready")
    assert down.status_code == 503 and down.json() == {"status": "unavailable", "database": "down"}
    assert c.get("/health").json()["status"] == "ok"  # /health stays a plain "the app runs"


def test_a_failed_tool_leaves_one_line_without_the_users_words(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("secret words 82.4 kg")
    monkeypatch.setattr(logbook, "log", boom)
    with caplog.at_level(logging.ERROR, logger="logger.tools"), pytest.raises(RuntimeError):
        mcp_app.log("secret words 82.4 kg")
    assert [r.getMessage() for r in caplog.records] == ["tool log failed: RuntimeError"]


def test_the_daily_clean_up_removes_only_old_short_lived_rows():
    owner_sql("""INSERT INTO idempotency_keys (key, tool, request_hash, response, created_at) VALUES
                 ('old', 'log', 'h', '{}', now() - interval '31 days'), ('new', 'log', 'h', '{}', now() - interval '2 days')""")
    owner_sql("""INSERT INTO pending (id, tool, payload, reason, created_at, expires_at, resolved_at, resolution) VALUES
                 ('p-old-done', 'log', '{}', 'x', now() - interval '40 days', now() - interval '37 days', now() - interval '39 days', 'confirmed'),
                 ('p-old-exp', 'log', '{}', 'x', now() - interval '40 days', now() - interval '35 days', NULL, NULL),
                 ('p-new-done', 'log', '{}', 'x', now() - interval '3 days', now() + interval '1 day', now() - interval '2 days', 'cancelled'),
                 ('p-open', 'log', '{}', 'x', now() - interval '1 day', now() + interval '2 days', NULL, NULL),
                 ('p-exp-recent', 'log', '{}', 'x', now() - interval '10 days', now() - interval '5 days', NULL, NULL)""")
    assert maintenance.cleanup() == {"idempotency_keys": 1, "pending": 2}
    assert [r["key"] for r in owner_sql("SELECT key FROM idempotency_keys")] == ["new"]
    assert sorted(r["id"] for r in owner_sql("SELECT id FROM pending")) == ["p-exp-recent", "p-new-done", "p-open"]
    assert maintenance.cleanup() == {"idempotency_keys": 0, "pending": 0}


def test_the_provider_gives_up_after_three_tries_not_five():
    assert AnthropicProvider("sk-test").client.max_retries == 2
