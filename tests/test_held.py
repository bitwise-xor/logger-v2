"""Never lose a message (01.11.2026, core items 1 and 2; held.py): a typed message is kept before the AI reads it, the AI
reads with no transaction open, and when the AI or the save fails the words stay "not read yet". They are read again by
cron (2 hours), by Try again on the web app, or when the chat sends the same message again; always with their own time."""
import re
from datetime import datetime, timedelta

import pytest

from app import db, held, logbook, nudges
from app.ai.provider import ProviderError
from app.when import BERLIN
from conftest import owner_sql
from test_log import fake, off, skyr  # noqa: F401 - fixtures reused
from test_web import act, n, web  # noqa: F401

TEXT = "150 g skyr, weight 82.4"
FOOD = "SELECT count(*) AS n FROM food_entries"


class Down:
    """The AI service not answering."""
    def __init__(self, err=None):
        self.err = err or ProviderError("down")

    def json(self, *a, **k):
        raise self.err


def kept():
    return owner_sql("SELECT * FROM private.incoming ORDER BY id")


def keep(text, key, fake):
    """Send a message while the AI is down, then bring the AI back."""
    logbook.PROVIDER = Down()
    out = logbook.log(text, key)
    logbook.PROVIDER = fake
    return out


@pytest.fixture
def device():
    owner_sql("INSERT INTO push_subscriptions (endpoint, p256dh, auth) VALUES ('https://web.push.apple.com/held-test', 'k', 'a')")
    yield
    owner_sql("DELETE FROM push_subscriptions WHERE endpoint = 'https://web.push.apple.com/held-test'")


def test_an_ai_failure_keeps_the_words_and_saves_nothing(fake, skyr):
    out = keep(TEXT, "k1", fake)
    assert out["outcome"] == "held" and "Do not send this message again" in out["message"]
    assert [(r["content"], r["channel"], r["idempotency_key"], r["tries"], r["last_error"], r["reading_since"]) for r in kept()] == \
        [(TEXT, "mcp", "k1", 1, "ProviderError", None)]
    assert n(FOOD) == 0 and n("SELECT count(*) AS n FROM raw_inbox") == 0
    audit = owner_sql("SELECT outcome, detail FROM audit_log WHERE tool = 'log'")
    assert [a["outcome"] for a in audit] == ["held"] and "skyr" not in str(audit)  # the kind of error only, never the words


def test_a_retry_saves_it_once_with_the_time_it_was_sent(fake, skyr, monkeypatch):
    sent = datetime.now(BERLIN).replace(microsecond=0) - timedelta(minutes=40)
    monkeypatch.setattr(logbook, "clock", lambda: sent)
    keep(TEXT, "k2", fake)
    monkeypatch.setattr(logbook, "clock", lambda: datetime.now(BERLIN))
    out = logbook.read_held(kept()[0]["id"])
    assert out["outcome"] == "saved" and kept() == []
    at = owner_sql("SELECT eaten_at FROM food_entries")[0]["eaten_at"]
    assert abs(at - sent) <= timedelta(minutes=1)  # logged at the time it was sent, not when it was read
    again = logbook.log(TEXT, "k2")  # the chat sends it again with the same key
    assert again.get("replayed") and n(FOOD) == 1


def test_sending_it_again_with_the_same_key_reads_the_kept_message(fake, skyr):
    keep(TEXT, "k3", fake)
    out = logbook.log(TEXT, "k3")
    assert out["outcome"] == "saved" and kept() == []
    assert n(FOOD) == 1 and n("SELECT count(*) AS n FROM raw_inbox") == 1


def test_the_same_words_with_another_key_while_it_waits_are_asked_about(fake, skyr):
    keep(TEXT, "k4", fake)
    out = logbook.log(TEXT, "k5")
    assert out["outcome"] == "needs_confirmation" and "already waiting to be read" in out["reason"]
    assert len(kept()) == 1 and n(FOOD) == 0


def test_a_message_being_read_is_not_read_twice_and_a_dropped_read_is_taken_again(fake, skyr):
    keep(TEXT, "k6", fake)
    hid = kept()[0]["id"]
    owner_sql("UPDATE private.incoming SET reading_since = now() WHERE id = %s", hid)
    assert logbook.read_held(hid)["outcome"] == "held" and fake.calls == []
    owner_sql("UPDATE private.incoming SET reading_since = now() - interval '11 minutes' WHERE id = %s", hid)  # the container stopped mid-read
    assert logbook.read_held(hid)["outcome"] == "saved" and n(FOOD) == 1
    assert logbook.read_held(hid)["outcome"] == "rejected"  # gone: saved


def test_a_failure_while_saving_saves_nothing_and_keeps_the_words(fake, skyr, monkeypatch):
    real = logbook._save

    def boom(cur, *a, **k):
        real(cur, *a, **k)
        raise RuntimeError("after the save")
    monkeypatch.setattr(logbook, "_save", boom)
    out = logbook.log(TEXT, "k7")
    assert out["outcome"] == "held" and "Something went wrong" in out["reason"]
    assert n(FOOD) == 0 and n("SELECT count(*) AS n FROM raw_inbox") == 0
    assert [r["last_error"] for r in kept()] == ["RuntimeError"]
    monkeypatch.setattr(logbook, "_save", real)
    assert logbook.read_held(kept()[0]["id"])["outcome"] == "saved" and n(FOOD) == 1 and kept() == []


def test_the_ai_reads_with_no_database_transaction_open(fake, skyr, monkeypatch):
    opened, seen = [], []

    def tracking(*a, **k):
        c = db.connect(*a, **k)
        opened.append(c)
        return c
    for mod in (logbook, held):
        monkeypatch.setattr(mod, "connect", tracking)
    real = fake.json

    def watching(*a, **k):
        seen.append([c.info.transaction_status.name for c in opened if not c.closed])
        return real(*a, **k)
    monkeypatch.setattr(fake, "json", watching)
    assert logbook.log(TEXT, "k8")["outcome"] == "saved"
    assert seen == [[]]  # every connection was closed while the AI read


def test_cron_tries_for_two_hours_then_tells_the_phone_once_without_the_words(fake, skyr, monkeypatch, device):
    keep(TEXT, "k9", fake)
    keep("idea: a weekly food chart", "k10", fake)
    owner_sql("UPDATE private.incoming SET last_try_at = now() - interval '5 minutes'")
    owner_sql("UPDATE private.incoming SET received_at = now() - interval '3 hours' WHERE idempotency_key = 'k10'")
    told = []
    monkeypatch.setattr(nudges, "deliver", lambda m: told.append(m) or 1)
    assert held.run() == {"tried": 1, "saved": 1, "late": 1, "told": 1}
    assert [r["idempotency_key"] for r in kept()] == ["k10"] and n(FOOD) == 1
    assert len(told) == 1 and "weekly" not in str(told[0]) and told[0]["body"].startswith("1 message could not be read")
    assert held.run() == {"tried": 0, "saved": 0, "late": 0, "told": 0}  # told once


def test_a_message_cron_cannot_log_waits_for_the_owner(fake):
    keep("why is that?", "k11", fake)  # a question for the assistant: nothing to log
    owner_sql("UPDATE private.incoming SET last_try_at = now() - interval '5 minutes'")
    assert held.run()["saved"] == 0
    r = kept()[0]
    assert r["last_error"].startswith(held.NEEDS_YOU) and r["reading_since"] is None and r["content"] == "why is that?"
    owner_sql("UPDATE private.incoming SET last_try_at = now() - interval '5 minutes'")
    assert held.run()["tried"] == 0  # no more automatic tries


def test_a_kept_message_that_is_private_goes_to_the_private_store_only(fake):
    keep("grateful for a quiet morning", "k12", fake)
    assert logbook.read_held(kept()[0]["id"])["outcome"] == "saved"
    assert n("SELECT count(*) AS n FROM private.entries") == 1 and n("SELECT count(*) AS n FROM raw_inbox") == 0 and kept() == []


def test_a_late_retry_gives_its_questions_their_full_time(fake, monkeypatch):
    monkeypatch.setattr(logbook, "clock", lambda: datetime.now(BERLIN) - timedelta(days=4))
    keep("30 g zzqfood", "k13", fake)
    out = logbook.read_held(kept()[0]["id"])
    assert out["questions"] and kept() == []
    assert n("SELECT count(*) AS n FROM pending WHERE expires_at > now() + interval '71 hours'") == len(out["questions"])


def test_quick_buttons_are_saved_at_once_as_before(fake, skyr):
    out = logbook.log("weight 90", "k14", pred={"intent": "log", "command_text": None, "private": None, "private_unsure": False,
                                                "food": [], "usual_meals": [], "health": [{"type": "weight", "value": 90, "value2": None,
                                                                                            "period": None, "session": None, "when": None}],
                                                "thoughts": [], "todos": []})
    assert out["outcome"] == "saved" and kept() == [] and fake.calls == []


def test_today_and_recent_show_it_with_try_again_delete_and_undo(web, fake, skyr):
    logbook.PROVIDER = Down()
    page = act(web, "/web/log", text=TEXT, key="w1")
    assert "Not read yet, but kept." in page.text and "Not read yet · 1" in page.text and "Try again" in page.text
    assert f">{TEXT}</textarea>" not in page.text  # not put back in the box: it is kept, so it is not sent twice
    assert "Not read yet · 1" in web[0].get("/web/recent").text
    hid = kept()[0]["id"]
    gone = act(web, "/web/held/delete", id=str(hid))
    assert "Deleted the unread message." in gone.text and "Not read yet ·" not in gone.text
    token = re.search(r'action="/web/undo/last">.*?name="token" value="([^"]+)"', gone.text)[1]
    back = act(web, "/web/undo/last", token=token)
    assert "The unread message is back." in back.text and "Not read yet · 1" in back.text
    logbook.PROVIDER = fake
    done = act(web, "/web/held/retry", id=str(hid))
    assert "Saved." in done.text and "Not read yet ·" not in done.text and n(FOOD) == 1


def test_a_deleted_message_goes_for_good_after_a_day(fake):
    from app import maintenance
    keep("idea: a weekly food chart", "k15", fake)
    owner_sql("UPDATE private.incoming SET deleted_at = now() - interval '25 hours'")
    assert maintenance.cleanup()["deleted_unread"] == 1 and kept() == []
