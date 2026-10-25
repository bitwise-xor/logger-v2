"""The free-text tools `log` and `answer`, through the MCP interface the AI app uses. The AI provider is a fake that
returns fixed parser answers, so these tests need no API key and show that the provider can be swapped."""
import copy
import json
import re

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
    "this is part of the garden test": P(thoughts=[dict(T("Part of the garden test", "note"), project="garden")]),
    "project garden: planted tomatoes": P(thoughts=[dict(T("Planted tomatoes", "progress"), project="garden")]),
    "weight 90": P(health=[H("weight", 90)]),
    "weight 90 kg": P(health=[H("weight", 90)]),
    "apple was 150 g": P(intent="command", command_text="apple was 150 g"),
    "what did I eat today": P(intent="command", command_text="what did I eat today"),
    "the skyr was 120 g": P(intent="command", command_text="the skyr was 120 g"),
    "weight was 80 not 82.4": P(intent="command", command_text="weight was 80 not 82.4"),
    "undo that": P(intent="command", command_text="undo that"),
    "delete the skyr": P(intent="command", command_text="delete the skyr"),
    "40 g radishes": P(food=[F("radishes", 40)]),
    "50 g frischkaese": P(food=[F("frischkaese", 50, en="cream cheese")]),
}

def _id(user, pattern):
    return re.search(pattern, user)[1]


def C(*steps, unclear=None):
    return {"steps": [{"action": a, "args": json.dumps(x)} for a, x in steps], "unclear": unclear}


# The command reader's answers: a function of its input, because the ids come from the recent entries it is shown.
COMMANDS = {
    "apple was 150 g": lambda u: C(),  # no apple logged: nothing to change
    "what did I eat today": lambda u: C(("find", {"food": "today"})),
    "the skyr was 120 g": lambda u: C(("change", {"id": "f" + _id(u, r"f(\d+) [^\n]*Skyr natural"), "fields": {"amount": 120, "unit": "g"}})),
    "weight was 80 not 82.4": lambda u: C(("change", {"id": "m" + _id(u, r"m(\d+) [^\n]*weight"), "fields": {"value": 80}})),
    "undo that": lambda u: C(("undo", {})),
    "delete the skyr": lambda u: C(unclear="Which skyr: the first or the second?"),
    "delete the skyr\n(Asked:": lambda u: C(("change", {"id": "f" + _id(u, r"f(\d+) [^\n]*Skyr natural"), "delete": True})),
}

LABEL = dict(kcal=60, protein=1.2, fat=0.3, carbs=13, sugar_total=11, source="label")


class FakeProvider:
    def __init__(self):
        self.calls = []

    def json(self, system, user, schema, model, max_tokens=4000):
        if 'Request:\n"""\n' in user:  # the command reader
            text = user.split('Request:\n"""\n', 1)[1].rsplit('\n"""', 1)[0]
            self.calls.append((text, model))
            key = max((k for k in COMMANDS if text.startswith(k)), key=len)
            return COMMANDS[key](user), Usage(model, 1000, 100)
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
    second = call("log", text="weight 90 kg")  # a second weight on the same day is read back first (v1 rule)
    assert second["outcome"] == "needs_confirmation" and len(second["questions"]) == 1
    assert n("SELECT count(*) AS n FROM measurements") == 1
    done = call("answer", question_id=second["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "saved" and n("SELECT count(*) AS n FROM measurements") == 2


def test_a_german_word_gets_the_english_food_suggested_not_taken(call, fake):
    cc = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                      VALUES ('Cream cheese', 'g', 250, 6, 24, 3, 'database') RETURNING id""")[0]["id"]
    body = call("log", text="50 g frischkaese")
    assert body["outcome"] == "needs_choice" and f"N{cc} Cream cheese" in body["message"]  # a translation may be another product
    assert n("SELECT count(*) AS n FROM food_entries") == 0
    done = call("answer", question_id=body["questions"][0]["question_id"], reply=f"N{cc}")
    assert done["outcome"] == "saved" and n("SELECT count(*) AS n FROM food_entries WHERE amount = 50") == 1
    assert "frischkaese" in owner_sql("SELECT aliases FROM foods WHERE id = %s", cc)[0]["aliases"]  # found directly next time


def test_a_new_food_from_a_label_answers_the_open_question(call, fake, skyr):
    asked = call("log", text="150 g skyr and 20 g dragonfruit")
    qid = asked["questions"][0]["question_id"]
    check = call("add_food", name="Dragon fruit (Aldi)", question_id=qid, **LABEL)
    assert check["outcome"] == "needs_confirmation" and len(check["questions"]) == 1  # a label is always read back first
    assert n("SELECT count(*) AS n FROM foods WHERE name = 'Dragon fruit (Aldi)'") == 0
    done = call("answer", question_id=check["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "saved"
    f = owner_sql("SELECT id, source, aliases FROM foods WHERE name = 'Dragon fruit (Aldi)'")[0]
    assert f["source"] == "label" and f["aliases"] == []  # the new food may answer a different question: no silent alias
    assert "food_words" in done["message"]  # the receipt suggests saving the word
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


def test_a_yes_with_more_words_confirms_and_an_unclear_reply_keeps_the_question(call, fake):
    first = call("add_food", name="Cottage cheese, semi-fat (ja!)", aliases=["frischkäse", "fresh cheese"], **LABEL)
    qid = first["questions"][0]["question_id"]
    unclear = call("answer", question_id=qid, reply="hmm, let me look again")
    assert unclear["outcome"] == "needs_confirmation" and n("SELECT count(*) AS n FROM foods") == 0  # still open
    done = call("answer", question_id=qid, reply="yes, it is correct, this is also called frischkäse")
    assert done["outcome"] == "saved"
    aliases = owner_sql("SELECT aliases FROM foods WHERE name = 'Cottage cheese, semi-fat (ja!)'")[0]["aliases"]
    assert {"frischkäse", "fresh cheese"} <= set(aliases)


def test_food_words_adds_aliases_refuses_clashes_and_sets_what_a_word_means(call, fake, skyr):
    other = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                         VALUES ('Skyr vanilla', 'g', 80, 9, 0.2, 10, 'label') RETURNING id""")[0]["id"]
    added = call("food_words", food_id=skyr, words=["isländischer joghurt"])
    assert added["outcome"] == "saved" and "now finds" in added["message"]
    clash = call("food_words", food_id=other, words=["Isländischer Joghurt"])
    assert "already used by" in clash["message"]
    assert "isländischer joghurt" not in owner_sql("SELECT aliases FROM foods WHERE id = %s", other)[0]["aliases"]
    means = call("food_words", food_id=other, words=["skyr"], means=True)
    assert "now means" in means["message"]
    assert owner_sql("SELECT food_id FROM food_defaults WHERE term = 'skyr'")[0]["food_id"] == other


def test_audit_log_has_no_health_values(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    detail = owner_sql("SELECT detail FROM audit_log WHERE tool = 'log'")[0]["detail"]
    assert set(detail) >= {"raw_id", "ai"} and "82.4" not in json.dumps(detail) and "skyr" not in json.dumps(detail)


def test_the_same_message_twice_within_30_minutes_asks_first(call, fake):
    first = call("log", text="idea: a weekly food chart")
    assert first["outcome"] == "saved" and n("SELECT count(*) AS n FROM thoughts") == 1
    again = call("log", text="idea: a weekly food chart")
    assert again["outcome"] == "needs_confirmation" and "already logged" in again["message"]
    assert n("SELECT count(*) AS n FROM thoughts") == 1 and len(fake.calls) == 1  # nothing saved, no AI call
    no = call("answer", question_id=again["questions"][0]["question_id"], reply="no")
    assert no["outcome"] == "cancelled" and n("SELECT count(*) AS n FROM thoughts") == 1
    again = call("log", text="idea: a weekly food chart")
    yes = call("answer", question_id=again["questions"][0]["question_id"], reply="yes, log it again")
    assert yes["outcome"] == "saved" and n("SELECT count(*) AS n FROM thoughts") == 2
    assert call("answer", question_id=again["questions"][0]["question_id"], reply="yes")["outcome"] == "rejected"  # closed


def test_a_new_food_that_looks_like_an_existing_one_asks_first(call, fake):
    old = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                       VALUES ('Salad greens, mixed, raw', 'g', 17, 1.5, 0.2, 2, 'database') RETURNING id""")[0]["id"]
    body = call("add_food", name="Salad greens, mixed", kcal=17, protein=1.5, fat=0.2, carbs=2, source="estimate")
    assert body["outcome"] == "needs_confirmation" and f"N{old}" in body["message"]
    assert n("SELECT count(*) AS n FROM foods WHERE name = 'Salad greens, mixed'") == 0
    done = call("answer", question_id=body["questions"][0]["question_id"], reply="yes")  # create it anyway
    assert done["outcome"] == "saved" and n("SELECT count(*) AS n FROM foods WHERE name = 'Salad greens, mixed'") == 1


def test_the_same_product_under_another_name_asks_first(call, fake):
    old = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                       VALUES ('Cottage cheese, semi-fat (ja!)', 'g', 60, 1.2, 0.3, 13, 'label') RETURNING id""")[0]["id"]
    body = call("add_food", name="Cottage cheese, half-fat (ja!)", **LABEL)  # same base name and the same label values
    assert body["outcome"] == "needs_confirmation" and f"N{old}" in body["message"]
    other = call("add_food", name="Quark, plain", **{**LABEL, "kcal": 61})  # different name, different values
    assert f"N{old}" not in other["message"]
    same = call("add_food", name="Fresh cheese (Aldi)", **LABEL)  # different name, the same values
    assert f"N{old}" in same["message"]


def test_an_answer_can_name_the_food_and_the_amount(call, fake, skyr):
    asked = call("log", text="150 g skyr and 20 g dragonfruit")
    fruit = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                         VALUES ('Pitaya', 'g', 60, 1.2, 0.3, 13, 'database') RETURNING id""")[0]["id"]
    done = call("answer", question_id=asked["questions"][0]["question_id"], reply=f"N{fruit} 150 g")
    assert done["outcome"] == "saved"
    assert n("SELECT count(*) AS n FROM food_entries WHERE food_id = %s AND amount = 150", fruit) == 1  # not the 20 g


def test_a_correction_is_not_logged_as_a_new_item(call, fake):
    body = call("log", text="apple was 150 g")
    assert body["outcome"] == "not_logged" and "Nothing done" in body["message"]
    assert n("SELECT count(*) AS n FROM food_entries") == 0 and n("SELECT count(*) AS n FROM raw_inbox") == 0


def test_a_plural_word_finds_the_singular_food(call, fake):
    radish = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source, state)
                          VALUES ('Radish, raw', 'g', 16, 0.7, 0.1, 3.4, 'database', 'raw') RETURNING id""")[0]["id"]
    owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                 VALUES ('Raisins', 'g', 299, 3.1, 0.5, 79, 'database')""")
    body = call("log", text="40 g radishes")
    assert body["outcome"] == "saved" and n("SELECT count(*) AS n FROM food_entries WHERE food_id = %s", radish) == 1


def test_a_look_up_answers_from_the_data_and_saves_nothing(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    body = call("log", text="what did I eat today")
    assert body["outcome"] == "answered" and "Look-up result" in body["message"] and "Skyr natural" in body["message"]
    assert n("SELECT count(*) AS n FROM raw_inbox") == 1 and n("SELECT count(*) AS n FROM food_entries") == 1


def test_a_change_is_previewed_and_made_only_after_yes(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    asked = call("log", text="the skyr was 120 g")
    assert asked["outcome"] == "needs_confirmation" and "Preview" in asked["message"] and "120" in asked["message"]
    assert n("SELECT count(*) AS n FROM food_entries WHERE amount = 150") == 1  # the preview was rolled back
    done = call("answer", question_id=asked["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "changed", done["message"]
    assert n("SELECT count(*) AS n FROM food_entries WHERE amount = 120") == 1
    assert owner_sql("SELECT round(kcal) AS k FROM food_entries")[0]["k"] == 76  # 63 kcal per 100 g, recalculated


def test_a_change_can_be_declined(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    asked = call("log", text="weight was 80 not 82.4")
    no = call("answer", question_id=asked["questions"][0]["question_id"], reply="no")
    assert no["outcome"] == "cancelled" and n("SELECT count(*) AS n FROM measurements WHERE value1 = 82.4") == 1
    details = json.dumps([r["detail"] for r in owner_sql("SELECT detail FROM audit_log")])
    assert "82.4" not in details and '"value"' not in details  # ids and outcomes only, no health values


def test_undo_takes_back_the_newest_message(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    asked = call("log", text="undo that")
    assert asked["outcome"] == "needs_confirmation" and n("SELECT count(*) AS n FROM food_entries") == 1
    done = call("answer", question_id=asked["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "changed"
    assert n("SELECT count(*) AS n FROM food_entries") == 0 and n("SELECT count(*) AS n FROM measurements") == 0
    assert n("SELECT count(*) AS n FROM raw_inbox") == 1  # the message itself stays


def test_an_unclear_change_asks_which_entry_first(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    asked = call("log", text="delete the skyr")
    assert asked["outcome"] == "needs_choice" and "Which skyr" in asked["message"]
    preview = call("answer", question_id=asked["questions"][0]["question_id"], reply="the first")
    assert preview["outcome"] == "needs_confirmation" and n("SELECT count(*) AS n FROM food_entries") == 1
    done = call("answer", question_id=preview["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "changed" and n("SELECT count(*) AS n FROM food_entries") == 0


def test_a_project_is_created_only_from_a_project_line(call, fake):
    body = call("log", text="this is part of the garden test")
    assert body["outcome"] == "saved" and "project garden:" in body["message"]
    assert n("SELECT count(*) AS n FROM thoughts WHERE thought_type = 'project'") == 0
    body = call("log", text="project garden: planted tomatoes")
    assert n("SELECT count(*) AS n FROM thoughts WHERE thought_type = 'project'") == 1
    assert n("SELECT count(*) AS n FROM thought_links WHERE link_type = 'part_of'") == 1
