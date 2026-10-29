"""The Private page: the words shown, new entries saved without the AI (one gratitude entry per line), filter and search,
change (Save is the yes) and delete (asked once), gratitude by day and by week; private words never reach the general
inbox, the audit log or a reply."""
import html
from datetime import timedelta

from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import act, client, n, web  # noqa: F401
from test_webtoday import day


def entry(label, content, summary=None, days_ago=0, hm="20:00"):
    return owner_sql(f"""INSERT INTO private.entries (channel, label, content, summary, thought_type, area, tags, event_at)
                         VALUES ('test', %s, %s, %s, 'journal', 'personal', %s,
                                 (log_day(now()) - {int(days_ago)}) + time '{hm}' AT TIME ZONE 'Europe/Berlin') RETURNING id""",
                     label, content, summary or content.split(":", 1)[-1].strip()[:40], ["gratitude"] if label == "gratitude" else [])[0]["id"]


def rows():
    return owner_sql("SELECT label, content, summary, thought_type, area, tags FROM private.entries ORDER BY id")


def test_the_page_needs_sign_in_and_the_app_host():
    assert client().get("/web/private").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/private").status_code == 404


def test_new_gratitude_lines_are_saved_one_each_without_the_ai(web, fake):
    page = act(web, "/web/private/new", kind="gratitude", key="g1",
               text="Grateful for the walk\n\n  gratitude: the call with my sister  \nfirst frost on the fields")
    text = html.unescape(page.text)
    assert "Saved 3 gratitude entries privately, without the AI." in text and fake.calls == []
    assert [(r["label"], r["content"], r["summary"], r["thought_type"], r["tags"]) for r in rows()] == [
        ("gratitude", "gratitude: Grateful for the walk", "Grateful for the walk", "journal", ["gratitude"]),
        ("gratitude", "gratitude: the call with my sister", "the call with my sister", "journal", ["gratitude"]),
        ("gratitude", "gratitude: first frost on the fields", "first frost on the fields", "journal", ["gratitude"])]
    assert n("SELECT count(*) AS n FROM raw_inbox") == 0  # never the general inbox
    assert "sister" not in str(owner_sql("SELECT detail FROM audit_log")) and "sister" not in text.split("<main id=\"main\">")[1].split("New entry")[0]
    again = act(web, "/web/private/new", kind="gratitude", key="g1", text="Grateful for the walk\n\ngratitude: the call with my sister\nfirst frost on the fields")
    assert n("SELECT count(*) AS n FROM private.entries") == 3, again.text  # a double click saves nothing twice


def test_a_new_private_entry_is_one_entry(web, fake):
    act(web, "/web/private/new", kind="private", key="p1", text="Felt low in the afternoon.\nProbably the short night.")
    r = rows()
    assert [(x["label"], x["content"], x["summary"], x["thought_type"]) for x in r] == [
        ("private", "private: Felt low in the afternoon.\nProbably the short night.", "Felt low in the afternoon.", "note")]
    assert fake.calls == []


def test_the_list_with_words_filter_and_search(web):
    entry("gratitude", "gratitude: Grateful for the first frost", days_ago=1)
    entry("private", "private: Felt low after a short night", days_ago=0)
    c, _ = web
    page = c.get("/web/private").text
    assert 'href="/web/private" aria-current="page">Private' in page and "2 entries" in page
    assert "Felt low after a short night" in page and "Grateful for the first frost" in page and "gratitude: Grateful" not in page
    assert page.index("Felt low") < page.index("first frost")  # newest first
    assert "Felt low" not in c.get("/web/private?kind=gratitude").text
    assert "1 entries" in c.get("/web/private?q=frost").text


def test_change_is_saved_at_once_and_delete_asks_once(web):
    pid = entry("gratitude", "gratitude: Grateful for the first frost", days_ago=1)
    page = web[0].get(f"/web/private?edit=P{pid}")
    assert 'action="/web/private/entry"' in page.text
    d = day() - timedelta(days=1)
    page = act(web, "/web/private/entry", id=f"P{pid}", act="save", summary="First frost", thought_type="journal", area="health",
               tags="gratitude, winter", day=d.isoformat(), time="07:30")
    assert "Saved: summary, area, tags, date." in page.text
    r = owner_sql("""SELECT summary, area, tags, to_char(event_at AT TIME ZONE 'Europe/Berlin', 'HH24:MI') AS t FROM private.entries""")[0]
    assert (r["summary"], r["area"], r["tags"], r["t"]) == ("First frost", "health", ["gratitude", "winter"], "07:30")
    assert "Yes, delete" in act(web, "/web/private/entry", id=f"P{pid}", act="delete").text and n("SELECT count(*) AS n FROM private.entries") == 1
    act(web, "/web/private/entry", id=f"P{pid}", act="delete", confirmed="1")
    assert n("SELECT count(*) AS n FROM private.entries") == 0


def test_gratitude_by_day_and_by_week(web):
    d = day()
    entry("gratitude", "gratitude: one", days_ago=0, hm="08:00")
    entry("gratitude", "gratitude: two", days_ago=0, hm="21:00")
    entry("gratitude", "gratitude: three", days_ago=8)
    entry("private", "private: not gratitude", days_ago=0)
    c, _ = web
    page = c.get("/web/private?view=grat").text
    assert "2 gratitude entries" in page and "one" in page and "two" in page and "not gratitude" not in page and "three" not in page
    week = c.get(f"/web/private?view=grat&mode=week&day={d.isoformat()}").text
    assert "this week" in week and f"Week {(d - timedelta(days=d.weekday())).isocalendar()[1]}" in week
    old = c.get(f"/web/private?view=grat&day={(d - timedelta(days=8)).isoformat()}").text
    assert "1 gratitude entry" in old and "three" in old


def test_bad_input_changes_nothing(web, fake):
    pid = entry("gratitude", "gratitude: keep me")
    assert "Pick Gratitude or Private" in act(web, "/web/private/new", kind="secret", text="x").text
    assert "Type something first" in act(web, "/web/private/new", kind="gratitude", text="  \n ").text
    assert "At most 20 lines" in act(web, "/web/private/new", kind="gratitude", text="\n".join(f"line {i}" for i in range(21))).text
    assert "Unknown entry" in act(web, "/web/private/entry", id="P1; drop", act="save").text
    assert "cannot be empty" in act(web, "/web/private/entry", id=f"P{pid}", act="save", summary=" ").text
    assert "from the lists" in act(web, "/web/private/entry", id=f"P{pid}", act="save", summary="x", thought_type="poem").text
    assert "hh:mm" in act(web, "/web/private/entry", id=f"P{pid}", act="save", summary="x", day=day().isoformat(), time="25:00").text
    assert [r["content"] for r in rows()] == ["gratitude: keep me"]
