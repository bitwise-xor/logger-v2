"""Open questions on Today and the Questions page, each with the form that fits it, answered in place."""
import re

from app import logbook
from conftest import owner_sql
from test_log import fake, off, skyr, OAT  # noqa: F401 - fixtures reused
from test_web import act, client, n, web  # noqa: F401


def card(page, qid):
    return re.search(rf'<div class="q">(?:(?!<div class="q">).)*?value="{qid}".*?</form></div>', page, re.S)[0]


def test_nothing_open(web):
    c, _ = web
    r = c.get("/web/questions")
    assert r.status_code == 200 and "Nothing is waiting for an answer." in r.text
    assert 'class="badge"' not in c.get("/web/").text


def test_which_food_shows_the_suggestions_as_buttons(web, fake):
    owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                 VALUES ('Cream cheese', 'g', 250, 6, 24, 3, 'database')""")
    asked = logbook.log("50 g frischkaese")  # through the chat
    qid = asked["questions"][0]["question_id"]
    page = web[0].get("/web/").text
    assert "Waiting for your answer · 1" in page and 'class="badge">1<' in page
    q = card(page, qid)
    assert '"50 g frischkaese"' in q and 'Which food is "frischkaese"?' in q
    assert 'name="quick" value="N' in q and "Look it up" in q and 'value="skip"' in q


def test_a_suggestion_button_answers_it(web, fake):
    cc = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                      VALUES ('Cream cheese', 'g', 250, 6, 24, 3, 'database') RETURNING id""")[0]["id"]
    qid = logbook.log("50 g frischkaese")["questions"][0]["question_id"]
    assert f'value="N{cc}"' in card(web[0].get("/web/").text, qid)
    page = act(web, "/web/answer", question_id=qid, quick=f"N{cc}", back="/web/questions")
    assert page.request.url.path == "/web/questions" and "Saved." in page.text and "Nothing is waiting" in page.text
    assert n("SELECT count(*) AS n FROM food_entries WHERE amount = 50") == 1


def test_a_typed_food_number_answers_it(web, fake, skyr):
    qid = logbook.log("150 g skyr and 20 g dragonfruit")["questions"][0]["question_id"]
    dragon = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                          VALUES ('Dragon fruit', 'g', 60, 1.2, 0, 13, 'database') RETURNING id""")[0]["id"]
    page = act(web, "/web/answer", question_id=qid, reply=f"N{dragon}")
    assert "Saved." in page.text and n("SELECT count(*) AS n FROM food_entries") == 2


def test_a_found_food_shows_its_values_and_is_added_with_one_click(web, fake, off):
    off.hits = [OAT]
    out = logbook.log("100 g haferdrink")
    qid = out["questions"][0]["question_id"]
    q = card(web[0].get("/web/").text, qid)
    assert "Found in Open Food Facts" in q and "<td>kcal</td><td>61" in q and "Add this food" in q
    page = act(web, "/web/answer", question_id=qid, quick="yes")
    assert "Saved." in page.text and n("SELECT count(*) AS n FROM food_entries") == 1


def test_skip_closes_a_question(web, fake, skyr):
    qid = logbook.log("150 g skyr and 20 g dragonfruit")["questions"][0]["question_id"]
    page = act(web, "/web/answer", question_id=qid, quick="skip")
    assert "Skipped." in page.text and "Waiting for your answer" not in page.text


def test_a_read_back_shows_whole_with_yes_and_no_and_correct_it(web, fake):
    logbook.log("weight 90")
    qid = logbook.log("weight 90 kg")["questions"][0]["question_id"]
    q = card(web[0].get("/web/questions").text, qid)
    assert "<pre>" in q and 'value="yes"' in q and 'value="no"' in q and 'data-fill="weight 90 kg"' in q
    assert "Ask the user" not in q and "resend" not in q and "Nothing saved yet:" in q  # the chat app's instructions are left out
    page = act(web, "/web/answer", question_id=qid, quick="no")
    assert "Skipped." in page.text and n("SELECT count(*) AS n FROM measurements") == 1


def test_today_shows_three_and_links_to_the_rest(web):
    for i in range(5):
        owner_sql("""INSERT INTO pending (id, tool, payload, reason, expires_at)
                     VALUES (%s, 'command', '{}', %s, now() + interval '1 day')""", f"pq{i}", f"Which entry {i}?")
    page = web[0].get("/web/").text
    assert page.count('<div class="q">') == 3 and "see all 5 on Questions" in page
    assert web[0].get("/web/questions").text.count('<div class="q">') == 5


def test_answered_expired_and_pilot_questions_are_not_listed(web):
    for pid, tool, extra in (("pdone", "log", "resolved_at = now()"), ("pold", "log", "expires_at = now() - interval '1 minute'"),
                             ("ppilot", "record_weight", None), ("popen", "command", None)):
        owner_sql("""INSERT INTO pending (id, tool, payload, reason, expires_at)
                     VALUES (%s, %s, '{}', %s, now() + interval '1 day')""", pid, tool, f"question {pid}")
        if extra:
            owner_sql(f"UPDATE pending SET {extra} WHERE id = %s", pid)
    page = web[0].get("/web/questions").text
    assert re.findall(r"question (p\w+)", page) == ["popen"]


def test_question_text_and_message_are_escaped(web):
    raw = owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('mcp', '<b>my</b> message') RETURNING id")[0]["id"]
    owner_sql("""INSERT INTO pending (id, tool, payload, reason, expires_at)
                 VALUES ('px', 'command', jsonb_build_object('raw_id', %s::bigint), 'Which <i>one</i>?', now() + interval '1 day')""", raw)
    page = web[0].get("/web/questions").text
    assert "<b>my</b>" not in page and "&lt;b&gt;my&lt;/b&gt; message" in page
    assert "<i>one</i>" not in page and "Which &lt;i&gt;one&lt;/i&gt;?" in page


def test_the_questions_page_needs_a_session_and_the_app_host():
    owner_sql("INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES ('ps', 'log', '{}', 'secret question', now() + interval '1 day')")
    r = client().get("/web/questions")
    assert r.status_code == 302 and "secret question" not in r.text
    assert client("log.example.test").get("/web/questions").status_code == 404
    assert client().get("/web/questions", headers={"CF-Ray": "x"}).status_code == 404
