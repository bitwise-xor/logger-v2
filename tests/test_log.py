"""The free-text tools `log` and `answer`, through the MCP interface the AI app uses. The AI provider is a fake that
returns fixed parser answers, so these tests need no API key and show that the provider can be swapped."""
import copy
import json
import re
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from app import foodfacts, logbook, todos
from app.ai.provider import Usage
from app.mcp_app import build_app
from app.todoist import FakeTodoist
from conftest import owner_sql

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "log.example.test"}


def P(**kw):
    base = dict(intent="log", command_text=None, private=None, private_unsure=False, food=[], usual_meals=[], health=[],
                thoughts=[], todos=[])
    return {**base, **kw}


def F(food, amount, unit="g", when=None, drink=None, en=None):
    return dict(food=food, food_en=en or food, amount=amount, unit=unit, grams_per_unit=None, cooked=False, drink=drink,
                when=when, note=None)


def D(item, list_="shopping", store=None):
    return dict(item=item, list=list_, store=store)


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
    "50 g coconut": P(food=[F("coconut", 50, en="coconut meat")]),
    "100 g haferdrink": P(food=[F("haferdrink", 100, en="oat drink", drink=True)]),
    "30 g zzqfood": P(food=[F("zzqfood", 30)]),
    "todo: buy milk, cola, potatoes": P(todos=[D("Milk"), D("Cola"), D("Potatoes")]),
    "todo: buy chips": P(todos=[D("Chips")]),
    "todo: buy meat": P(todos=[D("meat")]),
    "todo: buy fruits": P(todos=[D("Fruits")]),
    "todo: renew passport": P(todos=[D("Renew passport", "tasks")]),
    "got the milk": P(intent="command", command_text="got the milk"),
    "remove potatoes from the list": P(intent="command", command_text="remove potatoes from the list"),
    "remove milk": P(intent="command", command_text="remove milk"),
    "untick the milk": P(intent="command", command_text="untick the milk"),
    "got the bananas": P(intent="command", command_text="got the bananas"),
    "what is on my shopping list": P(intent="command", command_text="what is on my shopping list"),
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
    "got the milk": lambda u: C(("todo", {"done": ["milk"]})),
    "remove potatoes from the list": lambda u: C(("todo", {"remove": ["potatoes from the list"]})),
    "remove milk": lambda u: C(("todo", {"remove": ["milk"]})),
    "untick the milk": lambda u: C(("todo", {"back": ["milk"]})),
    "got the bananas": lambda u: C(("todo", {"done": ["bananas"]})),
    "what is on my shopping list": lambda u: C(("find", {"todoist": "shopping"})),
    "delete the skyr": lambda u: C(unclear="Which skyr: the first or the second?"),
    "delete the skyr\n(Asked:": lambda u: C(("change", {"id": "f" + _id(u, r"f(\d+) [^\n]*Skyr natural"), "delete": True})),
}

PICKS = {  # the food pick call: the word -> (label of the entry to pick, catalogue name)
    "coconut": ("Nuts, coconut meat, raw", "Coconut meat, raw"),
    "haferdrink": ("Haferdrink (BioPrimo)", "Oat drink (BioPrimo)"),
    "Haferdrink": ("Haferdrink (BioPrimo)", "Oat drink (BioPrimo)"),
}

PLACES = {  # the placement call for new Todoist items
    "Potatoes": {"section": "Produce", "label": "aldi", "generic": False},
    "meat": {"section": "Meat & Seafood", "label": "aldi", "generic": False},
    "Fruits": {"section": "Produce", "label": "aldi", "generic": True},
    "Renew passport": {"section": "Paperwork", "label": None, "generic": False},
}

LABEL = dict(kcal=60, protein=1.2, fat=0.3, carbs=13, sugar_total=11, source="label")


class FakeProvider:
    def __init__(self):
        self.calls = []

    def json(self, system, user, schema, model, max_tokens=4000):
        if user.startswith("Word: "):  # picking a USDA or Open Food Facts entry
            word = user.split("\n", 1)[0][len("Word: "):]
            self.calls.append((word, model))
            label, name = PICKS.get(word, (None, None))
            line = next((l for l in user.splitlines() if label and re.match(r"[UO]\d+ ", l) and l.split(" ", 1)[1].startswith(label)), None)
            return {"pick": line.split(" ", 1)[0] if line else None, "name": name if line else None, "state": None,
                    "drink": word.lower() == "haferdrink"}, Usage(model, 600, 30)
        if user.startswith("Item: "):  # placing a new Todoist item
            item = user.split("\n", 1)[0][len("Item: "):]
            self.calls.append((item, model))
            return dict(PLACES[item]), Usage(model, 300, 20)
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


OAT = {"code": "2200263020656", "product_name": "Haferdrink", "brands": ["BioPrimo"], "countries_tags": ["en:germany"],
       "nutriments": {"energy-kcal_100g": 61, "proteins_100g": 1, "fat_100g": 2.3, "saturated-fat_100g": 0.3,
                      "carbohydrates_100g": 9, "sugars_100g": 4, "fiber_100g": 0.8}}


@pytest.fixture(autouse=True)
def off(monkeypatch):
    """A fake Open Food Facts: nothing found unless a test puts hits or products in."""
    f = SimpleNamespace(hits=[], products={})
    monkeypatch.setattr(foodfacts, "search", lambda q, n=8: list(f.hits))
    monkeypatch.setattr(foodfacts, "product", lambda code: f.products.get(code))
    return f


@pytest.fixture
def todo():
    S, L = todos.SHOPPING, todos.LISTS
    f = FakeTodoist(
        sections={S: [("s1", "Produce"), ("s2", "Dairy & Eggs"), ("s3", "Beverages"), ("s4", "Snacks & Sweets"),
                     ("s5", "Meat & Seafood")],
                  L: [("l1", "Paperwork"), ("l2", "To do Sometime")]},
        tasks=[dict(id="a1", content="Milk | Lait | Milch", project_id=S, section_id="s2", labels=["aldi"]),
               dict(id="a2", content="Oat Milk | Lait d'avoine | Hafermilch", project_id=S, section_id="s2", checked=True,
                    completed_at="2026-10-20T10:00:00Z"),
               dict(id="a3", content="Cola | Cola | Cola", project_id=S, section_id="s3", labels=["aldi"], checked=True,
                    completed_at="2026-10-21T10:00:00Z"),
               dict(id="a4", content="Kettle Chips | Chips | Chips", project_id=S, section_id="s4", checked=True,
                    completed_at="2026-10-22T10:00:00Z"),
               dict(id="a5", content="Tortilla Chips | Tortilla | Tortilla-Chips", project_id=S, section_id="s4", checked=True,
                    completed_at="2026-10-19T10:00:00Z"),
               dict(id="a6", content="Meat seasoning", project_id=S, section_id="s4")])
    todos.CLIENT = f
    todos.forget()
    yield f
    todos.CLIENT = None
    todos.forget()


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
    assert [m for _, m in fake.calls][:2] == ["claude-haiku-5-5", "claude-sonnet-5-5"]  # read once more before asking
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


def test_shopping_items_follow_v1s_rules(call, fake, todo):
    body = call("log", text="todo: buy milk, cola, potatoes")
    assert body["outcome"] == "saved", body["message"]
    assert "Already on your list: Milk" in body["message"] and "Unchecked: Cola" in body["message"]
    assert "Created: Potatoes (Produce, aldi)" in body["message"]
    assert not todo.tasks["a3"]["checked"] and todo.log[-1][0] == "add"
    assert [r["op"] for r in owner_sql("SELECT op FROM todo_actions ORDER BY id")] == ["exists", "reopened", "created"]


def test_undo_takes_back_what_a_message_did_in_todoist(call, fake, todo):
    call("log", text="todo: buy milk, cola, potatoes")
    n_calls = len(todo.log)
    asked = call("log", text="undo that")
    assert asked["outcome"] == "needs_confirmation" and "delete Potatoes" in asked["message"]
    assert len(todo.log) == n_calls  # the preview never touches Todoist
    done = call("answer", question_id=asked["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "changed" and todo.tasks["a3"]["checked"]
    assert not any(t["content"] == "Potatoes" for t in todo.tasks.values())
    assert n("SELECT count(*) AS n FROM todo_actions WHERE undone_at IS NULL AND op <> 'exists'") == 0


def test_several_close_items_become_a_question(call, fake, todo):
    body = call("log", text="todo: buy chips")
    assert body["outcome"] == "needs_choice" and "Kettle Chips" in body["message"] and "Tortilla Chips" in body["message"]
    done = call("answer", question_id=body["questions"][0]["question_id"], reply="tortilla")
    assert done["outcome"] == "saved" and not todo.tasks["a5"]["checked"] and todo.tasks["a4"]["checked"]


def test_a_generic_word_and_a_task(call, fake, todo):
    body = call("log", text="todo: buy fruits")
    assert "group of things" in body["message"] and not any(e[0] == "add" for e in todo.log)
    body = call("log", text="todo: renew passport")
    assert "Created: Renew passport (Paperwork)" in body["message"]
    assert any(t["content"] == "Renew passport" and t["section_id"] == "l1" for t in todo.tasks.values())


def test_items_wait_while_todoist_is_down_and_go_with_the_next_message(call, fake, todo):
    todo.down = True
    body = call("log", text="todo: buy milk, cola, potatoes")
    assert "waits" in body["message"] and n("SELECT count(*) AS n FROM todo_actions WHERE op = 'waiting'") == 3
    todo.down = False
    body = call("log", text="idea: a weekly food chart")
    assert "Sent to Todoist now" in body["message"] and "Created: Potatoes" in body["message"]
    assert n("SELECT count(*) AS n FROM todo_actions WHERE op = 'waiting' AND undone_at IS NULL") == 0


def test_ticking_off_is_straight_away_and_undo_puts_it_back(call, fake, todo):
    body = call("log", text="got the milk")
    assert body["outcome"] == "changed" and "Ticked off: Milk" in body["message"] and todo.tasks["a1"]["checked"]
    asked = call("log", text="undo that")
    assert "put Milk back on the list" in asked["message"]
    call("answer", question_id=asked["questions"][0]["question_id"], reply="yes")
    assert not todo.tasks["a1"]["checked"]


def test_the_shopping_list_can_be_looked_up(call, fake, todo):
    body = call("log", text="what is on my shopping list")
    assert body["outcome"] == "answered" and "Dairy & Eggs: Milk [aldi]" in body["message"]


def test_a_close_name_is_asked_and_new_creates_it(call, fake, todo):
    body = call("log", text="todo: buy meat")
    assert body["outcome"] == "needs_choice" and "Meat seasoning" in body["message"]  # not taken silently
    done = call("answer", question_id=body["questions"][0]["question_id"], reply="new")
    assert done["outcome"] == "saved" and "Created: Meat (Meat & Seafood, aldi)" in done["message"]


def test_remove_deletes_only_what_the_logger_added_and_back_unticks(call, fake, todo):
    call("log", text="todo: buy milk, cola, potatoes")
    body = call("log", text="remove potatoes from the list")  # added by the logger: deleted
    assert body["outcome"] == "changed" and "Deleted: Potatoes" in body["message"]
    assert not any(t["content"] == "Potatoes" for t in todo.tasks.values())
    body = call("log", text="remove milk")  # the user's own item: only ticked off
    assert "Ticked off: Milk" in body["message"] and todo.tasks["a1"]["checked"] and "a1" in todo.tasks
    body = call("log", text="untick the milk")
    assert "Back on the list: Milk" in body["message"] and not todo.tasks["a1"]["checked"]


def test_a_tick_off_that_matches_nothing_says_so(call, fake, todo):
    body = call("log", text="got the bananas")
    assert body["outcome"] == "rejected" and "not found" in body["message"] and body["message"].startswith("Nothing changed")
    steps = owner_sql("SELECT detail FROM audit_log WHERE tool = 'log' ORDER BY id DESC LIMIT 1")[0]["detail"]["steps"]
    assert steps[0]["items"] == {"done": ["bananas"]}  # the to-do names are kept, so a miss can be traced


def test_a_new_plain_food_is_offered_from_usda_and_yes_adds_it(call, fake, off):
    body = call("log", text="50 g coconut")
    assert body["outcome"] == "needs_choice" and "USDA has Nuts, coconut meat, raw: 354 kcal" in body["message"]
    done = call("answer", question_id=body["questions"][0]["question_id"], reply="yes")
    assert done["outcome"] == "saved", done["message"]
    f = owner_sql("SELECT id, source, source_note, carbs_include_fibre, aliases FROM foods WHERE name = 'Coconut meat, raw'")[0]
    assert f["source"] == "database" and "fdc 170169" in f["source_note"] and f["carbs_include_fibre"]
    assert "coconut" in f["aliases"]  # the user's word now finds it
    units = {r["unit"]: float(r["grams"]) for r in owner_sql("SELECT unit, grams FROM food_units WHERE food_id = %s", f["id"])}
    assert units["medium"] == 397 and units["piece"] == 45 and units["cup"] == 80  # USDA's own piece: 2" x 2" x 1/2"
    assert n("SELECT count(*) AS n FROM food_entries WHERE food_id = %s AND amount = 50", f["id"]) == 1


def test_a_german_product_comes_from_open_food_facts_with_its_barcode(call, fake, off):
    off.hits.append(OAT)
    off.products[OAT["code"]] = {**OAT, "nutriments": {**OAT["nutriments"], "salt_100g": 0.1}}
    body = call("log", text="100 g haferdrink")
    assert "Open Food Facts has Haferdrink (BioPrimo)" in body["message"]
    call("answer", question_id=body["questions"][0]["question_id"], reply="yes, add it")
    f = owner_sql("SELECT barcode, brand, sodium_mg, carbs_include_fibre FROM foods WHERE name = 'Oat drink (BioPrimo)'")[0]
    assert f["barcode"] == OAT["code"] and f["brand"] == "BioPrimo" and float(f["sodium_mg"]) == 40 and f["carbs_include_fibre"] is False


def test_a_barcode_is_looked_up_shown_and_then_known(call, fake, off):
    off.products[OAT["code"]] = OAT
    body = call("food_lookup", barcode="2200 2630 20656")
    assert body["outcome"] == "needs_confirmation" and "61 kcal" in body["message"]
    assert n("SELECT count(*) AS n FROM foods") == 0  # only after the yes
    call("answer", question_id=body["questions"][0]["question_id"], reply="yes")
    again = call("food_lookup", barcode=OAT["code"])
    assert "already has N" in again["message"]


def test_a_food_found_nowhere_keeps_the_plain_question(call, fake, off):
    body = call("log", text="30 g zzqfood")
    assert body["outcome"] == "needs_choice" and "Nothing in the catalogue fits" in body["message"]


def test_coconut_is_not_taken_for_coconut_oil_and_the_lookup_is_offered(call, fake, off):
    oil = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                       VALUES ('Coconut oil', 'g', 862, 0, 100, 0, 'database') RETURNING id""")[0]["id"]
    body = call("log", text="50 g coconut")
    assert body["outcome"] == "needs_choice" and n("SELECT count(*) AS n FROM food_entries") == 0
    assert f"N{oil} Coconut oil" in body["message"] and "USDA has Nuts, coconut meat, raw" in body["message"]
    call("answer", question_id=body["questions"][0]["question_id"], reply="yes")
    assert owner_sql("SELECT f.name FROM food_entries e JOIN foods f ON f.id = e.food_id")[0]["name"] == "Coconut meat, raw"


def test_the_same_brand_with_nearly_the_same_values_is_flagged(call, fake):
    old = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                       VALUES ('Mixed nuts, finest variation (Farmer Naturals)', 'g', 657, 19.3, 58.5, 10, 'label') RETURNING id""")[0]["id"]
    body = call("add_food", name="Nut variation, natural (Farmer naturals)", kcal=649, protein=17.5, fat=58, carbs=10.1, source="database")
    assert body["outcome"] == "needs_confirmation" and f"N{old}" in body["message"]
    other = call("add_food", name="Cashews, salted (Farmer Naturals)", kcal=600, protein=18, fat=48, carbs=26, source="database")
    assert f"N{old}" not in other["message"]  # same brand, other values: a different product
