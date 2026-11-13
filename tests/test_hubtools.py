"""The hub's tools (13.11.2026, hubtools.py): the service token that lets the home dashboard call the MCP endpoint, the
read-only summary of today and the four small actions, all through the MCP interface with that token. Every habit, food,
chore and sentence here is invented; the habits are added for a test and taken out after it."""
import json
import os
import re
from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from app import auth, mcp_app, todaypage, uiprefs
from app.mcp_app import build_app
from app.rules import BERLIN
from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import n
from test_webhealth import measure
from test_webtoday import day, eat, no_targets, raw  # noqa: F401 - no_targets: clean targets and usual meals

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Host": "log.example.test"}
TOKEN = "hub-test-token-0123456789abcdef-0123456789"
APP = "https://app.example.test/web"
KEYS = {"day", "generated_at", "links", "food", "water", "habits", "habit_history", "health", "review", "questions", "chores"}
# (name, label, group, times a day); the group "behaviour" makes the last one a habit to avoid (habitaim.py)
HABITS = (("hub_stretch", "Hub stretching", "practice", 1), ("hub_walk", "Hub walk", "practice", 2), ("hub_soda", "Hub soda", "behaviour", 1))
SECRETS = ("violet umbrella", "copper lantern", "marble staircase", "paper kite", "quiet harbour", "silver thimble")


def rpc(c, method, params=None, token=TOKEN, host="log.example.test"):
    headers = {**HDR, "Host": host, **({"Authorization": "Bearer " + token} if token is not None else {})}
    return c.post("/mcp-testpath", headers=headers, content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}))


def client():
    return TestClient(build_app(), base_url="http://log.example.test")


@pytest.fixture
def hub(monkeypatch):
    """Call a tool as the hub does: with the service token, no sign-in. A reply that is an error fails the test."""
    monkeypatch.setattr(auth, "HUB_TOKEN", TOKEN)
    with client() as c:
        def call(tool, **args):
            r = rpc(c, "tools/call", {"name": tool, "arguments": args})
            assert r.status_code == 200, r.text
            res = r.json()["result"]
            assert res.get("isError") in (False, None), res
            return res.get("structuredContent") or json.loads(res["content"][0]["text"])
        yield call


@pytest.fixture
def habits():
    """Only the invented habits are tracked during the test; the real list is as it was after it."""
    keep = owner_sql("SELECT name, on_checklist FROM log_types WHERE kind = 'event'")
    owner_sql("UPDATE log_types SET on_checklist = false WHERE kind = 'event'")
    for i, (name, label, group, target) in enumerate(HABITS):
        owner_sql("""INSERT INTO log_types (name, label, kind, category, aliases, on_checklist, daily_target, active, sort_order)
                     VALUES (%s, %s, 'event', %s, %s, true, %s, true, %s)""", name, label, group, [label.lower()], target, 900 + i)
    yield
    names = [h[0] for h in HABITS]
    owner_sql("DELETE FROM events WHERE type_id IN (SELECT id FROM log_types WHERE name = ANY(%s))", names)
    owner_sql("DELETE FROM log_types WHERE name = ANY(%s)", names)
    for r in keep:
        owner_sql("UPDATE log_types SET on_checklist = %s WHERE name = %s", r["on_checklist"], r["name"])


def tick(name, days_ago=0, hm="08:00", timed=True):
    """A tick put in by hand, on the logging day that many days back."""
    d = day() - timedelta(days=days_ago)
    ts = datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), BERLIN)
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                 SELECT %s, id, %s, %s, %s, 'test' FROM log_types WHERE name = %s""", raw(), d, ts, timed, name)


def ticks(name):
    return n("SELECT count(*) AS n FROM events e JOIN log_types t ON t.id = e.type_id WHERE t.name = %s", name)


def habit(summary, name):
    return next(h for h in summary["habits"] if h["name"] == name)


def chores(names, zone="Hub room"):
    z = owner_sql("INSERT INTO home_zones (name, short, sort_order) VALUES (%s, 'Hub', 1) RETURNING id", zone)[0]["id"]
    return z, [owner_sql("INSERT INTO home_chores (name, zone_id, interval_days, sort_order) VALUES (%s, %s, 7, %s) RETURNING id", name, z, i)[0]["id"]
               for i, name in enumerate(names)]


# ---- the service token ----------------------------------------------------------------------------------

def test_the_hub_token_opens_the_connector_without_a_stored_token(monkeypatch):
    monkeypatch.setattr(auth, "HUB_TOKEN", TOKEN)
    with client() as c:
        r = rpc(c, "tools/list")
        assert r.status_code == 200
        assert {"today_summary", "tick_habit", "untick_habit", "add_water", "set_checkin", "log"} <= {t["name"] for t in r.json()["result"]["tools"]}
    assert n("SELECT count(*) AS n FROM oauth_tokens") == 0
    got = auth.hub_access(TOKEN)
    assert (got.client_id, got.scopes, got.expires_at, got.resource) == ("hub", [auth.SCOPE], None, None)


def test_another_token_or_none_is_refused_while_the_hub_token_is_set(monkeypatch):
    monkeypatch.setattr(auth, "HUB_TOKEN", TOKEN)
    with client() as c:
        for wrong in (TOKEN + "x", TOKEN[:-1], TOKEN.upper(), "", None):
            assert rpc(c, "tools/list", token=wrong).status_code == 401, wrong


def test_without_a_hub_token_no_token_is_accepted(monkeypatch):
    monkeypatch.setattr(auth, "HUB_TOKEN", "")  # as config.py reads it when the variable is not set
    assert auth.hub_access("") is None and auth.hub_access(TOKEN) is None
    with client() as c:
        for tried in (TOKEN, "", "anything-at-all-that-is-long-enough-0123456789"):
            assert rpc(c, "tools/list", token=tried).status_code == 401, tried


def test_a_hub_token_shorter_than_32_characters_is_off(monkeypatch):
    short = "a" * 31
    monkeypatch.setattr(auth, "HUB_TOKEN", short)
    with client() as c:
        assert rpc(c, "tools/list", token=short).status_code == 401
    monkeypatch.setattr(auth, "HUB_TOKEN", short + "a")  # 32 are enough
    with client() as c:
        assert rpc(c, "tools/list", token=short + "a").status_code == 200


def test_the_sign_in_tokens_work_as_before_next_to_the_hub_token(monkeypatch, bearer):
    monkeypatch.setattr(auth, "HUB_TOKEN", TOKEN)
    with client() as c:
        assert c.post("/mcp-testpath", headers={**HDR, **bearer}, content=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})).status_code == 200


def test_the_compose_file_lets_the_hub_call_the_container_by_its_name(monkeypatch):
    compose = open(os.path.join(os.path.dirname(__file__), "..", "compose.yaml")).read()
    assert "HUB_TOKEN: ${HUB_TOKEN:-}" in compose
    value = re.search(r'^\s*ALLOWED_HOSTS:\s*"([^"]+)"', compose, re.M)[1]
    hosts = [h.strip() for h in value.split(",") if h.strip()]  # as config.py reads it
    assert len(hosts) == 2 and "logger-v2-core:*" in hosts
    monkeypatch.setattr(mcp_app, "EXTRA_ALLOWED_HOSTS", hosts)
    monkeypatch.setattr(auth, "HUB_TOKEN", TOKEN)
    with client() as c:
        assert rpc(c, "tools/list", host="logger-v2-core:8000").status_code == 200
        assert rpc(c, "tools/list", host="logger-v2-core.example.org:8000").status_code == 421
        assert rpc(c, "tools/list", host="other-container:8000").status_code == 421


# ---- the hub token may call the hub's tools only ----------------------------------------------------------

def result(c, tool, token, **args):
    r = rpc(c, "tools/call", {"name": tool, "arguments": args}, token=token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def body(res):
    assert res.get("isError") in (False, None), res
    return res.get("structuredContent") or json.loads(res["content"][0]["text"])


def question():
    owner_sql("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES ('phub2', 'log_repeat', %s, 'same message', now() + interval '1 day')",
              json.dumps({"text": "weight 90", "now": datetime.now(BERLIN).isoformat()}))
    return "phub2"


FOOD = dict(name="Hub test crispbread", kcal=350, protein=10, fat=2, carbs=70, source="estimate")
TABLES = ("raw_inbox", "measurements", "events", "foods", "food_entries", "thoughts", "private.entries", "private.incoming",
          "idempotency_keys", "audit_log")


def test_the_hub_token_cannot_call_the_other_tools_and_nothing_happens(monkeypatch, fake, caplog):
    monkeypatch.setattr(auth, "HUB_TOKEN", TOKEN)
    qid = question()
    calls = (("log", dict(text="weight 90", idempotency_key="hub-x1")), ("answer", dict(question_id=qid, reply="yes")),
             ("add_food", FOOD), ("food_words", dict(food_id=1, words=["crispbread"])), ("food_lookup", dict(name="crispbread")),
             ("lab_report", dict(text="Hemoglobin 13,8 g/dl")), ("record_weight", dict(kg=82.4)), ("recent_weights", {}), ("undo", dict(receipt_id="m1")))
    with client() as c, caplog.at_level("WARNING", logger="logger.tools"):
        for tool, args in calls:
            res = result(c, tool, TOKEN, **args)
            said = res["content"][0]["text"]
            assert res["isError"] is True and f"{tool} is not available to the hub" in said and "today_summary" in said, tool
            assert "weight 90" not in said and "crispbread" not in said
    assert fake.calls == []
    for table in TABLES:  # one of ten fixed names
        assert n(f"SELECT count(*) AS n FROM {table}") == 0, table
    assert owner_sql("SELECT resolved_at FROM pending") == [{"resolved_at": None}]
    assert [r.getMessage() for r in caplog.records][:2] == ["tool log refused: called with the hub's token", "tool answer refused: called with the hub's token"]


def test_a_sign_in_token_still_calls_every_tool_the_hub_tools_too(monkeypatch, bearer, fake, habits):
    monkeypatch.setattr(auth, "HUB_TOKEN", TOKEN)
    owner = bearer["Authorization"][len("Bearer "):]
    with client() as c:
        saved = body(result(c, "log", owner, text="weight 90", idempotency_key="hub-x2"))
        assert saved["outcome"] == "saved" and n("SELECT count(*) AS n FROM measurements") == 1
        again = body(result(c, "answer", owner, question_id=question(), reply="yes"))
        assert again["outcome"] in ("saved", "needs_confirmation") and owner_sql("SELECT resolved_at FROM pending WHERE id = 'phub2'")[0]["resolved_at"]
        assert body(result(c, "add_food", owner, **FOOD))["outcome"] == "saved" and n("SELECT count(*) AS n FROM foods WHERE name = %s", FOOD["name"]) == 1
        assert body(result(c, "record_weight", owner, kg=82.4, at="2026-10-12T08:30"))["outcome"] == "saved"
        assert set(body(result(c, "today_summary", owner))) == KEYS
        assert body(result(c, "tick_habit", owner, name="hub_stretch"))["habit"]["done"] == 1
        assert body(result(c, "untick_habit", owner, name="hub_stretch"))["habit"]["done"] == 0
        assert body(result(c, "add_water", owner, ml=200))["outcome"] == "saved"
        assert body(result(c, "set_checkin", owner, mood=3))["outcome"] == "saved"
    assert {r["client"] for r in owner_sql("SELECT client FROM audit_log WHERE tool = 'log'")} == {"mcp", "web"}  # the chat's log, the buttons' taps


# ---- today_summary --------------------------------------------------------------------------------------

def test_an_empty_day_has_every_key_and_nothing_in_it(hub, habits):
    s = hub("today_summary")
    assert set(s) == KEYS and s["day"] == day().isoformat()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", s["generated_at"])
    assert s["links"] == {"today": APP + "/", "habits": APP + "/habits", "health": APP + "/health", "food": APP + "/food", "home": APP + "/home"}
    assert s["food"] == {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0, "targets": {"kcal": None, "protein": None, "fat": None, "carbs": None}}
    assert s["water"] == {"ml": 0, "target_ml": None}
    assert s["habits"] == [
        {"name": "hub_stretch", "label": "Hub stretching", "done": 0, "target": 1, "complete": False, "avoid": False, "usual_hour": None,
         "link": APP + "/habits#hab-hub_stretch"},
        {"name": "hub_walk", "label": "Hub walk", "done": 0, "target": 2, "complete": False, "avoid": False, "usual_hour": None,
         "link": APP + "/habits#hab-hub_walk"},
        {"name": "hub_soda", "label": "Hub soda", "done": 0, "target": 1, "complete": False, "avoid": True, "usual_hour": None,
         "link": APP + "/habits#hab-hub_soda"}]
    assert s["habit_history"]["days"] == [(day() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    assert [(r["name"], r["label"], r["done"]) for r in s["habit_history"]["rows"]] == [
        ("hub_stretch", "Hub stretching", [False] * 7), ("hub_walk", "Hub walk", [False] * 7),
        ("hub_soda", "Hub soda", [True] * 7)]  # a habit to avoid: a day without it is a good day
    assert s["health"] == {"weight_kg": None, "bp_morning": None, "bp_evening": None, "blood_sugar": None, "wake": None, "mood": None, "energy": None}
    assert s["review"] == {"count": 0, "items": []}
    assert s["questions"] == {"count": 0, "not_read": 0, "link": APP + "/"}
    assert s["chores"] == {"count": 0, "items": [], "link": APP + "/home"}


def test_targets_show_before_anything_is_eaten(hub):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs, water_ml) VALUES ('2020-01-01', 2100, 110, 70, 220, 2500)")
    s = hub("today_summary")
    assert s["food"] == {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0, "targets": {"kcal": 2100, "protein": 110, "fat": 70, "carbs": 220}}
    assert s["water"] == {"ml": 0, "target_ml": 2500}


def test_a_day_with_food_water_health_values_and_a_ticked_habit(hub, habits):
    d = day()
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs, water_ml) VALUES ('2020-01-01', 2000, 100, 70, 200, 2500)")
    eat("Hub test oats", 400, 20.5, 10, 60, 100)
    eat("Hub test tea", 2, 0, 0, 0.5, amount=300, drink=True)
    measure("weight", d, "07:30", 82.4)
    measure("bp", d, "07:35", 121, 79, "morning")
    measure("bp", d, "21:00", 118, 76, "evening")
    measure("blood_sugar", d, "07:40", 92, None, "fasted")
    measure("mood", d, "20:00", 4)
    measure("energy", d, "20:00", 2)
    tick("hub_stretch")
    tick("hub_walk")
    s = hub("today_summary")
    assert s["food"] == {"kcal": 402, "protein": 20.5, "fat": 10, "carbs": 60.5, "targets": {"kcal": 2000, "protein": 100, "fat": 70, "carbs": 200}}
    assert s["water"] == {"ml": 300, "target_ml": 2500}
    assert s["health"] == {"weight_kg": 82.4, "bp_morning": [121, 79], "bp_evening": [118, 76], "blood_sugar": 92, "wake": None, "mood": 4, "energy": 2}
    assert (habit(s, "hub_stretch")["done"], habit(s, "hub_stretch")["complete"]) == (1, True)
    assert (habit(s, "hub_walk")["done"], habit(s, "hub_walk")["target"], habit(s, "hub_walk")["complete"]) == (1, 2, False)
    assert '"402.0"' not in json.dumps(s) and "Decimal" not in json.dumps(s)


def test_the_wake_up_time_stays_when_the_page_has_moved_on_to_the_next_sleep(hub, monkeypatch):
    d = day()
    at = datetime.combine(d, datetime.strptime("06:40", "%H:%M").time(), BERLIN)
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                 SELECT %s, id, %s, %s, true, 'test' FROM log_types WHERE name = 'wake'""", raw(), d, at)
    monkeypatch.setattr(todaypage, "clock", lambda: at + timedelta(hours=3))
    assert hub("today_summary")["health"]["wake"] == "06:40"
    monkeypatch.setattr(todaypage, "clock", lambda: at + timedelta(hours=13))  # the Health card's field is empty now, "last 06:40" beside it
    assert hub("today_summary")["health"]["wake"] == "06:40"


def test_habits_follow_the_today_page_hidden_ones_and_order(hub, habits):
    uiprefs.save(["habit:hub_walk"], {"habit": ["hub_soda", "hub_stretch"]})
    s = hub("today_summary")
    assert [h["name"] for h in s["habits"]] == ["hub_soda", "hub_stretch"]
    assert [r["name"] for r in s["habit_history"]["rows"]] == ["hub_soda", "hub_stretch"]


def test_usual_hour_needs_five_ticks_and_is_their_median(hub, habits):
    for i, hm in enumerate(("07:00", "08:00", "08:30", "09:00"), 1):
        tick("hub_stretch", i, hm)
    for i, hm in enumerate(("21:00", "21:30", "22:15", "06:00", "22:00"), 1):
        tick("hub_soda", i, hm)
    for i in range(1, 6):
        tick("hub_walk", 40 + i, "12:00")          # older than 30 days: not counted
        tick("hub_walk", i, "12:00", timed=False)  # ticked later for a past day: no time of its own
    s = hub("today_summary")
    assert habit(s, "hub_stretch")["usual_hour"] is None  # four ticks
    assert habit(s, "hub_soda")["usual_hour"] == 21.5
    assert habit(s, "hub_walk")["usual_hour"] is None
    tick("hub_stretch", 5, "08:30")
    assert habit(hub("today_summary"), "hub_stretch")["usual_hour"] == 8.5


def test_habit_history_is_seven_days_with_a_day_without_it_good_for_a_habit_to_avoid(hub, habits):
    tick("hub_stretch", 1)
    tick("hub_stretch", 3)
    tick("hub_stretch", 7)   # before the seven days
    tick("hub_walk", 1, "09:00")
    tick("hub_walk", 1, "18:00")
    tick("hub_walk", 2)      # once of twice a day: not done
    tick("hub_soda", 2, "15:00")
    tick("hub_soda", 0, "15:00")
    rows = {r["name"]: r["done"] for r in hub("today_summary")["habit_history"]["rows"]}
    #                              6      5      4      3      2      1    today
    assert rows["hub_stretch"] == [False, False, False, True, False, True, False]
    assert rows["hub_walk"] == [False, False, False, False, False, True, False]
    assert rows["hub_soda"] == [True, True, True, True, False, True, False]


def test_review_items_have_the_page_labels_and_at_most_five_are_listed(hub):
    eat("Hub test soup", 50, 2, amount=120, review=True)
    mid = measure("weight", day(), "07:30", 82.4)
    owner_sql("UPDATE measurements SET needs_review = true, review_reason = 'far from the last value' WHERE id = %s", mid)
    s = hub("today_summary")["review"]
    assert s["count"] == 2 and {i["kind"] for i in s["items"]} == {"food", "measurement"}
    food = next(i for i in s["items"] if i["kind"] == "food")
    assert food["what"] == "Hub test soup 120 g" and food["reason"] == "odd value" and re.fullmatch(r"f\d+", food["ref"]) and food["link"] == APP + "/"
    value = next(i for i in s["items"] if i["kind"] == "measurement")
    assert value["what"] == "Weight 82.4 kg" and value["reason"] == "far from the last value" and re.fullmatch(r"m\d+", value["ref"])
    for i in range(5):
        eat(f"Hub test snack {i}", 10, 1, review=True)
    s = hub("today_summary")["review"]
    assert s["count"] == 7 and len(s["items"]) == 5


def test_no_private_gratitude_thought_or_typed_text_leaves(hub, habits):
    private, grateful, thought, typed, unread, asked = SECRETS
    owner_sql("""INSERT INTO private.entries (channel, label, content, summary, thought_type, area, tags, event_at) VALUES
                 ('test', 'private', %s, %s, 'journal', 'personal', '{}', now()),
                 ('test', 'gratitude', %s, %s, 'journal', 'personal', '{gratitude}', now())""",
              f"private: the {private}", f"the {private}", f"gratitude: the {grateful}", f"the {grateful}")
    owner_sql("""INSERT INTO thoughts (raw_id, original_text, summary, thought_type, area, needs_review, review_reason)
                 VALUES (%s, %s, %s, 'idea', 'personal', true, 'nothing extracted')""", raw(), f"about the {thought}", f"The {thought}")
    owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('web', %s)", f"note to self: the {typed}")
    owner_sql("INSERT INTO private.incoming (channel, content) VALUES ('web', %s)", f"not read yet: the {unread}")
    owner_sql("""INSERT INTO pending (id, tool, payload, reason, expires_at)
                 VALUES ('phub1', 'log_confirm', %s, %s, now() + interval '1 day')""", json.dumps({"text": f"the {asked}"}), f"Save the {asked}?")
    s = hub("today_summary")
    blob = json.dumps(s).lower()
    for secret in SECRETS:
        assert secret not in blob, secret
    assert s["questions"] == {"count": 1, "not_read": 1, "link": APP + "/"}
    assert s["review"]["count"] == 1 and s["review"]["items"][0]["kind"] == "thought"
    assert s["review"]["items"][0]["what"] == "Thought (idea)" and s["review"]["items"][0]["reason"] == "nothing extracted"


def test_chores_are_the_home_page_now_list(hub):
    names = [f"Hub chore {c}" for c in "ABCDEFG"]
    z, ids = chores(names)
    owner_sql("""INSERT INTO home_done (chore_id, zone_id, done_at, day, source) VALUES
                 (%s, %s, now() - interval '70 days', log_day(now()) - 70, 'web'), (%s, %s, now(), log_day(now()), 'web')""",
              ids[3], z, ids[0], z)                                                    # D: ten times its interval ago; A: done today
    owner_sql("INSERT INTO home_skips (chore_id, zone_id, day) VALUES (%s, %s, log_day(now()))", ids[1], z)  # B: "Not today"
    s = hub("today_summary")["chores"]
    assert s["count"] == 6 and s["link"] == APP + "/home" and len(s["items"]) == 5
    assert s["items"][0] == {"name": "Hub chore D", "zone": "Hub room", "link": f"{APP}/home/chore?id={ids[3]}"}
    assert [i["name"] for i in s["items"][1:]] == ["Hub chore C", "Hub chore E", "Hub chore F", "Hub chore G"]  # never done, then A (not listed)


# ---- the actions ----------------------------------------------------------------------------------------

def test_tick_and_untick_a_habit_as_a_tap_on_today(hub, habits, fake):
    out = hub("tick_habit", name="hub_stretch", idempotency_key="hub-t1")
    assert out == {"outcome": "saved", "head": "Saved.", "link": APP + "/",
                   "habit": {"name": "hub_stretch", "done": 1, "target": 1, "complete": True}}  # nothing else: no receipt lines, no ids
    assert ticks("hub_stretch") == 1 and fake.calls == []
    assert owner_sql("SELECT content, channel FROM raw_inbox") == [{"content": "tick hub_stretch", "channel": "web"}]
    assert owner_sql("SELECT tool, outcome, client FROM audit_log") == [{"tool": "log", "outcome": "saved", "client": "web"}]
    assert habit(hub("today_summary"), "hub_stretch")["complete"] is True
    off = hub("untick_habit", name="hub_stretch")
    assert (off["outcome"], off["head"]) == ("changed", "Removed the Hub stretching tick.")
    assert off["habit"] == {"name": "hub_stretch", "done": 0, "target": 1, "complete": False} and ticks("hub_stretch") == 0
    assert owner_sql("SELECT tool, outcome, client FROM audit_log WHERE tool = 'web_change'") == [
        {"tool": "web_change", "outcome": "changed", "client": "web"}]
    again = hub("untick_habit", name="hub_stretch")  # nothing left to take off
    assert again["outcome"] == "unchanged" and "no tick from today" in again["reason"] and again["head"] == "Nothing changed."


def test_a_tick_sent_twice_with_one_key_counts_once(hub, habits, fake):
    hub("tick_habit", name="hub_walk", idempotency_key="hub-t2")
    again = hub("tick_habit", name="hub_walk", idempotency_key="hub-t2")
    assert again["outcome"] == "saved" and again["replayed"] is True and again["head"] == "Already saved earlier (same message)."
    assert ticks("hub_walk") == 1 and again["habit"] == {"name": "hub_walk", "done": 1, "target": 2, "complete": False}
    more = hub("tick_habit", name="hub_walk", idempotency_key="hub-t3")  # the second of two a day: a new tap, no "log it again?"
    assert more["habit"] == {"name": "hub_walk", "done": 2, "target": 2, "complete": True} and n("SELECT count(*) AS n FROM pending") == 0


def test_untick_takes_only_the_last_tick_of_today(hub, habits, fake):
    tick("hub_walk", 1)
    hub("tick_habit", name="hub_walk")
    hub("tick_habit", name="hub_walk")
    assert hub("untick_habit", name="hub_walk")["habit"]["done"] == 1
    assert hub("untick_habit", name="hub_walk")["habit"]["done"] == 0
    assert hub("untick_habit", name="hub_walk")["outcome"] == "unchanged" and ticks("hub_walk") == 1  # yesterday's stays


def test_a_habit_that_is_not_tracked_is_refused_as_data(hub, habits, fake):
    for tool in ("tick_habit", "untick_habit"):
        out = hub(tool, name="hub_no_such_habit")
        assert (out["outcome"], out["head"], out["habit"]) == ("rejected", "Nothing saved.", None)
        assert out["reason"] == "That habit is not on the checklist." and out["link"] == APP + "/"
    assert n("SELECT count(*) AS n FROM events") == 0 and n("SELECT count(*) AS n FROM raw_inbox") == 0


def test_water_a_glass_or_another_amount(hub, fake):
    owner_sql("INSERT INTO food_targets (valid_from, water_ml) VALUES ('2020-01-01', 2000)")
    out = hub("add_water", idempotency_key="hub-w1")
    assert (out["outcome"], out["head"], out["link"]) == ("saved", "Saved.", APP + "/") and fake.calls == []
    assert hub("add_water", idempotency_key="hub-w1")["replayed"] is True  # the same tap again
    hub("add_water", ml=500, idempotency_key="hub-w2")
    hub("add_water", ml=50)
    rows = owner_sql("SELECT e.amount, e.is_drink, f.name FROM food_entries e JOIN foods f ON f.id = e.food_id ORDER BY e.id")
    assert [(float(r["amount"]), r["is_drink"], r["name"]) for r in rows] == [(250, True, "Water"), (500, True, "Water"), (50, True, "Water")]
    assert [r["content"] for r in owner_sql("SELECT content FROM raw_inbox WHERE channel = 'web' ORDER BY id")] == [
        "Water 250 ml (water bar)", "Water 500 ml (water bar)", "Water 50 ml (water bar)"]
    assert {(r["tool"], r["outcome"], r["client"]) for r in owner_sql("SELECT tool, outcome, client FROM audit_log")} == {
        ("web_change", "changed", "web"), ("log", "saved", "web"), ("log", "replayed", "web")}
    assert hub("today_summary")["water"] == {"ml": 800, "target_ml": 2000}


def test_water_outside_50_to_2000_ml_is_refused(hub, fake):
    for ml in (0, 49, 2001, -250):
        out = hub("add_water", ml=ml)
        assert (out["outcome"], out["head"]) == ("rejected", "Nothing saved.") and "50 to 2000" in out["reason"], ml
    assert n("SELECT count(*) AS n FROM food_entries") == 0 and n("SELECT count(*) AS n FROM foods") == 0


def test_check_in_one_value_or_both_and_once_per_key(hub, fake):
    out = hub("set_checkin", mood=4, energy=2, idempotency_key="hub-c1")
    assert (out["outcome"], out["head"], out["link"]) == ("saved", "Saved.", APP + "/") and fake.calls == []
    saved = "SELECT t.name, m.value1 FROM measurements m JOIN log_types t ON t.id = m.type_id ORDER BY m.id"
    assert [(r["name"], float(r["value1"])) for r in owner_sql(saved)] == [("mood", 4.0), ("energy", 2.0)]
    assert [r["content"] for r in owner_sql("SELECT content FROM raw_inbox WHERE channel = 'web' ORDER BY id")] == ["mood 4", "energy 2"]
    again = hub("set_checkin", mood=4, energy=2, idempotency_key="hub-c1")
    assert again["replayed"] is True and n("SELECT count(*) AS n FROM measurements") == 2
    health = hub("today_summary")["health"]
    assert (health["mood"], health["energy"]) == (4, 2)
    hub("set_checkin", energy=5)  # one alone; the latest of the day counts
    assert hub("today_summary")["health"]["energy"] == 5 and n("SELECT count(*) AS n FROM measurements") == 3


def test_a_check_in_outside_one_to_five_saves_nothing(hub, fake):
    for args in (dict(mood=7), dict(mood=3, energy=0), dict(mood=6, energy=4), dict(energy=-1)):
        out = hub("set_checkin", **args)
        assert (out["outcome"], out["head"], out["reason"]) == ("rejected", "Nothing saved.", "Pick 1 to 5."), args
    none = hub("set_checkin")
    assert none["outcome"] == "rejected" and "mood, energy or both" in none["reason"]
    assert n("SELECT count(*) AS n FROM measurements") == 0 and n("SELECT count(*) AS n FROM raw_inbox") == 0
