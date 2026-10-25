"""The free-text tools `log` and `answer`, through the MCP interface the AI app uses. The AI provider is a fake that
returns fixed parser answers, so these tests need no API key and show that the provider can be swapped."""
import copy
import json

import pytest
from starlette.testclient import TestClient

from app import logbook
from app.ai.provider import Usage
from app.mcp_app import build_app
from conftest import owner_sql

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "log.example.test"}


def P(**kw):
    base = dict(intent="log", command_text=None, private=None, private_unsure=False, food=[], usual_meals=[], health=[],
                thoughts=[], todos=[])
    return {**base, **kw}


def F(food, amount, unit="g", when=None, drink=None, en=None):
    return dict(food=food, food_en=en or food, amount=amount, unit=unit, grams_per_unit=None, cooked=False, drink=drink,
                when=when, note=None)


def H(type_, value=None, value2=None, when=None):
    return dict(type=type_, value=value, value2=value2, period=None, session=None, when=when)


def T(summary, thought_type="idea", area="projects"):
    return dict(summary=summary, thought_type=thought_type, area=area, tags=[], project=None)


ANSWERS = {
    "150 g skyr, weight 82.4": P(food=[F("skyr", 150)], health=[H("weight", 82.4)]),
    "150 g skyr and 20 g dragonfruit": P(food=[F("skyr", 150), F("dragonfruit", 20)]),
    "grateful for a quiet morning": P(private="gratitude", thoughts=[T("Grateful for a quiet morning", "journal", "personal")]),
    "why is that?": P(intent="question"),
    "idea: a weekly food chart": P(thoughts=[T("A weekly food chart")]),
    "weight 90": P(health=[H("weight", 90)]),
    "50 g frischkaese": P(food=[F("frischkaese", 50, en="cream cheese")]),
}

LABEL = dict(kcal=60, protein=1.2, fat=0.3, carbs=13, sugar_total=11, source="label")


class FakeProvider:
    def __init__(self):
        self.calls = []

    def json(self, system, user, schema, model, max_tokens=4000):
        text = user.split('Message:\n"""\n', 1)[1].rsplit('\n"""', 1)[0]
        self.calls.append((text, model))
        return copy.deepcopy(ANSWERS[text]), Usage(model, 1000, 100)


@pytest.fixture
def fake():
    f = FakeProvider()
    logbook.PROVIDER = f
    yield f
    logbook.PROVIDER = None


@pytest.fixture
def skyr():
    return owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                        VALUES ('Skyr natural', 'g', 63, 11, 0.2, 4, 'label') RETURNING id""")[0]["id"]


@pytest.fixture
def call(bearer):
    with TestClient(build_app(), base_url="http://log.example.test") as c:
        def _call(tool, **args):
            r = c.post("/mcp-testpath", headers={**HDR, **bearer}, content=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}))
            assert r.status_code == 200, r.text
            res = r.json()["result"]
            assert not res.get("isError"), res
            body = res.get("structuredContent") or json.loads(res["content"][0]["text"])
            assert isinstance(body["message"], str) and body["message"]
            return body
        yield _call


def n(sql, *args):
    return owner_sql(sql, *args)[0]["n"]


def test_food_and_weight_in_one_message(call, fake, skyr):
    body = call("log", text="150 g skyr, weight 82.4", idempotency_key="k1")
    assert body["outcome"] == "saved" and not body["questions"]
    assert n("SELECT count(*) AS n FROM food_entries WHERE food_id = %s AND amount = 150", skyr) == 1
    assert n("SELECT count(*) AS n FROM measurements WHERE value1 = 82.4") == 1
    assert n("SELECT count(*) AS n FROM raw_inbox WHERE channel = 'mcp'") == 1
    assert fake.calls == [("150 g skyr, weight 82.4", "claude-haiku-5-5")]  # nothing open, so no larger model


def test_same_key_is_saved_once(call, fake, skyr):
    a = call("log", text="150 g skyr, weight 82.4", idempotency_key="k2")
    b = call("log", text="150 g skyr, weight 82.4", idempotency_key="k2")
    assert b.get("replayed") and b["outcome"] == a["outcome"] == "saved"
    assert n("SELECT count(*) AS n FROM food_entries") == 1 and len(fake.calls) == 1


def test_unknown_food_is_asked_and_answer_saves_it_into_the_same_message(call, fake, skyr):
    body = call("log", text="150 g skyr and 20 g dragonfruit")
    assert body["outcome"] == "partly_saved" and len(body["questions"]) == 1
    assert [m for _, m in fake.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]  # read once more before asking
    dragon = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                          VALUES ('Dragon fruit', 'g', 60, 1.2, 0, 13, 'database') RETURNING id""")[0]["id"]
    done = call("answer", question_id=body["questions"][0]["question_id"], reply=f"N{dragon}")
    assert done["outcome"] == "saved"
    assert n("SELECT count(DISTINCT raw_id) AS n FROM food_entries") == 1 and n("SELECT count(*) AS n FROM food_entries") == 2
    again = call("answer", question_id=body["questions"][0]["question_id"], reply=f"N{dragon}")
    assert again["outcome"] == "rejected"


def test_gratitude_without_label_goes_to_the_private_store_only(call, fake):
    body = call("log", text="grateful for a quiet morning")
    assert body["outcome"] == "saved" and "not private" in body["message"]
    assert n("SELECT count(*) AS n FROM private.entries WHERE label = 'gratitude'") == 1
    assert n("SELECT count(*) AS n FROM thoughts") == 0 and n("SELECT count(*) AS n FROM raw_inbox") == 0


def test_a_question_for_the_assistant_is_not_logged(call, fake):
    body = call("log", text="why is that?")
    assert body["outcome"] == "not_logged" and n("SELECT count(*) AS n FROM raw_inbox") == 0


def test_a_thought(call, fake):
    body = call("log", text="idea: a weekly food chart")
    assert body["outcome"] == "saved" and n("SELECT count(*) AS n FROM thoughts WHERE thought_type = 'idea'") == 1


def test_an_odd_value_waits_for_yes(call, fake):
    owner_sql("SELECT 1")
    first = call("log", text="weight 90")
    assert first["outcome"] == "saved"
    second = call("log", text="weight 90")  # a second weight on the same day is read back first (v1 rule)
    assert second["outcome"] == "needs_confirmation" and len(second["questions"]) == 1
    assert n("SELECT count(*) AS n FROM measurements") == 1
    done = call("answer", question_id=second["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "saved" and n("SELECT count(*) AS n FROM measurements") == 2


def test_a_german_word_finds_the_english_food(call, fake):
    owner_sql("INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source) VALUES ('Cream cheese', 'g', 250, 6, 24, 3, 'database')")
    body = call("log", text="50 g frischkaese")
    assert body["outcome"] == "saved" and "English name" in body["message"]
    assert n("SELECT count(*) AS n FROM food_entries WHERE amount = 50") == 1


def test_a_new_food_from_a_label_answers_the_open_question(call, fake, skyr):
    asked = call("log", text="150 g skyr and 20 g dragonfruit")
    qid = asked["questions"][0]["question_id"]
    check = call("add_food", name="Dragon fruit (Aldi)", question_id=qid, **LABEL)
    assert check["outcome"] == "needs_confirmation" and len(check["questions"]) == 1  # a label is always read back first
    assert n("SELECT count(*) AS n FROM foods WHERE name = 'Dragon fruit (Aldi)'") == 0
    done = call("answer", question_id=check["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "saved"
    f = owner_sql("SELECT id, source, aliases FROM foods WHERE name = 'Dragon fruit (Aldi)'")[0]
    assert f["source"] == "label" and "dragonfruit" in f["aliases"]  # the user's word now finds it
    assert n("SELECT count(*) AS n FROM food_entries WHERE food_id = %s AND amount = 20", f["id"]) == 1
    assert n("SELECT count(DISTINCT raw_id) AS n FROM food_entries") == 1  # same message as the skyr
    assert call("answer", question_id=qid, reply="N1")["outcome"] == "rejected"  # the question is closed


def test_an_estimate_added_to_the_catalogue_only_is_flagged(call, fake):
    body = call("add_food", name="Masala chai (homemade)", kcal=45, protein=1.5, fat=1.6, carbs=6, source="estimate",
                basis="ml", drink=True)
    assert body["outcome"] == "saved"
    f = owner_sql("SELECT id, needs_review, is_drink, basis FROM foods WHERE name = 'Masala chai (homemade)'")[0]
    assert f["needs_review"] and f["is_drink"] and f["basis"] == "ml"
    assert n("SELECT count(*) AS n FROM food_entries") == 0  # nothing eaten


def test_impossible_values_are_rejected(call, fake):
    body = call("add_food", name="Odd food", kcal=100, protein=1, fat=2, carbs=10, sat_fat=5, source="estimate")
    assert body["outcome"] == "rejected" and n("SELECT count(*) AS n FROM foods") == 0


def test_audit_log_has_no_health_values(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    detail = owner_sql("SELECT detail FROM audit_log WHERE tool = 'log'")[0]["detail"]
    assert set(detail) >= {"raw_id", "ai"} and "82.4" not in json.dumps(detail) and "skyr" not in json.dumps(detail)
