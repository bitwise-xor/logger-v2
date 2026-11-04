"""The complete Usage page (04.11.2026, web item 29 / core item 26): every kind of AI call records its tokens (food look-ups, lab
reports, label photos, answers that call the AI), the page names each kind and shows its tokens, older rows without tokens are
marked, and the phone notifications are counted (they cost nothing)."""
import html

from app import logbook
from app.ai.provider import Usage
from conftest import owner_sql
from test_log import REPORT_TEXT, call, fake, skyr  # noqa: F401 - fixtures reused
from test_web import web  # noqa: F401
from test_webusage import S, call as audit


def ai_of(tool):
    return [r["detail"].get("ai") for r in owner_sql("SELECT detail FROM audit_log WHERE tool = %s ORDER BY id", tool)]


def test_every_call_is_recorded_with_its_tokens():
    u = Usage("claude-sonnet-5-5", 3000, 800, cache_read_tokens=500, seconds=2.345)
    assert logbook._ai([u]) == [{"model": "claude-sonnet-5-5", "in": 3500, "out": 800, "cache_read": 500, "cache_write": 0,
                                 "usd": round(u.usd, 6), "s": 2.35}]


def test_a_lab_report_records_its_tokens(call, fake):
    call("lab_report", text=REPORT_TEXT, source="document")
    (ai,) = ai_of("lab_report")
    assert ai[0]["in"] == 3000 and ai[0]["out"] == 800 and ai[0]["usd"] > 0


def test_an_answer_that_calls_the_ai_is_recorded(call, fake, skyr):
    call("log", text="150 g skyr, weight 82.4")
    asked = call("log", text="delete the skyr")
    call("answer", question_id=asked["questions"][0]["question_id"], reply="the first")  # the request is read again with the answer
    answers = [a for a in ai_of("answer") if a]
    assert len(answers) == 1 and answers[0][0]["in"] == 1000 and answers[0][0]["out"] == 100


def test_the_page_names_every_kind_marks_old_rows_and_counts_notifications(web):
    audit("label_photo", [{"model": S, "in": 2500, "out": 300, "usd": 0.012}])
    audit("answer", [{"model": S, "in": 1000, "out": 100, "usd": 0.004}])
    audit("lab_report", [{"model": S, "usd": 0.02}])  # an older row: cost only
    for _ in range(3):
        owner_sql("INSERT INTO audit_log (tool, outcome, client, detail) VALUES ('nudge', 'sent', 'nudges', '{}'::jsonb)")
    page = html.unescape(web[0].get("/web/usage").text)
    assert "<td>label photo</td><td>1</td><td>2.5k</td><td>0.3k</td><td>$0.012</td>" in page
    assert "<td>answer to a question</td><td>1</td><td>1.0k</td><td>0.1k</td><td>$0.004</td>" in page
    assert "<td>lab report</td><td>1</td><td>0.0k*</td><td>0.0k*</td><td>$0.020</td>" in page
    assert "Phone notifications sent: 3 (free)." in page and "label_photo" not in page
    assert "Sonnet (second reads, lab reports, label photos)" in page
