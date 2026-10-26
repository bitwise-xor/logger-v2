"""The web app's entry box: the same path as the chat's `log`, the reply shown once under the box after a redirect.
The AI provider is the fake from test_log, so no API key is needed."""
import re

from app import logbook
from app.ai.provider import ProviderError
from conftest import owner_sql
from test_log import fake, off, skyr  # noqa: F401 - fixtures reused
from test_web import act, n, web  # noqa: F401


def test_the_entry_box_is_on_every_page(web):
    c, csrf = web
    for path in ("/web/", "/web/questions"):
        page = c.get(path).text
        assert 'action="/web/log"' in page and 'id="entry-text"' in page and csrf in page
        assert re.search(r'name="key" value="[\w-]{16,}"', page)


def test_an_entry_is_logged_like_in_the_chat(web, fake, skyr):
    page = act(web, "/web/log", text="150 g skyr, weight 82.4", key="w1")
    assert page.status_code == 200 and "Saved." in page.text and "Skyr natural" in page.text
    assert n("SELECT count(*) AS n FROM food_entries WHERE food_id = %s AND amount = 150", skyr) == 1
    assert n("SELECT count(*) AS n FROM measurements WHERE value1 = 82.4") == 1
    assert fake.calls == [("150 g skyr, weight 82.4", "claude-haiku-5-5")]
    assert [r["channel"] for r in owner_sql("SELECT channel FROM raw_inbox")] == ["web"]
    assert {r["client"] for r in owner_sql("SELECT client FROM audit_log")} == {"web"}


def test_the_same_words_from_the_chat_and_the_web_are_caught(web, fake):
    logbook.log("idea: a weekly food chart")  # through the chat
    page = act(web, "/web/log", text="idea: a weekly food chart", key="w12")
    assert "please confirm" in page.text and "already logged" in page.text
    assert n("SELECT count(*) AS n FROM thoughts") == 1
    qid = re.search(r'name="question_id" value="([^"]+)"', page.text)[1]
    done = act(web, "/web/answer", question_id=qid, quick="yes")
    assert "Saved." in done.text and n("SELECT count(*) AS n FROM thoughts") == 2
    assert [r["channel"] for r in owner_sql("SELECT channel FROM raw_inbox ORDER BY id")] == ["mcp", "web"]


def test_sending_the_same_form_twice_saves_once(web, fake, skyr):
    act(web, "/web/log", text="150 g skyr, weight 82.4", key="w2")
    again = act(web, "/web/log", text="150 g skyr, weight 82.4", key="w2")
    assert "Already saved earlier" in again.text
    assert n("SELECT count(*) AS n FROM food_entries") == 1 and len(fake.calls) == 1


def test_reloading_the_page_repeats_nothing(web, fake, skyr):
    act(web, "/web/log", text="150 g skyr, weight 82.4", key="w3")
    for _ in range(2):
        page = web[0].get("/web/")
        assert "Saved." not in page.text
    assert n("SELECT count(*) AS n FROM food_entries") == 1 and len(fake.calls) == 1


def test_a_question_for_the_assistant_is_not_logged(web, fake):
    page = act(web, "/web/log", text="why is that?", key="w8")
    assert "Not logged" in page.text and n("SELECT count(*) AS n FROM raw_inbox") == 0


def test_a_look_up_shows_its_result_escaped(web, fake, skyr):
    act(web, "/web/log", text="150 g skyr, weight 82.4", key="w9")
    owner_sql("UPDATE foods SET name = 'Skyr <i>natural</i>' WHERE id = %s", skyr)
    page = act(web, "/web/log", text="what did I eat today", key="w10")
    assert "Found." in page.text and "<i>natural</i>" not in page.text and "&lt;i&gt;natural&lt;/i&gt;" in page.text


def test_an_ai_failure_says_nothing_was_saved(web, skyr):
    class Down:
        def json(self, *a, **k):
            raise ProviderError("down")
    logbook.PROVIDER = Down()
    try:
        page = act(web, "/web/log", text="150 g skyr, weight 82.4", key="w11")
    finally:
        logbook.PROVIDER = None
    assert "Nothing saved." in page.text and "did not answer" in page.text
    assert n("SELECT count(*) AS n FROM food_entries") == 0
