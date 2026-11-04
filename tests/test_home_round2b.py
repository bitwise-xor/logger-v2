"""The Home page, round 2, step B of 04.11.2026 (plan in the doc, tab Current): never-logged jobs rank as 1.5 times late and get a
starting point on Zones; whole-flat chores per zone (on/off, interval, minutes) and "Vacuum the whole flat"; zone cards with Dust,
Vacuum and Mop; "Done, next job" while the timer runs; a job done earlier (web and chat); a page per chore; History by day.
Values are the starter list or invented."""
import html
import re
from datetime import timedelta

from app import home
from app.db import connect
from conftest import owner_sql
from test_home import age_days, cid, done_rows, say, starter, token, zid  # noqa: F401 - fixtures reused
from test_home_pick import jobs, pairs_of, start_timer, timer_row
from test_log import ANSWERS, P, call, fake  # noqa: F401
from test_web import act, n, web  # noqa: F401


def today():
    return owner_sql("SELECT log_day(now()) AS d")[0]["d"]


def rows_of(chore):
    return owner_sql("""SELECT z.name AS zone, d.day, d.source, d.dated, to_char(d.done_at AT TIME ZONE 'Europe/Berlin', 'HH24:MI') AS at
                        FROM home_done d JOIN home_zones z ON z.id = d.zone_id WHERE d.chore_id = %s ORDER BY d.id""", cid(chore))


def per_zone(web, chore, **fields):
    """The Per zone form of a whole-flat chore: every active zone on unless off=[names]; days/min = {zone name: value}."""
    off = fields.pop("off", [])
    form = {"chore": str(cid(chore))}
    for z in owner_sql("SELECT id, name FROM home_zones WHERE active"):
        if z["name"] not in off:
            form[f'on_{z["id"]}'] = "1"
        for k in ("days", "min"):
            if z["name"] in fields.get(k, {}):
                form[f'{k}_{z["id"]}'] = str(fields[k][z["name"]])
    return act(web, "/web/home/chorezones", **form)


# ---- the starting point card ----------------------------------------------------------------------------

def test_the_starting_point_card_gives_never_logged_jobs_an_age_that_is_not_a_session(starter, web):
    page = html.unescape(web[0].get("/web/home/zones").text)
    assert "When did you last do these?" in page and "Vacuum · 8 zones" in page and "This week" in page and "Don’t know" in page
    assert "more jobs after these" in page  # 20 chores never logged, 5 shown
    act(web, "/web/home/startpoint", pairs=",".join(f'{cid("Vacuum")}-{zid(z)}' for z in ("Kitchen", "Bathroom")), ago="week", key="s1")
    got = rows_of("Vacuum")
    assert [(r["zone"], today() - r["day"], r["source"], r["dated"], r["at"]) for r in got] == [
        ("Kitchen", timedelta(days=3), "start", True, "12:00"), ("Bathroom", timedelta(days=3), "start", True, "12:00")]
    act(web, "/web/home/startpoint", pairs=f'{cid("Bathtub")}-{zid("Bathroom")}', ago="longer", key="s2")
    assert today() - rows_of("Bathtub")[0]["day"] == timedelta(days=28)  # twice its interval
    act(web, "/web/home/startpoint", pairs=f'{cid("Window", "Bathroom")}-{zid("Bathroom")}', ago="month", key="s3")
    assert today() - rows_of("Window")[0]["day"] == timedelta(days=14)
    with connect() as conn, conn.cursor() as cur:
        assert home.week(cur)["this"] == 0 and home.recent(cur) == []  # not a session, not under Recently done
        ages = {(p["chore"]["name"], p["zone"]["name"]): p["age"] for p in home.load(cur)["pairs"]}
    assert ages[("Vacuum", "Kitchen")] == 3 and ages[("Vacuum", "Entrance")] is None
    assert "Recently done" not in web[0].get("/web/home").text
    assert "starting point" in web[0].get("/web/home/history").text


def test_dont_know_takes_the_job_off_the_card_and_undo_puts_it_back(starter, web):
    one = f'{cid("Bathtub")}-{zid("Bathroom")}'
    out = act(web, "/web/home/startpoint", pairs=one, ago="unknown", key="u1")
    assert "Left as not logged yet: Bathtub · Bathroom" in html.unescape(out.text) and done_rows() == []
    with connect() as conn, conn.cursor() as cur:
        assert all(p["chore"]["name"] != "Bathtub" for p in home.never_done(cur, home.load(cur)))
    act(web, "/web/undo/last", token=token(out.text))
    with connect() as conn, conn.cursor() as cur:
        assert any(p["chore"]["name"] == "Bathtub" for p in home.never_done(cur, home.load(cur)))


def test_a_logged_job_gets_no_starting_point(starter, web):
    act(web, "/web/home/tick", chore=str(cid("Bathtub")), zone=str(zid("Bathroom")), key="t1")
    act(web, "/web/home/startpoint", pairs=f'{cid("Bathtub")}-{zid("Bathroom")}', ago="week", key="s1")
    assert [r["source"] for r in rows_of("Bathtub")] == ["web"]


# ---- whole-flat chores per zone -------------------------------------------------------------------------------

def test_a_whole_flat_chore_can_be_off_in_a_zone_and_have_its_own_interval_and_minutes_there(starter, web):
    per_zone(web, "Vacuum", off=["Storage"], days={"Kitchen": 3}, min={"Bathroom": 5, "Kitchen": 10})
    c = owner_sql("SELECT zone_off, zone_days, zone_minutes FROM home_chores WHERE name = 'Vacuum'")[0]
    assert c == {"zone_off": [zid("Storage")], "zone_days": {str(zid("Kitchen")): 3}, "zone_minutes": {str(zid("Bathroom")): 5}}  # 10 = the chore's
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
    vac = {p["zone"]["name"]: p for p in d["pairs"] if p["chore"]["name"] == "Vacuum"}
    assert "Storage" not in vac and len(vac) == 7 and vac["Bathroom"]["minutes"] == 5 and vac["Kitchen"]["interval"] == 3
    zones = html.unescape(web[0].get("/web/home/zones").text)
    assert "· weekly; Kitchen every 3 days; not in Storage" in zones
    assert "every 2 weeks; Bath, Kitchen weekly" in zones  # Mop
    assert "That chore is not in the list." in act(web, "/web/home/tick", chore=str(cid("Vacuum")), zone=str(zid("Storage")), key="t1").text
    act(web, "/web/home/alldone", chore=str(cid("Vacuum")), key="a1")
    assert len(rows_of("Vacuum")) == 7 and "Vacuum · whole flat" in web[0].get("/web/home").text
    edit = web[0].get("/web/home/edit").text
    assert 'action="/web/home/chorezones"' in edit and f'name="on_{zid("Storage")}" value="1">' in edit and 'placeholder="7" value="3"' in edit


def test_a_whole_flat_chore_needs_one_zone_and_numbers_are_checked(starter, web):
    every = [z["name"] for z in owner_sql("SELECT name FROM home_zones")]
    assert "needs at least one zone" in per_zone(web, "Dust", off=every).text
    assert "whole numbers" in per_zone(web, "Dust", days={"Kitchen": 0}).text
    assert owner_sql("SELECT zone_off FROM home_chores WHERE name = 'Dust'")[0]["zone_off"] == []


def test_the_chat_leaves_out_a_zone_where_the_chore_is_off(call, fake, starter, web):
    per_zone(web, "Dust", off=["Storage"])
    out = say(call, "dusted everything")
    assert out["outcome"] == "saved" and "Home: Dust · whole flat" in out["message"] and len(rows_of("Dust")) == 7
    per_zone(web, "Vacuum", off=["Storage"])
    ask = say(call, "vacuumed")
    assert ask["outcome"] == "needs_choice" and "Storage" not in ask["message"]


def test_the_pick_page_offers_the_whole_flat_when_every_zone_fits(starter, web):
    per_zone(web, "Vacuum", min={z["name"]: 3 for z in owner_sql("SELECT name FROM home_zones")})
    got = jobs(30)
    assert got[1]["title"] == "Vacuum the whole flat" and len(got[1]["pairs"]) == 8 and got[1]["minutes"] == 24
    assert got[1]["steps"][0] == "Bathroom (3 min)" and all(j["title"] != "Vacuum the whole flat" for j in jobs(15))
    act(web, "/web/home/tick", chore=str(cid("Vacuum")), zone=str(zid("Kitchen")), key="t1")
    whole = next(j for j in jobs(30) if j["title"] == "Vacuum the whole flat")
    assert len(whole["pairs"]) == 7  # the zones still waiting today
    start_timer(web, whole, 30)
    page = web[0].get("/web/home/pick").text
    assert "Vacuum the whole flat" in page and "Bathroom (3 min)" in page
    act(web, "/web/home/pickdone", pairs=pairs_of(whole), timer=str(timer_row()["id"]), key="d1")
    assert len(rows_of("Vacuum")) == 8


# ---- zone cards -------------------------------------------------------------------------------------------------

def test_a_zone_card_lists_the_zones_dust_vacuum_and_mop_too(starter, web):
    page = web[0].get("/web/home/zones").text
    kitchen = page[page.index("<summary><b>Kitchen</b>"):]
    kitchen = kitchen[:kitchen.index("</details>")]
    names = re.findall(r'<a class="holink" href="/web/home/chore\?id=\d+"><b>([^<]+)</b>', kitchen)
    assert names[0] == "Dust" and names[-2:] == ["Vacuum", "Mop"] and "Scrub sink" in names and len(names) == 8
    act(web, "/web/home/tick", chore=str(cid("Dust")), zone=str(zid("Kitchen")), key="t1")  # the same as the Kitchen tile
    assert [r["zone"] for r in rows_of("Dust")] == ["Kitchen"]
    storage = web[0].get("/web/home/zones").text
    assert 'href="/web/ideas/todoist"' in storage  # Storage: Dust, Vacuum, Mop and the Todoist link


# ---- Done, next job -------------------------------------------------------------------------------------------------

def test_done_next_job_ticks_and_the_timer_goes_on_with_the_next_job(starter, web):
    first = jobs(15)[0]
    start_timer(web, first, 15)
    page = web[0].get("/web/home/pick").text
    assert "Done, next job" in page and 'action="/web/home/picknext" data-gone-at-zero' in page
    t = timer_row()
    out = act(web, "/web/home/picknext", pairs=pairs_of(first), timer=str(t["id"]), key="n1")
    assert "Saved: Vacuum · Bathroom. Next: " in html.unescape(out.text)
    assert [(r["chore"], r["zone"], r["source"]) for r in done_rows()] == [("Vacuum", "Bathroom", "pick")]
    t2 = timer_row()
    assert t2["id"] == t["id"] and not t2["closed"] and t2["ends_at"] == t["ends_at"] and t2["pairs"] != t["pairs"]
    nxt = html.unescape(web[0].get("/web/home/pick").text)
    assert "Vacuum the bathroom" not in nxt and "Done, next job" in nxt
    assert act(web, "/web/home/picknext", pairs=pairs_of(first), timer=str(t["id"]), key="n1").status_code == 200  # a double tap
    assert len(done_rows()) == 1


def test_done_next_job_with_little_time_left_closes_the_timer(starter, web):
    first = jobs(15)[0]
    start_timer(web, first, 15)
    owner_sql("UPDATE home_timers SET ends_at = now() + interval '2 minutes'")
    assert "Done, next job" not in web[0].get("/web/home/pick").text
    out = act(web, "/web/home/picknext", pairs=pairs_of(first), timer=str(timer_row()["id"]), key="n1")
    assert "the timer is closed" in out.text and timer_row()["closed"] and len(done_rows()) == 1


# ---- a job done earlier -------------------------------------------------------------------------------------------------

def test_a_job_done_earlier_gets_noon_of_that_day_and_history_shows_the_date_only(starter, web):
    assert "Done it earlier?" in web[0].get("/web/home/pick?min=15").text
    one = f'{cid("Bathtub")}-{zid("Bathroom")}'
    out = act(web, "/web/home/earlier", pairs=one, when="1", key="e1")
    y = today() - timedelta(days=1)
    assert f"Saved: Bathtub · Bathroom on {y:%d.%m.}" in html.unescape(out.text)
    assert [(r["day"], r["at"], r["dated"], r["source"]) for r in rows_of("Bathtub")] == [(y, "12:00", True, "web")]
    act(web, "/web/home/earlier", pairs=f'{cid("Dust")}-{zid("Kitchen")}', when=(today() - timedelta(days=9)).isoformat(), key="e2")
    assert today() - rows_of("Dust")[0]["day"] == timedelta(days=9)
    for bad in (today().isoformat(), (today() + timedelta(days=1)).isoformat(), (today() - timedelta(days=400)).isoformat(), "x"):
        assert "Pick a day before today" in act(web, "/web/home/earlier", pairs=one, when=bad, key="b" + bad).text
    hist = html.unescape(web[0].get("/web/home/history").text)
    assert f"{y:%A %d.%m.}" in hist and "+ Add one done earlier" in hist and '<td class="small"></td><td>Bathtub · Bathroom</td><td class="small">earlier</td>' in hist
    assert n("SELECT count(*) AS n FROM raw_inbox WHERE content LIKE 'home: Bathtub · Bathroom (done %%'") == 1


def test_typed_lines_say_when_without_the_ai(call, fake, starter):
    out = say(call, "vacuumed the kitchen yesterday")
    y = today() - timedelta(days=1)
    assert out["outcome"] == "saved" and f"Home: Vacuum · Kitchen ({y:%d.%m.})" in out["message"]
    assert [(r["day"], r["dated"], r["source"]) for r in rows_of("Vacuum")] == [(y, True, "chat")]
    say(call, "scrubbed the bathtub last night")
    assert rows_of("Bathtub")[0]["day"] == y
    say(call, "did laundry this morning")
    assert rows_of("Do laundry")[0]["day"] == today() and not rows_of("Do laundry")[0]["dated"]
    day = today()
    monday = day - timedelta(days=(day.weekday() - 0) % 7 or 7)
    say(call, "dusted the desk on monday")
    assert rows_of("Dust")[0]["day"] == monday and 1 <= (day - monday).days <= 7
    ask = say(call, "mopped last friday")
    assert ask["outcome"] == "needs_choice"
    pid = owner_sql("SELECT id FROM pending WHERE tool = 'home_choice' AND resolved_at IS NULL")[0]["id"]
    call("answer", question_id=pid, reply="Kitchen")
    friday = day - timedelta(days=(day.weekday() - 4) % 7 or 7)
    assert [(r["zone"], r["day"]) for r in rows_of("Mop")] == [("Kitchen", friday)]
    assert fake.calls == []


# ---- a chore's page and History ----------------------------------------------------------------------------------------

def test_a_chore_page_shows_its_dates_how_often_and_an_edit_link(starter, web):
    for days in (21, 14, 6, 0):
        age_days("Bathtub", "Bathroom", days)
    page = html.unescape(web[0].get(f'/web/home/chore?id={cid("Bathtub")}').text)
    assert "<h1>Bathtub</h1>" in page and "every 2 weeks · 15 min · Bathroom" in page and "Scrub the tub" in page
    assert "usually every 7 days" in page and f'href="/web/home/edit#chore-{cid("Bathtub")}"' in page
    assert page.count(f"{today():%d.%m.}") >= 1
    age_days("Dust", "Kitchen", 3)
    owner_sql("INSERT INTO home_done (chore_id, zone_id, day, source, dated) VALUES (%s, %s, log_day(now()) - 30, 'start', true)", cid("Dust"), zid("Bathroom"))
    dust = html.unescape(web[0].get(f'/web/home/chore?id={cid("Dust")}').text)
    assert "<b>Kitchen</b>" in dust and "(starting point)" in dust and "not logged yet" in dust and "whole flat" in dust
    assert "That chore is not in the list." in web[0].get("/web/home/chore?id=999").text
    assert f'href="/web/home/chore?id={cid("Vacuum")}"' in web[0].get("/web/home").text


def test_history_is_grouped_by_day_with_plain_words_and_show_more(starter, web):
    act(web, "/web/home/tick", chore=str(cid("Bathtub")), zone=str(zid("Bathroom")), key="t1")
    start_timer(web, jobs(15)[0])
    act(web, "/web/home/pickdone", pairs=pairs_of(jobs(15)[0]), key="d1")
    age_days("Dust", "Kitchen", 2)
    hist = html.unescape(web[0].get("/web/home/history").text)
    two = today() - timedelta(days=2)
    assert f'<h3 class="hoday">{today():%A %d.%m.}</h3>' in hist and f'<h3 class="hoday">{two:%A %d.%m.}</h3>' in hist
    assert ">ticked</td>" in hist and ">timer</td>" in hist and "Show more" not in hist
    owner_sql("""INSERT INTO home_done (chore_id, zone_id, done_at, day, source) SELECT %s, %s, now() - make_interval(days => g), log_day(now()) - g, 'web'
                 FROM generate_series(3, 80) g""", cid("Bathtub"), zid("Bathroom"))
    more = web[0].get("/web/home/history").text
    assert 'href="/web/home/history?n=120">Show more' in more and "last 60" in more
    assert "last 81" in web[0].get("/web/home/history?n=120").text
