"""Private and gratitude lines saved without the AI at every door (privateline.py, 14.11.2026): a message starting with
"gratitude:", "private:" or "g:" goes straight into the private store, typed in the entry box, sent through the chat
connector, kept earlier and read again, or confirmed after "log it again?". The AI is never called and the words reach
no other table, log or reply. The gratitude view groups by log_day(), so its days follow the wake-up like every page."""
import html
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from app import held, logbook, privateline
from app.when import BERLIN
from conftest import owner_sql
from test_log import call, fake, off, skyr  # noqa: F401 - fixtures reused
from test_web import act, n, web  # noqa: F401
from test_webtoday import day, raw

WORD = "quietkitchen"  # an invented word that must stay in the private store
ELSEWHERE = ("private.incoming", "raw_inbox", "audit_log", "idempotency_keys", "pending", "web_sessions")


def entries():
    return owner_sql("SELECT label, content, summary, thought_type, area, tags, channel FROM private.entries ORDER BY id")


def nowhere_else(*also: str) -> None:
    for t in ELSEWHERE:
        assert WORD not in str(owner_sql(f"SELECT * FROM {t}")), t  # t is one of the fixed names above
    for x in also:
        assert WORD not in x


def body(page) -> str:
    return html.unescape(page.text)


# ---- the rules (no database)

def test_the_label_forms_and_what_is_not_a_label():
    assert [privateline.label(t) for t in ("gratitude: x", "  Gratitude : x", "G: x", "g:x", "private: x", "PRIVATE:\nx")] == \
        ["gratitude", "gratitude", "gratitude", "gratitude", "private", "private"]
    for t in ("g 200 skyr", "grateful for x", "p: x", "privately: x", "my gratitude: x", "200 g: skyr", "", None):
        assert privateline.label(t) is None, t


def test_lines_entries_and_refusals():
    kind, items = privateline.read(f"g: the {WORD}\n\n   the   warm  bread \ngratitude: a long call")
    assert kind == "gratitude" and [t for t, _ in items] == [f"gratitude: the {WORD}", "gratitude: the warm bread", "gratitude: a long call"]
    p = items[1][1]
    assert p["private"] == "gratitude" and p["thoughts"] == [{"summary": "the warm bread", "thought_type": "journal", "area": "personal",
                                                              "tags": ["gratitude"]}]
    kind, items = privateline.read("Private:\r\nfirst line\nsecond  line")
    assert kind == "private" and [t for t, _ in items] == ["private: first line\nsecond  line"]
    assert items[0][1]["thoughts"][0] | {} == {"summary": "first line", "thought_type": "note", "area": "personal", "tags": []}
    for text, why in (("gratitude:", "Type something after the label."), ("g:   \n  ", "Type something after the label."),
                      ("gratitude: " + "\n".join(f"line {i}" for i in range(21)), "At most 20 lines at once.")):
        try:
            privateline.read(text)
            raise AssertionError(text)
        except privateline.Refused as e:
            assert str(e) == why
    assert len(privateline.read("gratitude: " + "\n".join(f"line {i}" for i in range(20)))[1]) == 20


# ---- every door

def test_the_entry_box_saves_it_without_the_ai(web, fake):
    page = act(web, "/web/log", text=f"gratitude: the {WORD} at dawn", key="e1")
    assert "Saved." in body(page) and "private store only" in body(page)
    assert [(e["label"], e["content"], e["channel"]) for e in entries()] == [("gratitude", f"gratitude: the {WORD} at dawn", "web")]
    assert fake.calls == []
    nowhere_else(page.text)
    act(web, "/web/log", text=f"gratitude: the {WORD} at dawn", key="e1")  # a double tap: the key replays it
    assert len(entries()) == 1


def test_the_entry_box_short_form_several_lines_and_case(web, fake):
    page = act(web, "/web/log", text=f"G: the {WORD}\nthe warm bread\n\n a quiet street ", key="e2")
    assert body(page).count("private store only") == 3
    assert [e["content"] for e in entries()] == [f"gratitude: the {WORD}", "gratitude: the warm bread", "gratitude: a quiet street"]
    assert fake.calls == []
    nowhere_else(page.text)
    audit = owner_sql("SELECT outcome, detail FROM audit_log WHERE tool = 'log'")
    assert [a["outcome"] for a in audit] == ["saved"] and audit[0]["detail"]["entries"] == 3
    assert audit[0]["detail"]["private_ids"] == [1, 2, 3] and audit[0]["detail"]["ai"] == []


def test_a_private_text_with_line_breaks_is_one_entry(web, fake):
    page = act(web, "/web/log", text=f"Private: the {WORD} was loud\nand then quiet", key="e3")
    assert [(e["label"], e["content"], e["summary"], e["thought_type"]) for e in entries()] == [
        ("private", f"private: the {WORD} was loud\nand then quiet", f"the {WORD} was loud", "note")]
    assert fake.calls == []
    nowhere_else(page.text)


def test_refusals_save_nothing_and_send_nothing_to_the_ai(web, fake):
    page = act(web, "/web/log", text="gratitude:", key="e4")
    assert "Type something after the label." in body(page)
    page = act(web, "/web/log", text="g: " + "\n".join(f"{WORD} {i}" for i in range(21)), key="e5")
    assert "At most 20 lines at once." in body(page)
    assert entries() == [] and fake.calls == [] and n("SELECT count(*) AS n FROM private.incoming") == 0
    assert n("SELECT count(*) AS n FROM raw_inbox") == 0 and n("SELECT count(*) AS n FROM idempotency_keys") == 0
    for t in ("private.incoming", "raw_inbox", "audit_log", "idempotency_keys"):
        assert WORD not in str(owner_sql(f"SELECT * FROM {t}")), t


def test_the_chat_connector_saves_it_without_the_ai(call, fake):
    out = call("log", text=f"gratitude: the {WORD}\nthe warm bread", idempotency_key="m1")
    assert out["outcome"] == "saved" and len(out["private_ids"]) == 2 and out["private_id"] == out["private_ids"][0]
    assert [(e["content"], e["channel"]) for e in entries()] == [(f"gratitude: the {WORD}", "mcp"), ("gratitude: the warm bread", "mcp")]
    assert fake.calls == [] and WORD not in str(out)
    again = call("log", text=f"gratitude: the {WORD}\nthe warm bread", idempotency_key="m1")
    assert again["replayed"] and again["private_ids"] == out["private_ids"] and len(entries()) == 2
    assert call("log", text="g:", idempotency_key="m2")["outcome"] == "rejected" and len(entries()) == 2
    nowhere_else()


def keep(text: str, key: str, minutes: int = 10) -> int:
    """A labelled message kept for reading (as one was before 14.11.2026, or by any way round the label check)."""
    return owner_sql("""INSERT INTO private.incoming (received_at, channel, content, idempotency_key)
                        VALUES (now() - make_interval(mins => %s), 'mcp', %s, %s) RETURNING id""", minutes, text, key)[0]["id"]


def test_a_kept_labelled_message_read_by_cron_never_reaches_the_ai(fake):
    keep(f"gratitude: the {WORD}\nthe warm bread", "h1")
    bad = keep("g: " + "\n".join(f"line {i}" for i in range(21)), "h2")
    out = held.run()
    assert out["tried"] == 2 and out["saved"] == 1 and fake.calls == []
    assert [e["content"] for e in entries()] == [f"gratitude: the {WORD}", "gratitude: the warm bread"]
    left = owner_sql("SELECT id, last_error FROM private.incoming")
    assert [r["id"] for r in left] == [bad] and left[0]["last_error"] == held.NEEDS_YOU + "At most 20 lines at once."
    nowhere_else()
    hid = keep(f"private: the {WORD}", "h3")
    assert logbook.read_held(hid, background=True)["outcome"] == "saved" and len(entries()) == 3 and fake.calls == []
    nowhere_else()


def test_try_again_on_a_kept_labelled_message(web, fake):
    hid = keep(f"G: the {WORD}", "h4")
    page = act(web, "/web/held/retry", id=str(hid))
    assert "Saved." in body(page) and "Not read yet ·" not in body(page)
    assert [e["content"] for e in entries()] == [f"gratitude: the {WORD}"] and fake.calls == []
    nowhere_else(page.text)


def test_log_it_again_yes_saves_it_without_the_ai(fake):
    """An open "log it again?" question asked before 14.11.2026 about a labelled message: the yes still skips the AI."""
    owner_sql("""INSERT INTO pending (id, tool, payload, reason, expires_at) VALUES ('prep1', 'log_repeat', %s, 'same message', now() + interval '1 hour')""",
              Jsonb({"text": "gratitude: the warm bread", "now": datetime.now(BERLIN).isoformat()}))
    out = logbook.answer("prep1", "yes")
    assert out["outcome"] == "saved" and [e["content"] for e in entries()] == ["gratitude: the warm bread"] and fake.calls == []
    assert n("SELECT count(*) AS n FROM raw_inbox") == 0 and "warm bread" not in str(owner_sql("SELECT * FROM audit_log"))


def test_the_ai_paths_refuse_a_labelled_text_on_their_own(fake):
    from app import commands, record
    from app.db import connect
    from app.v1_log import Reject
    with connect() as c, c.cursor() as cur:
        for f in (lambda: record.read(cur, fake, "g: x", datetime.now(BERLIN)), lambda: logbook._read("private: x", datetime.now(BERLIN), [], [])):
            try:
                f()
                raise AssertionError("read")
            except Reject:
                pass
        assert commands.handle(cur, fake, "gratitude: x", datetime.now(BERLIN))[0]["outcome"] == "rejected"
    assert fake.calls == []


def test_unlabelled_lines_go_the_normal_way(fake, skyr):
    out = logbook.log("150 g skyr, weight 82.4", "n1")
    assert out["outcome"] == "saved" and fake.calls and entries() == []
    fake.calls.clear()
    assert logbook.log("grateful for a quiet morning", "n2")["outcome"] == "saved"  # no label: the AI reads it (and finds it private)
    assert fake.calls and entries()[0]["content"] == "grateful for a quiet morning"


def test_the_private_page_box_still_works_the_same(web, fake):
    page = act(web, "/web/private/new", kind="gratitude", key="pp1", text=f"the {WORD}\ng: the warm bread")
    assert "Saved 2 gratitude entries privately, without the AI." in body(page)
    assert [e["content"] for e in entries()] == [f"gratitude: the {WORD}", "gratitude: the warm bread"] and fake.calls == []
    nowhere_else()


# ---- the gratitude view follows the wake-up

def wake(at: datetime) -> None:
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                 SELECT %s, id, wake_day(%s), %s, true, 'test' FROM log_types WHERE name = 'wake'""", raw(), at, at)


def grat(content: str, at: datetime) -> None:
    owner_sql("""INSERT INTO private.entries (channel, label, content, summary, thought_type, area, tags, event_at)
                 VALUES ('test', 'gratitude', %s, 'x', 'journal', 'personal', '{gratitude}', %s)""", content, at)


def test_the_gratitude_view_follows_the_wake_up(web):
    d0 = day() - timedelta(days=6)
    at = lambda days, h, m=0: datetime(d0.year, d0.month, d0.day, h, m, tzinfo=BERLIN) + timedelta(days=days)  # noqa: E731
    wake(at(0, 14))  # a late day: up at 14:00 on d0
    grat("gratitude: owl hour tea", at(1, 6, 30))  # 06:30 the next morning, still before the next wake-up: d0's day
    wake(at(1, 9))
    wake(at(3, 0, 30))  # up at 00:30: the day starts at once
    grat("gratitude: early bird bread", at(3, 2))  # 02:00, after that wake-up: its own date, not the evening before
    c, _ = web
    first = c.get(f"/web/private?view=grat&day={d0.isoformat()}").text
    assert "owl hour tea" in first and "1 gratitude entry" in first
    assert "owl hour tea" not in c.get(f"/web/private?view=grat&day={(d0 + timedelta(days=1)).isoformat()}").text
    third = c.get(f"/web/private?view=grat&day={(d0 + timedelta(days=3)).isoformat()}").text
    assert "early bird bread" in third
    assert "early bird bread" not in c.get(f"/web/private?view=grat&day={(d0 + timedelta(days=2)).isoformat()}").text
    week = c.get(f"/web/private?view=grat&mode=week&day={d0.isoformat()}").text
    if d0.weekday() <= 3:  # d0 and d0 + 3 in one week: both days listed with their counts
        assert f"{d0:%a %d.%m.}" in week and f"{d0 + timedelta(days=3):%a %d.%m.}" in week and "2 gratitude entries this week" in week
    else:
        assert f"{d0:%a %d.%m.}" in week and "owl hour tea" in week
