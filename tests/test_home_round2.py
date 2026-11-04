"""The Home page, round 2, step A of 04.11.2026 (plan in the doc, tab Current): a chore's own steps win over the zone's spots and the
title loses "area"; Dust, the zone's own jobs, Vacuum, Mop in a zone session; "Not today" also hides the job on Now and the Today
card; "floor" in typed lines; the countdown on Now, the wake lock and "Finished" at zero; "yearly"; a chore in a retired zone keeps
its zone; "All done" twice takes it back; "Job N of M". Values are the starter list or invented."""
from app import home
from app.db import connect
from conftest import owner_sql
from test_home import cid, done_rows, say, starter, token, zid  # noqa: F401 - fixtures reused
from test_home_pick import jobs, pairs_of, start_timer, timer_row
from test_log import ANSWERS, P, call, fake  # noqa: F401
from test_web import act, n, web  # noqa: F401


# ---- steps and titles ---------------------------------------------------------------------------------

def test_a_chores_own_steps_win_and_the_zones_spots_are_used_only_without_them(starter):
    owner_sql("UPDATE home_chores SET steps = %s WHERE name = 'Vacuum'", ["Floor and rugs", "Edges and corners", "Under the bed and furniture"])
    got = jobs(30)
    vac = next(j for j in got if j["title"].startswith("Vacuum"))
    assert vac["title"] == "Vacuum the bathroom" and vac["steps"] == ["Floor and rugs", "Edges and corners", "Under the bed and furniture"]
    dust = next(j for j in got if j["title"].startswith("Dust"))
    assert dust["title"] == "Dust the bathroom" and dust["steps"][:2] == ["Sink", "Bathtub"]  # Dust has no steps: the zone's spots
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
    dressing = next(p for p in d["pairs"] if p["chore"]["name"] == "Vacuum" and p["zone"]["name"] == "Dressing area")
    assert home.job_of(d, [(dressing["chore"]["id"], dressing["zone"]["id"])])["title"] == "Vacuum the dressing area"  # "area" only from the name


# ---- the order of a zone session -----------------------------------------------------------------------

def test_a_zone_session_goes_dust_own_jobs_vacuum_mop_also_for_a_running_timer(starter, web):
    session = jobs(60)[0]
    assert session["session"] and [s.split(" (")[0] for s in session["steps"]] == ["Dust", "Sink and toilet", "Vacuum", "Mop"]
    old_order = list(reversed(session["pairs"]))  # a timer started before this order
    act(web, "/web/home/start", pairs=home.pairs_value(old_order), minutes="60")
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
    assert home.job_of(d, old_order)["steps"] == session["steps"]
    assert "<li>Dust (15 min)</li><li>Sink and toilet (15 min)</li><li>Vacuum (10 min)</li><li>Mop (15 min)</li>" in web[0].get("/web/home/pick").text


# ---- Not today everywhere ------------------------------------------------------------------------------

def test_not_today_hides_the_job_on_now_and_on_the_today_card_too(starter, web):
    c = web[0]
    assert "hidden for today" not in c.get("/web/home").text and "hidden for today" not in c.get("/web/").text
    assert "<b>Vacuum · Bathroom</b>" in c.get("/web/").text
    out = act(web, "/web/home/skip", pairs=pairs_of(jobs(15)[0]))
    assert "Not today: Vacuum · Bathroom" in out.text
    now = c.get("/web/home").text
    assert "1 hidden for today" in now and "<b>Vacuum · Bathroom</b>" not in now and "<b>Vacuum · Entrance</b>" in now
    today = c.get("/web/").text
    assert "1 hidden for today" in today and "<b>Vacuum · Bathroom</b>" not in today and "<b>Vacuum · Entrance</b>" in today  # the next one waits longest
    act(web, "/web/undo/last", token=token(out.text))
    assert "hidden for today" not in c.get("/web/home").text and "<b>Vacuum · Bathroom</b>" in c.get("/web/").text


def test_the_today_card_says_so_when_everything_left_is_hidden(starter, web):
    owner_sql("INSERT INTO home_skips (chore_id, zone_id, day) SELECT c.id, z.id, log_day(now()) FROM home_chores c JOIN home_zones z "
              "ON c.zone_id IS NULL OR c.zone_id = z.id")
    page = web[0].get("/web/").text
    assert "Nothing else is waiting today." in page and "41 hidden for today" in page and "Open Home" in page


# ---- typed lines -------------------------------------------------------------------------------------------

def test_floor_is_a_filler_word_in_typed_lines(call, fake, starter):
    out = say(call, "vacuumed the kitchen floor")
    assert out["outcome"] == "saved" and [(r["chore"], r["zone"]) for r in done_rows()] == [("Vacuum", "Kitchen")]
    ask = say(call, "mopped the floors")
    assert ask["outcome"] == "needs_choice" and "Which zone: Mop?" in ask["message"] and fake.calls == []
    ANSWERS["cleaned the living room"] = P(thoughts=[])
    try:
        say(call, "cleaned the living room")  # no living room zone: the AI reads it, as before
    finally:
        del ANSWERS["cleaned the living room"]
    assert [c[0] for c in fake.calls] == ["cleaned the living room"]


def test_the_verbs_of_maintenance_jobs_are_known(call, fake, starter, web):
    act(web, "/web/home/chorenew", name="Water the plants", zone=str(zid("Workstation")), days="7", minutes="5", words="plants")
    act(web, "/web/home/chorenew", name="Bins and recycling", zone=str(zid("Kitchen")), days="7", minutes="5", words="bins, recycling")
    assert say(call, "watered the plants")["outcome"] == "saved"
    assert say(call, "took out the bins")["outcome"] == "saved"
    assert {(r["chore"], r["zone"]) for r in done_rows()} == {("Water the plants", "Workstation"), ("Bins and recycling", "Kitchen")} and fake.calls == []


# ---- the timer ------------------------------------------------------------------------------------------------

def test_the_now_timer_card_counts_down_and_the_pick_page_marks_what_changes_at_zero(starter, web):
    start_timer(web, jobs(15)[0])
    now = web[0].get("/web/home").text
    assert 'data-countdown="' in now and "left</span>" in now
    page = web[0].get("/web/home/pick").text
    assert 'action="/web/home/pause" data-gone-at-zero' in page and 'action="/web/home/stop" data-finish-at-zero' in page and "Stop timer" in page
    act(web, "/web/home/pause", timer=str(timer_row()["id"]))
    assert "data-countdown" not in web[0].get("/web/home").text and "(paused)" in web[0].get("/web/home").text
    act(web, "/web/home/resume", timer=str(timer_row()["id"]))
    owner_sql("UPDATE home_timers SET ends_at = now() - interval '1 second'")
    over = web[0].get("/web/home/pick").text
    assert ">Finished</button>" in over and "Stop timer" not in over and "Pause" not in over and "data-finish-at-zero" not in over
    assert "time is up" in web[0].get("/web/home").text and "data-countdown" not in web[0].get("/web/home").text
    assert "Timer closed." in act(web, "/web/home/stop", timer=str(timer_row()["id"])).text
    with connect() as conn, conn.cursor() as cur:
        assert home.week(cur)["this"] == 1  # it ran out: still a session


def test_the_script_keeps_the_screen_on_and_counts_down_on_now():
    from pathlib import Path
    js = (Path(home.__file__).parent / "static" / "app.js").read_text()
    assert 'navigator.wakeLock.request("screen")' in js and "visibilitychange" in js and "lock.release()" in js
    assert "function setupCountdown" in js and "[data-countdown]" in js and "[data-gone-at-zero]" in js and '"Finished"' in js


# ---- intervals ---------------------------------------------------------------------------------------------

def test_long_intervals_read_yearly_and_every_2_years():
    assert [home.interval_text(x) for x in (180, 365, 730, 400)] == ["every 6 months", "yearly", "every 2 years", "every 400 days"]


# ---- small bugs ------------------------------------------------------------------------------------------------

def test_editing_a_chore_in_a_retired_zone_keeps_its_zone(starter, web):
    act(web, "/web/home/zoneactive", zone=str(zid("Laundry")), on="0")
    page = web[0].get("/web/home/edit").text
    lid = zid("Laundry")
    assert f'<option value="{lid}" selected>Laundry (retired)</option>' in page
    assert page.count(f'<option value="{lid}"') == 2  # the two Laundry chores' forms only; not in "+ New chore" or other chores
    chore = cid("Do laundry")
    act(web, "/web/home/choreedit", chore=str(chore), name="Do laundry", zone=str(lid), days="7", minutes="25", steps="Wash", howto="", words="laundry")
    assert owner_sql("SELECT zone_id, minutes FROM home_chores WHERE id = %s", chore)[0] == {"zone_id": lid, "minutes": 25}
    assert "Pick a zone" in act(web, "/web/home/chorenew", name="x", zone=str(lid), days="7", minutes="10").text  # a new one: active zones only


def test_all_done_a_second_time_the_same_day_takes_it_back(starter, web):
    dust = str(cid("Dust"))
    act(web, "/web/home/tick", chore=dust, zone=str(zid("Kitchen")), key="k1")
    first = act(web, "/web/home/alldone", chore=dust, key="a1")
    assert "Saved: Dust · whole flat" in first.text and len(done_rows()) == 8  # the kitchen was ticked already: 7 more
    assert "All done ✓" in web[0].get("/web/home/zones").text and 'aria-pressed="true"' in web[0].get("/web/home/zones").text
    assert len(done_rows()) == 8 and act(web, "/web/home/alldone", chore=dust, key="a1").status_code == 200  # a double tap: saved once
    assert len(done_rows()) == 8
    back = act(web, "/web/home/alldone", chore=dust, key="a2")
    assert "Ticked off: Dust · whole flat" in back.text and done_rows() == []
    assert "All done ✓" not in web[0].get("/web/home/zones").text
    act(web, "/web/undo/last", token=token(back.text))
    assert len(done_rows()) == 8 and n("SELECT count(*) AS n FROM raw_inbox") == 2
