"""Coaching (web item 52, 04.11.2026; mockup https://claude.ai/artifact/4z4HLimQeLi2jo4Z6ZX3ND): the Coach card on Today and the
Week review card, what the coach reads (and never reads: thoughts, private and gratitude entries), the model and the answer's form,
the kept coachings on the Coach page, "working", "failed" and "cut off", the daily limit, the goals in Settings > Coach, and the
cost on the Usage page. The AI is faked; every value is invented."""
import html
import json
import re
from datetime import timedelta

import pytest

from app import coach, logbook, uiprefs
from app.ai.provider import ProviderError, Usage
from conftest import owner_sql
from test_log import fake  # noqa: F401 - the logger's own fake AI, for a private entry
from test_web import act, n, web  # noqa: F401
from test_webhealth import measure
from test_webtoday import day, eat, no_targets  # noqa: F401

DAY_BODY = {"went_well": ["Protein reached 112 g."], "improve": ["Carbs are high from the rolls."],
            "next_meal": {"title": "Salmon 150 g with broccoli", "why": "Fits what is left.", "kcal": 380, "protein": 34, "carbs": 6, "fat": 25},
            "gaps": ["No bed time for last night."], "doctor": ""}
WEEK_BODY = {"targets": ["Calories 1,890 a day on average."], "trends": ["Weight 82.4 to 81.9 kg."], "habits": ["Stretching 6 of 7 days."],
             "best_day": "Best: Thursday.", "hardest_day": "Hardest: Saturday.", "focus": ["Protein at breakfast."], "doctor": ""}


class FakeCoach:
    def __init__(self):
        self.calls, self.fail = [], False

    def json(self, system, user, schema, model, max_tokens=4000):
        self.calls.append({"system": system, "user": user, "schema": schema, "model": model, "max_tokens": max_tokens})
        if self.fail:
            raise ProviderError("the AI service did not answer")
        body = DAY_BODY if "went_well" in schema["properties"] else WEEK_BODY
        return json.loads(json.dumps(body)), Usage(model, 6000, 900, seconds=12.5)


@pytest.fixture
def ai(monkeypatch):
    f = FakeCoach()
    monkeypatch.setattr(coach, "PROVIDER", f)
    monkeypatch.setattr(coach, "BACKGROUND", False)
    return f


def coach_me(web, d=None, kind="day", end=None):
    d = d or day()
    return act(web, "/web/coach/start", kind=kind, start=d.isoformat(), end=(end or d).isoformat(), back=f"/web/?day={d.isoformat()}", key=f"k{n('SELECT count(*) AS n FROM coachings')}")


def sent(ai) -> dict:
    return json.loads(ai.calls[-1]["user"].split("The data:\n", 1)[1])


# ---- the day coaching ------------------------------------------------------------------------------

def test_today_has_a_coach_card_and_coach_me_saves_a_coaching(web, ai):
    page = web[0].get("/web/").text
    assert "Coach</span>" in page and ">Coach me<" in page and "about 30 seconds · about 6 cents" in page
    assert 'href="/web/coach">All coachings' in page and 'href="/web/settings?tab=coach">Your goals' in page
    out = coach_me(web)
    assert "The coach is reading your day" in out.text
    page = web[0].get("/web/").text
    assert "What went well" in page and "Protein reached 112 g." in page and "Salmon 150 g with broccoli" in page
    assert "380 kcal · 34 g protein · 6 g carbs · 25 g fat" in page and "Missing in the log" in page and ">Coach me again<" in page
    assert re.search(r"Claude Opus · \$0\.0\d · \d\d\.\d\d\. \d\d:\d\d", page) and "not medical advice" in page
    row = owner_sql("SELECT kind, status, model, usd, body FROM coachings")[0]
    assert row["kind"] == "day" and row["status"] == "done" and row["model"] == "claude-opus-5-5" and float(row["usd"]) > 0
    assert ai.calls[0]["model"] == "claude-opus-5-5" and ai.calls[0]["max_tokens"] == coach.MAX_TOKENS and "not a doctor" in ai.calls[0]["system"]


def test_the_coach_reads_the_day_and_never_thoughts_or_private_entries(web, ai, fake):
    owner_sql("INSERT INTO food_targets (valid_from, kcal, protein, fat, carbs) VALUES ('2020-01-01', 2000, 120, 70, 200)")
    eat("Test skyr", 120, 22, 1, 8, 250)
    measure("weight", day(), "07:40", 82.4)
    measure("bp", day(), "07:45", 128, 82, "morning")
    logbook.log("grateful for a quiet morning")  # a private gratitude entry with a thought: never sent
    logbook.log("idea: a weekly food chart")  # a thought: never sent (the owner's answer)
    coach_me(web)
    data = sent(ai)
    text = json.dumps(data)
    assert data["kind"] == "day" and data["day"]["not_over_yet"] is True and data["day"]["time_now"]
    assert data["targets"] == {"kcal": 2000, "protein_g": 120, "fat_g": 70, "carbs_g": 200}
    assert data["day"]["food"][0]["food"] == "Test skyr" and data["day"]["kcal"] == 120
    assert "weight 82.4 kg 07:40" in data["day"]["values"] and any(v.startswith("bp 128/82") for v in data["day"]["values"])
    assert len(data["days_before"]) == 7 and any("Test skyr" in f for f in data["frequent_foods"])
    assert "quiet morning" not in text.lower() and "weekly food chart" not in text.lower() and data["goals"] == "(none written)"


def test_an_earlier_day_can_be_coached_and_shows_its_own_card(web, ai):
    y = day() - timedelta(days=1)
    coach_me(web, y)
    assert sent(ai)["day"]["not_over_yet"] is False
    assert "Protein reached 112 g." in web[0].get(f"/web/?day={y.isoformat()}").text
    assert "Protein reached 112 g." not in web[0].get("/web/").text  # today has its own card


def test_coach_me_again_keeps_the_older_coaching(web, ai):
    coach_me(web)
    coach_me(web)
    assert n("SELECT count(*) AS n FROM coachings WHERE status = 'done'") == 2 and len(ai.calls) == 2
    page = web[0].get("/web/coach").text
    assert page.count('<details class="coitem">') == 2 and "This month: 2 coachings" in page


# ---- working, failed, cut off, the limit -----------------------------------------------------------------

def test_while_the_coach_reads_the_card_says_so_and_the_status_is_asked(web, ai):
    cid = owner_sql("INSERT INTO coachings (kind, start_day, end_day) VALUES ('day', %s, %s) RETURNING id", day(), day())[0]["id"]
    page = web[0].get("/web/").text
    assert f'data-coach-wait="{cid}"' in page and "Coaching… about 30 seconds" in page and ">Coach me<" not in page
    assert web[0].get(f"/web/coach/status?id={cid}").json() == {"status": "working"}
    assert "already on it" in coach_me(web).text and len(ai.calls) == 0
    owner_sql("UPDATE coachings SET created_at = now() - interval '11 minutes'")  # a restart cut it off
    page = web[0].get("/web/").text
    assert "That did not work (cut off)" in page and ">Coach me<" in page
    coach_me(web)
    assert owner_sql("SELECT status, error FROM coachings ORDER BY id")[0] == {"status": "failed", "error": "cut off"}


def test_a_failed_coaching_says_so_and_costs_nothing(web, ai):
    ai.fail = True
    coach_me(web)
    row = owner_sql("SELECT status, error, usd FROM coachings")[0]
    assert row["status"] == "failed" and row["error"] == "ProviderError" and row["usd"] is None
    assert "That did not work (ProviderError)" in web[0].get("/web/").text
    audit = owner_sql("SELECT outcome, detail FROM audit_log WHERE tool = 'coaching'")[0]
    assert audit["outcome"] == "failed" and "ai" not in audit["detail"]


def test_the_daily_limit_and_bad_requests(web, ai):
    for _ in range(coach.DAILY_LIMIT):
        owner_sql("INSERT INTO coachings (kind, start_day, end_day, status) VALUES ('day', %s, %s, 'done')", day(), day())
    assert "daily limit" in coach_me(web).text and len(ai.calls) == 0
    owner_sql("DELETE FROM coachings")
    assert "not a day or week" in coach_me(web, day() + timedelta(days=1)).text
    assert "not a day or week" in act(web, "/web/coach/start", kind="month", start=day().isoformat(), end=day().isoformat()).text
    assert "not a day or week" in coach_me(web, day() - timedelta(days=2), "day", day()).text  # a day is one day


def test_the_status_address_needs_a_session(web, ai):
    from test_web import client
    assert client().get("/web/coach/status?id=1").status_code == 403
    assert web[0].get("/web/coach/status?id=999").json() == {"status": "gone"}


# ---- the week review -----------------------------------------------------------------------------------

def test_the_week_page_has_a_review_card_that_reads_the_week_and_its_day_coachings(web, ai):
    eat("Test oats", 400, 20, 10, 60)
    page = web[0].get("/web/week?p=7").text
    assert "Week review" in page and ">Review this week<" in page
    a = re.search(r'name="kind" value="week"><input type="hidden" name="start" value="([\d-]+)"><input type="hidden" name="end" value="([\d-]+)"', page)
    start, end = a[1], a[2]
    owner_sql("INSERT INTO coachings (kind, start_day, end_day, status, body) VALUES ('day', %s, %s, 'done', %s::jsonb)", end, end, json.dumps(DAY_BODY))
    act(web, "/web/coach/start", kind="week", start=start, end=end, back="/web/week?p=7")
    data = sent(ai)
    assert data["kind"] == "week" and len(data["week"]["days"]) == 7 and data["day_coachings_this_week"][0]["improve"] == DAY_BODY["improve"]
    assert "properties" in ai.calls[-1]["schema"] and "focus" in ai.calls[-1]["schema"]["properties"]
    page = web[0].get("/web/week?p=7").text
    assert "Against your targets" in page and "Best and hardest day" in page and "Focus for next week" in page and ">Review again<" in page


# ---- goals, the Coach page, Usage, the Today switch -----------------------------------------------------

def test_goals_are_set_in_settings_and_read_every_time(web, ai):
    page = web[0].get("/web/settings?tab=coach").text
    assert "My goals" in page and 'name="goals"' in page and "never thoughts, private or gratitude entries" in page
    out = act(web, "/web/settings/coach_goals", goals="Lose 5 kg by March;\nfasting sugar under 100", back="/web/settings?tab=coach")
    assert "the coach reads your goals" in out.text and "Lose 5 kg by March;\nfasting sugar under 100</textarea>" in html.unescape(out.text)
    coach_me(web)
    assert sent(ai)["goals"] == "Lose 5 kg by March;\nfasting sugar under 100"
    assert "no goals" in act(web, "/web/settings/coach_goals", goals="  ", back="/web/settings?tab=coach").text


def test_the_coach_page_filters_and_is_in_the_more_menu(web, ai):
    coach_me(web)
    owner_sql("INSERT INTO coachings (kind, start_day, end_day, status, body, usd) VALUES ('week', %s, %s, 'done', %s::jsonb, 0.2)",
              day() - timedelta(days=7), day() - timedelta(days=1), json.dumps(WEEK_BODY))
    c = web[0]
    page = c.get("/web/coach").text
    assert "<h1>Coach</h1>" in page and page.count('class="coitem"') == 2 and 'href="/web/coach"' in page
    assert c.get("/web/coach?f=weeks").text.count('class="coitem"') == 1 and c.get("/web/coach?f=days").text.count('class="coitem"') == 1
    assert "Protein at breakfast." in c.get("/web/coach?f=weeks").text


def test_the_cost_is_on_the_usage_page_as_coaching_by_opus(web, ai):
    coach_me(web)
    audit = owner_sql("SELECT detail FROM audit_log WHERE tool = 'coaching'")[0]["detail"]
    assert audit["ai"][0]["in"] == 6000 and audit["ai"][0]["out"] == 900 and audit["kind"] == "day" and "body" not in audit
    page = html.unescape(web[0].get("/web/usage").text)
    assert re.search(r"<td>coaching</td><td>1</td><td>6\.0k</td><td>0\.9k</td>", page) and "Opus (coaching)" in page


def test_the_coach_card_can_be_hidden_on_today(web, ai):
    uiprefs.save(["card:coach"], {})
    assert ">Coach me<" not in web[0].get("/web/").text
    assert 'name="show_card_coach"' in web[0].get("/web/settings?tab=today").text
