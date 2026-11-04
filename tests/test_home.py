"""The Home page, step 1 of 03.11.2026 (plan in the doc "Logger v2: status and web app plan", tab Current; spec
docs/specs/home-page-spec.md): the starter list, ages and the order of the chores, ticking (and ticking off) with Undo, "All
done", the Zones, History and Edit zones pages, chores typed in the chat by word match without the AI (clear lines, questions,
lines the AI must read), Recent, the Today card and its switch. Every value here is the starter list or invented."""
import re

import pytest

from app import home, homechat, uiprefs
from app.db import connect
from conftest import owner_sql
from test_log import ANSWERS, H, P, call, fake  # noqa: F401 - fixtures reused
from test_web import act, n, web  # noqa: F401


@pytest.fixture
def starter():
    return home.seed()


def zid(name):
    return owner_sql("SELECT id FROM home_zones WHERE name = %s", name)[0]["id"]


def cid(name, zone=None):
    rows = owner_sql("SELECT c.id FROM home_chores c LEFT JOIN home_zones z ON z.id = c.zone_id WHERE c.name = %s AND (%s::text IS NULL OR z.name = %s)",
                     name, zone, zone)
    return rows[0]["id"]


def done_rows():
    return owner_sql("""SELECT c.name AS chore, z.name AS zone, d.source, d.raw_id FROM home_done d JOIN home_chores c ON c.id = d.chore_id
                        JOIN home_zones z ON z.id = d.zone_id ORDER BY d.id""")


def token(page):
    return re.search(r'action="/web/undo/last">.*?name="token" value="([^"]+)"', page)[1]


def age_days(chore, zone, days):
    owner_sql("""INSERT INTO home_done (chore_id, zone_id, done_at, day, source) VALUES (%s, %s, now() - make_interval(days => %s),
                 log_day(now()) - %s, 'web')""", cid(chore), zid(zone), days, days)  # whole logging days (a clock change in between once made 60 days 59)


# ---- the starter list and the words -----------------------------------------------------------------

def test_the_starter_list_is_made_once(starter):
    assert starter == {"made": True, "zones": 8, "chores": 20}
    assert home.seed()["made"] is False
    assert n("SELECT count(*) AS n FROM home_chores WHERE zone_id IS NULL") == 3  # Dust, Vacuum, Mop: the whole flat
    mop = owner_sql("SELECT zone_days FROM home_chores WHERE name = 'Mop'")[0]["zone_days"]
    assert mop == {str(zid("Kitchen")): 7, str(zid("Bathroom")): 7}


def test_ages_and_intervals_are_plain_words():
    assert [home.age_text(x) for x in (None, 0, 1, 5, 14, 21, 42, 90, 400)] == [
        "not logged yet", "today", "yesterday", "5 days ago", "2 weeks ago", "3 weeks ago", "6 weeks ago", "3 months ago", "1 year ago"]
    assert [home.age_short(x) for x in (None, 0, 5, 21, 60, 400)] == ["not yet", "today", "5 days", "3 wk", "2 mo", "1 yr"]
    assert [home.interval_text(x) for x in (7, 14, 30, 60, 90, 21, 10)] == [
        "weekly", "every 2 weeks", "monthly", "every 2 months", "every 3 months", "every 3 weeks", "every 10 days"]


# ---- the order ---------------------------------------------------------------------------------------

def test_never_done_ranks_as_one_and_a_half_times_late_shorter_intervals_first(starter):
    with connect() as conn, conn.cursor() as cur:
        pairs = home.load(cur)["pairs"]
    assert (pairs[0]["chore"]["name"], pairs[0]["zone"]["name"], pairs[0]["age"]) == ("Vacuum", "Bathroom", None)  # weekly beats every 2 weeks
    assert len(pairs) == 3 * 8 + 17  # three whole-flat chores in 8 zones, 17 zone chores
    age_days("Vacuum", "Kitchen", 3)
    age_days("Dust", "Workstation", 100)
    with connect() as conn, conn.cursor() as cur:
        rows = home.worst(home.load(cur)["pairs"])
    names = [(p["chore"]["name"], p["zone"]["name"]) for p in rows]
    assert len(names) == 20 and len({p["chore"]["id"] for p in rows}) == 20  # one row per chore
    assert names[0] == ("Dust", "Workstation")  # 100 / 14 = 7 times late: before the never-done jobs (1.5 times late, round 2 step B)
    assert names[1] == ("Vacuum", "Bathroom")  # its other zones were never done
    age_days("Dust", "Workstation", 0)  # done today: the never-done zones come first again
    age_days("Bathtub", "Bathroom", 28)  # 28 / 14 = 2 times late: before a never-done job
    with connect() as conn, conn.cursor() as cur:
        rows = home.worst(home.load(cur)["pairs"])
    assert [(p["chore"]["name"], p["age"]) for p in rows[:2]] == [("Bathtub", 28), ("Vacuum", None)]
    assert dict((p["chore"]["name"], p["zone"]["name"]) for p in rows)["Dust"] != "Workstation"


def test_the_most_overdue_comes_before_a_less_overdue_one(starter):
    for name in (c["name"] for c in owner_sql("SELECT name FROM home_chores WHERE zone_id IS NOT NULL")):
        owner_sql("""INSERT INTO home_done (chore_id, zone_id, done_at, day, source) SELECT id, zone_id, now(), log_day(now()), 'web'
                     FROM home_chores WHERE name = %s AND zone_id IS NOT NULL""", name)
    owner_sql("""UPDATE home_done SET day = day - 30, done_at = done_at - interval '30 days' WHERE chore_id IN (SELECT id FROM home_chores WHERE name = 'Bathtub')""")
    owner_sql("""UPDATE home_done SET day = day - 3, done_at = done_at - interval '3 days' WHERE chore_id IN (SELECT id FROM home_chores WHERE name = 'Scrub sink')""")
    with connect() as conn, conn.cursor() as cur:
        pairs = [p for p in home.load(cur)["pairs"] if p["age"] is not None]
    assert [p["chore"]["name"] for p in pairs[:2]] == ["Bathtub", "Scrub sink"]  # 30 / 14 = 2.1 before 3 / 7 = 0.43


# ---- the pages ---------------------------------------------------------------------------------------

def test_the_pages_show_in_the_menu_and_the_empty_state_points_to_edit_zones(web):
    c = web[0]
    page = c.get("/web/home").text
    assert "<h1>Home</h1>" in page and "No zones or chores yet" in page and 'href="/web/home/edit"' in page
    assert 'href="/web/home"' in page  # More menu
    for path in ("/web/home/zones", "/web/home/history", "/web/home/edit"):
        assert c.get(path).status_code == 200


def test_now_lists_the_chores_waiting_longest(web, starter):
    c = web[0]
    page = c.get("/web/home").text
    assert "Longest since last done" in page and "20 chores" in page and "Show all 20 chores" in page
    assert page.count('class="horow"') == 6 and "Vacuum · Bathroom" in page and "not logged yet · weekly" in page
    assert "Recently done" not in page and "Pick a job for me" in page and "This week" in page  # step 2's cards


def test_a_tick_is_saved_ticked_off_by_a_second_tap_and_undone(web, starter):
    vac, bath = cid("Vacuum"), zid("Bathroom")
    page = act(web, "/web/home/tick", chore=str(vac), zone=str(bath), key="h1")
    assert "Saved: Vacuum · Bathroom" in page.text
    assert [(r["chore"], r["zone"], r["source"]) for r in done_rows()] == [("Vacuum", "Bathroom", "web")]
    assert owner_sql("SELECT content FROM raw_inbox")[0]["content"] == "home: Vacuum · Bathroom"
    assert re.search(r'class="hotile done"[^>]*>\s*<b>Bath</b><span>today</span>', web[0].get("/web/home/zones").text)
    twice = act(web, "/web/home/tick", chore=str(vac), zone=str(bath), key="h1")  # a double tap
    assert len(done_rows()) == 1 and twice.status_code == 200
    off = act(web, "/web/home/tick", chore=str(vac), zone=str(bath), key="h2")
    assert "Ticked off: Vacuum · Bathroom" in off.text and done_rows() == []
    back = act(web, "/web/undo/last", token=token(off.text))
    assert len(done_rows()) == 1 and back.status_code == 200
    gone = act(web, "/web/undo/last", token=token(act(web, "/web/home/tick", chore=str(cid("Mop")), zone=str(zid("Kitchen")), key="h3").text))
    assert [r["chore"] for r in done_rows()] == ["Vacuum"] and gone.status_code == 200


def test_a_chore_cannot_be_ticked_in_the_wrong_zone(web, starter):
    out = act(web, "/web/home/tick", chore=str(cid("Bathtub")), zone=str(zid("Kitchen")), key="x")
    assert "not in the list" in out.text and done_rows() == []
    assert "not in the list" in act(web, "/web/home/tick", chore="999", zone="1", key="y").text


def test_all_done_stamps_every_zone_as_one_message(web, starter):
    out = act(web, "/web/home/alldone", chore=str(cid("Dust")), key="a1")
    assert "Saved: Dust · whole flat" in out.text
    assert len(done_rows()) == 8 and {r["zone"] for r in done_rows()} == {z["name"] for z in owner_sql("SELECT name FROM home_zones")}
    assert n("SELECT count(*) AS n FROM raw_inbox") == 1
    act(web, "/web/undo/last", token=token(out.text))
    assert done_rows() == []
    act(web, "/web/home/alldone", chore=str(cid("Dust")), key="a3")
    assert web[0].get("/web/home/history").text.count("<td>Dust · whole flat</td>") == 1  # eight rows, one line
    assert "not a whole-flat chore" in act(web, "/web/home/alldone", chore=str(cid("Bathtub")), key="a2").text


def test_zones_page_has_tiles_cards_and_the_todoist_link(web, starter):
    age_days("Dust", "Kitchen", 5)
    age_days("Dust", "Workstation", 60)
    act(web, "/web/home/tick", chore=str(cid("Sink and toilet")), zone=str(zid("Bathroom")), key="z1")
    page = web[0].get("/web/home/zones").text
    assert "Whole flat" in page and page.count('class="hotile') >= 24 and "Edit zones" in page
    assert re.search(r'class="hotile done"[^>]*>\s*<b>Kitchen</b><span>5 days</span>', page)  # 5 of 14 days: tinted
    assert re.search(r'class="hotile"[^>]*>\s*<b>Desk</b><span>2 mo</span>', page)
    assert "Sink and toilet" in page and "weekly · today" in page
    assert 'href="/web/ideas/todoist"' in page and "sorting jobs in Todoist" in page
    assert "3 chores · oldest" in page  # the Bathroom card


def test_history_removes_a_tick_and_undo_puts_the_same_rows_back(web, starter):
    act(web, "/web/home/alldone", chore=str(cid("Vacuum")), key="a1")
    ids = [r["id"] for r in owner_sql("SELECT id FROM home_done ORDER BY id")]
    page = web[0].get("/web/home/history").text
    assert 'name="ids" value="' + ",".join(str(i) for i in reversed(ids)) + '"' in page
    gone = act(web, "/web/home/remove", ids=",".join(str(i) for i in ids))
    assert "Removed: Vacuum" in gone.text and done_rows() == []
    act(web, "/web/undo/last", token=token(gone.text))
    assert [r["id"] for r in owner_sql("SELECT id FROM home_done ORDER BY id")] == ids
    assert "not in the history" in act(web, "/web/home/remove", ids="9999").text


# ---- Edit zones --------------------------------------------------------------------------------------

def test_edit_zones_adds_changes_and_retires_chores_and_zones(web, starter):
    page = web[0].get("/web/home/edit").text
    assert "Whole flat" in page and "+ New chore" in page and "+ New zone" in page
    added = act(web, "/web/home/chorenew", name="Clean the oven", zone=str(zid("Kitchen")), days="60", minutes="25", steps="Racks\nDoor glass", howto="Soak first",
                words="oven, Backofen")
    assert "Added Clean the oven (every 2 months)" in added.text
    row = owner_sql("SELECT * FROM home_chores WHERE name = 'Clean the oven'")[0]
    assert (row["interval_days"], row["minutes"], row["steps"], row["words"], row["howto"]) == (60, 25, ["Racks", "Door glass"], ["oven", "backofen"], "Soak first")
    assert "1 to 730 days" in act(web, "/web/home/chorenew", name="x", zone="flat", days="0", minutes="10").text
    assert "Give the interval" in act(web, "/web/home/chorenew", name="x", zone="flat", days="abc", minutes="10").text
    assert "Pick a zone" in act(web, "/web/home/chorenew", name="x", zone="99999", days="7", minutes="10").text
    act(web, "/web/home/choreedit", chore=str(row["id"]), name="Clean the oven", zone=str(zid("Kitchen")), days="30", minutes="30", steps="", howto="", words="oven")
    assert owner_sql("SELECT interval_days, minutes, howto FROM home_chores WHERE id = %s", row["id"])[0] == {"interval_days": 30, "minutes": 30, "howto": None}
    retired = act(web, "/web/home/choreactive", chore=str(row["id"]), on="0")
    assert "retired; its history stays" in retired.text
    with connect() as conn, conn.cursor() as cur:
        assert "Clean the oven" not in [p["chore"]["name"] for p in home.load(cur)["pairs"]]
    act(web, "/web/home/choreactive", chore=str(row["id"]), on="1")
    assert n("SELECT count(*) AS n FROM home_chores WHERE active") == 21
    # zones: new, a duplicate, a rename, retire (its chores leave the lists), bring back
    assert "Added the zone Balcony" in act(web, "/web/home/zonenew", name="Balcony", short="", spots="Floor\nRailing", words="terrace").text
    assert "already in your list" in act(web, "/web/home/zonenew", name="balcony").text
    bal = zid("Balcony")
    assert owner_sql("SELECT short, spots, words FROM home_zones WHERE id = %s", bal)[0] == {"short": "Balcony", "spots": ["Floor", "Railing"], "words": ["terrace"]}
    assert "Another zone has that name" in act(web, "/web/home/zoneedit", zone=str(bal), name="Kitchen").text
    act(web, "/web/home/zoneedit", zone=str(bal), name="Terrace", short="Terr", spots="", words="")
    act(web, "/web/home/zoneactive", zone=str(zid("Laundry")), on="0")
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
    assert "Do laundry" not in [p["chore"]["name"] for p in d["pairs"]] and len([p for p in d["pairs"] if p["chore"]["name"] == "Dust"]) == 8  # 8 of 9 zones
    assert "Laundry · retired" in web[0].get("/web/home/edit").text
    assert "is back in your list" in act(web, "/web/home/zonenew", name="laundry").text


# ---- chores typed in the chat ---------------------------------------------------------------------------

def say(call, text, **kw):
    return call("log", text=text, **kw)


def test_clear_lines_are_saved_by_word_match_without_the_ai(call, fake, starter):
    out = say(call, "vacuumed the kitchen")
    assert out["outcome"] == "saved" and fake.calls == [] and "Home: Vacuum · Kitchen (" in out["message"]
    assert [(r["chore"], r["zone"], r["source"]) for r in done_rows()] == [("Vacuum", "Kitchen", "chat")]
    assert owner_sql("SELECT content, channel FROM raw_inbox")[0] == {"content": "vacuumed the kitchen", "channel": "mcp"}
    say(call, "dusted everything")
    assert [r["zone"] for r in done_rows() if r["chore"] == "Dust"].__len__() == 8
    say(call, "did laundry")
    say(call, "Changed the bedding.")
    say(call, "I mopped the bathroom and the kitchen")
    got = {(r["chore"], r["zone"]) for r in done_rows()}
    assert {("Do laundry", "Laundry"), ("Change bedding", "Sleeping"), ("Mop", "Bathroom"), ("Mop", "Kitchen")} <= got
    say(call, "dusted the desk")  # the desk is a place here, not the chore "Clear and wipe desk"
    assert ("Clear and wipe desk", "Workstation") not in {(r["chore"], r["zone"]) for r in done_rows()}
    assert fake.calls == []


def test_the_chats_undo_takes_a_chore_line_back(call, fake, starter):
    out = say(call, "vacuumed the kitchen")
    from app import webactions
    assert webactions.undo(str(out["raw_id"]))["head"] == "Undone."
    assert done_rows() == []


def test_a_repeated_chore_line_asks_like_any_message(call, fake, starter):
    say(call, "vacuumed the kitchen")
    again = say(call, "vacuumed the kitchen")
    assert again["outcome"] == "needs_confirmation" and len(done_rows()) == 1


def test_unclear_lines_ask_and_the_answer_saves(call, fake, starter):
    out = say(call, "cleaned the bathroom")
    assert out["outcome"] == "needs_choice" and done_rows() == [] and "Which job did you mean?" in out["message"]
    q = out["questions"][0]["question_id"]
    unclear = call("answer", question_id=q, reply="banana")
    assert unclear["outcome"] == "needs_choice"
    saved = call("answer", question_id=q, reply="2")
    assert saved["outcome"] == "saved" and [(r["chore"], r["zone"]) for r in done_rows()] == [("Bathtub", "Bathroom")]
    assert saved["raw_id"] == owner_sql("SELECT id FROM raw_inbox")[0]["id"]
    both = say(call, "cleaned the sink")  # a bathroom sink and a kitchen sink
    assert both["outcome"] == "needs_choice"
    call("answer", question_id=both["questions"][0]["question_id"], reply="all")
    assert {(r["chore"], r["zone"]) for r in done_rows()} == {("Bathtub", "Bathroom"), ("Sink and toilet", "Bathroom"), ("Scrub sink", "Kitchen")}
    flat = say(call, "vacuumed")  # which zone?
    assert flat["outcome"] == "needs_choice" and "Which zone" in flat["message"]
    skipped = call("answer", question_id=flat["questions"][0]["question_id"], reply="skip")
    assert skipped["outcome"] == "cancelled" and len(done_rows()) == 3
    window = say(call, "cleaned the window")
    call("answer", question_id=window["questions"][0]["question_id"], reply="sleeping")
    assert ("Window", "Sleeping") in {(r["chore"], r["zone"]) for r in done_rows()}


def test_the_question_card_has_a_button_per_choice(web, call, fake, starter):
    out = say(call, "cleaned the bathroom")
    page = web[0].get("/web/").text
    assert 'name="quick" value="H1"' in page and 'name="quick" value="H3"' in page and "All of them" in page and "Which job did you mean?" in page
    act(web, "/web/answer", question_id=out["questions"][0]["question_id"], quick="all", back="/web/")
    assert len(done_rows()) == 3


def test_lines_with_anything_else_in_them_go_to_the_ai(call, fake, starter):
    ANSWERS["vacuumed the kitchen and weight 90"] = P(health=[H("weight", 90)])
    ANSWERS["did stretching"] = P(thoughts=[])
    try:
        say(call, "vacuumed the kitchen and weight 90")
        say(call, "did stretching")
    finally:
        del ANSWERS["vacuumed the kitchen and weight 90"], ANSWERS["did stretching"]
    assert done_rows() == [] and [c[0] for c in fake.calls][:2] == ["vacuumed the kitchen and weight 90", "did stretching"]


def test_without_chores_the_chat_is_left_alone(call, fake):
    ANSWERS["did laundry"] = P(thoughts=[])
    try:
        say(call, "did laundry")
    finally:
        del ANSWERS["did laundry"]
    assert fake.calls and n("SELECT count(*) AS n FROM home_done") == 0
    with connect() as conn, conn.cursor() as cur:
        assert homechat.read(cur, "did laundry", None, "mcp") is None


def test_a_retired_zone_is_not_matched(call, fake, starter):
    owner_sql("UPDATE home_zones SET active = false WHERE name = 'Storage'")
    ANSWERS["cleaned the storage"] = P(thoughts=[])
    try:
        say(call, "cleaned the storage")
    finally:
        del ANSWERS["cleaned the storage"]
    assert fake.calls and done_rows() == []


# ---- Recent, Today and the switch ----------------------------------------------------------------------

def test_recent_shows_chores_from_the_chat_and_from_ticks(web, call, fake, starter):
    say(call, "vacuumed the kitchen")
    act(web, "/web/home/tick", chore=str(cid("Mop")), zone=str(zid("Workstation")), key="r1")
    page = web[0].get("/web/recent").text
    assert "Home" in page and "Vacuum · Kitchen" in page and "Mop · Workstation" in page


def test_the_today_card_names_the_chore_waiting_longest_and_can_be_hidden(web, starter):
    c = web[0]
    page = c.get("/web/").text
    assert "Waiting longest" in page and "Vacuum · Bathroom" in page and 'href="/web/home"' in page and "not logged yet · weekly" in page
    assert page.index("Waiting longest") < page.index("gymcard")  # before the Gym card
    assert "Waiting longest" not in c.get("/web/?day=2020-01-01").text  # not on earlier days
    settings = c.get("/web/settings?tab=today").text
    assert "Cards" in settings and 'name="show_card_home"' in settings
    act(web, "/web/settings/today")  # everything unchecked: hidden
    with connect() as conn, conn.cursor() as cur:
        assert "card:home" in uiprefs.load(cur)["hidden"]
    assert "Waiting longest" not in c.get("/web/").text


def test_the_today_card_needs_chores(web):
    assert "Waiting longest" not in web[0].get("/web/").text
