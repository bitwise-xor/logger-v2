"""The Home page, step 2 of 03.11.2026 (plan in the doc, tab Current): the jobs that fit a time, "Another job", "Not today" with Undo,
the zone session at 1 hour, the timer kept on the server (start, pause, resume, stop, run out), "Done" on the pick page, the sessions of
the weekly goal and the goal itself, the time chips and week card on Now and the Today card's button. Values are the starter list or
invented."""
import re
from datetime import timedelta

from app import home
from app.db import connect
from conftest import owner_sql
from test_home import age_days, cid, done_rows, starter, token, zid  # noqa: F401 - fixtures reused
from test_web import act, n, web  # noqa: F401


def jobs(minutes=15):
    """The jobs as the pick page offers them: without what is hidden for today."""
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
        return home.candidates(d, minutes, home.skipped(cur, d["today"]))


def pairs_of(job):
    return home.pairs_value(job["pairs"])


def timer_row():
    return owner_sql("SELECT *, ends_at IS NULL AS paused FROM home_timers ORDER BY id DESC LIMIT 1")[0]


def start_timer(web, job, minutes=15):
    return act(web, "/web/home/start", pairs=pairs_of(job), minutes=str(minutes))


# ---- which jobs fit ----------------------------------------------------------------------------------

def test_the_jobs_that_fit_come_one_per_chore_in_the_order_of_the_list(starter):
    got = jobs(15)
    assert got[0]["title"] == "Vacuum the bathroom" and got[0]["zone"] == "Bathroom" and got[0]["age"] is None and not got[0]["over"]
    assert [j["title"] for j in got].count("Vacuum the bathroom") == 1 and len({tuple(j["pairs"]) for j in got}) == len(got)
    assert all(j["minutes"] <= 15 for j in got) and len(got) == n("SELECT count(*) AS n FROM home_chores WHERE minutes <= 15")
    assert len(jobs(30)) == 20 and len(jobs(10)) < len(got)
    assert got[0]["steps"][:2] == ["Sink", "Bathtub"]  # a whole-flat chore without steps shows the zone's spots; a zone chore its own steps
    sink = next(j for j in got if j["title"] == "Sink and toilet")
    assert sink["steps"] == ["Sink and tap", "Toilet seat and bowl", "Floor around the toilet"] and sink["howto"] is None
    assert next(j for j in jobs(30) if j["title"] == "Do laundry")["howto"] == "Wash, dry, put away."


def test_nothing_fits_five_minutes_so_the_shortest_job_is_offered_with_a_note(starter, web):
    got = jobs(5)
    assert len(got) == 1 and got[0]["over"] and got[0]["minutes"] == 10 and got[0]["title"] == "Vacuum the bathroom"
    page = web[0].get("/web/home/pick?min=5").text
    assert "Your 5 minutes" in page and "This job takes about 10 minutes: do what fits, stopping counts." in page


def test_what_was_done_today_and_what_is_hidden_today_are_left_out(starter, web):
    act(web, "/web/home/tick", chore=str(cid("Vacuum")), zone=str(zid("Bathroom")), key="t1")
    assert jobs(15)[0]["title"] == "Vacuum the entrance"  # the next zone of the same chore
    first = jobs(15)[0]
    out = act(web, "/web/home/skip", pairs=pairs_of(first))
    assert "Not today: Vacuum · Entrance" in out.text and jobs(15)[0]["title"] == "Vacuum the dressing area"
    act(web, "/web/undo/last", token=token(out.text))
    assert jobs(15)[0]["title"] == "Vacuum the entrance" and n("SELECT count(*) AS n FROM home_skips") == 0
    # hidden for one logging day only
    owner_sql("INSERT INTO home_skips (chore_id, zone_id, day) VALUES (%s, %s, log_day(now()) - 1)", cid("Vacuum"), zid("Entrance"))
    with connect() as conn, conn.cursor() as cur:
        assert home.skipped(cur, home.today(cur)) == set()
    assert "not in the list" in act(web, "/web/home/skip", pairs="999-999").text


def test_one_hour_offers_a_zone_session_and_done_ticks_all_of_it(starter, web):
    got = jobs(60)
    assert got[0]["session"] and got[0]["title"] == "Bathroom: 4 jobs" and got[0]["minutes"] == 55 and got[0]["zone"] == "Bathroom"
    assert got[0]["steps"] == ["Dust (15 min)", "Sink and toilet (15 min)", "Vacuum (10 min)", "Mop (15 min)"] and not got[1]["session"] and got[1]["title"] == "Vacuum the bathroom"
    page = web[0].get("/web/home/pick?min=60").text
    assert "Your hour" in page and "Bathroom: 4 jobs" in page
    out = act(web, "/web/home/pickdone", pairs=pairs_of(got[0]), key="d1")
    assert "Saved:" in out.text and len(done_rows()) == 4 and {r["source"] for r in done_rows()} == {"pick"}
    assert {r["zone"] for r in done_rows()} == {"Bathroom"} and n("SELECT count(*) AS n FROM raw_inbox") == 1
    act(web, "/web/undo/last", token=token(out.text))
    assert done_rows() == []
    assert jobs(30)[0]["session"] is False  # a zone session is for an hour only


def test_the_pick_page_shows_the_job_its_steps_and_the_next_ones(starter, web):
    c = web[0]
    page = c.get("/web/home/pick?min=15").text
    assert "Your 15 minutes" in page and "Vacuum the bathroom" in page and "<li>Sink</li>" in page and "not logged yet" in page
    assert "Start timer" in page and "15:00" in page and ">Done<" in page and "Another job" in page and "Not today" in page
    main = page[page.index("‹ Home"):]  # the page's own part, without the frame's Log button
    assert main.count('class="primary"') == 1 and '<button class="ghost" type="submit">Done</button>' in main  # Start timer is the one main button
    assert f"Job 1 of {len(jobs(15))}" in page
    assert "Also fits in 15 minutes" in page and 'href="/web/home/pick?min=15&n=1"' in page
    second = c.get("/web/home/pick?min=15&n=1").text
    assert jobs(15)[1]["title"] in second and "Vacuum the bathroom</h2>" not in second and f"Job 2 of {len(jobs(15))}" in second
    wrap = c.get(f"/web/home/pick?min=15&n={len(jobs(15))}").text  # past the end: back to the first
    assert "Vacuum the bathroom</h2>" in wrap
    assert "Your 15 minutes" in c.get("/web/home/pick?min=99&n=x").text  # junk falls back to the default


def test_done_on_the_pick_page_ticks_and_leaves_what_is_done_already(starter, web):
    job = jobs(15)[0]
    act(web, "/web/home/tick", chore=str(cid("Vacuum")), zone=str(zid("Bathroom")), key="t1")
    assert "Already ticked today." in act(web, "/web/home/pickdone", pairs=pairs_of(job), key="d2").text
    assert len(done_rows()) == 1
    assert "not in the list" in act(web, "/web/home/pickdone", pairs="999-999", key="d3").text
    two = jobs(15)[0]
    out = act(web, "/web/home/pickdone", pairs=pairs_of(two), key="d4")
    assert "Saved: Vacuum · Entrance" in out.text and [r["source"] for r in done_rows()] == ["web", "pick"]
    assert owner_sql("SELECT content FROM raw_inbox ORDER BY id DESC LIMIT 1")[0]["content"] == "home: Vacuum · Entrance"


# ---- the timer ---------------------------------------------------------------------------------------

def test_the_timer_is_kept_on_the_server_and_can_be_paused_resumed_and_stopped(starter, web):
    job = jobs(15)[0]
    assert "Timer started: 15 minutes." in start_timer(web, job).text
    t = timer_row()
    assert t["minutes"] == 15 and t["pairs"] == [list(job["pairs"][0])] and not t["paused"]
    page = web[0].get("/web/home/pick?min=30").text  # the running timer wins over the chosen time
    assert re.search(r'class="hoclock" data-left="(89\d|900)"', page) and "Pause" in page and "Start timer" not in page and "Another job" not in page
    assert "Vacuum the bathroom" in page and "Stop timer" in page and ">Done<" in page
    act(web, "/web/home/pause", timer=str(t["id"]))
    t = timer_row()
    assert t["paused"] and 880 <= t["left_s"] <= 900
    page = web[0].get("/web/home/pick").text
    assert "Resume" in page and "Paused." in page and "data-left" not in page
    assert "not running" in act(web, "/web/home/pause", timer=str(t["id"])).text
    act(web, "/web/home/resume", timer=str(t["id"]))
    t = timer_row()
    assert not t["paused"] and t["left_s"] is None
    assert "not paused" in act(web, "/web/home/resume", timer=str(t["id"])).text
    assert "Timer closed." in act(web, "/web/home/stop", timer=str(t["id"])).text
    t = timer_row()
    assert t["closed"] and t["ends_at"] is None  # stopped before the end: it does not count as run out
    assert "already closed" in act(web, "/web/home/stop", timer=str(t["id"])).text
    assert "Start timer" in web[0].get("/web/home/pick").text  # back to picking


def test_a_new_timer_closes_the_old_one_and_a_bad_job_or_time_is_refused(starter, web):
    start_timer(web, jobs(15)[0])
    start_timer(web, jobs(15)[1], 30)
    assert [r["closed"] for r in owner_sql("SELECT closed FROM home_timers ORDER BY id")] == [True, False]
    assert "not in the list" in act(web, "/web/home/start", pairs="999-999", minutes="15").text
    assert "not in the list" in act(web, "/web/home/start", pairs=pairs_of(jobs(15)[0]), minutes="0").text


def test_a_timer_that_ran_out_says_so_counts_as_a_session_and_done_closes_it(starter, web):
    job = jobs(15)[0]
    start_timer(web, job)
    owner_sql("UPDATE home_timers SET ends_at = now() - interval '1 minute'")
    page = web[0].get("/web/home/pick").text
    assert "Time is up · well done" in page and 'class="hoclock over"' in page and "Pause" not in page
    with connect() as conn, conn.cursor() as cur:
        assert home.week(cur)["this"] == 1 and home.timer_open(cur)["state"] == "over"
    out = act(web, "/web/home/pickdone", pairs=pairs_of(job), timer=str(timer_row()["id"]), key="d5")
    assert "Saved:" in out.text and timer_row()["closed"]
    act(web, "/web/undo/last", token=token(out.text))
    with connect() as conn, conn.cursor() as cur:
        assert home.week(cur)["this"] == 1  # the timer ran out: still a session after the tick is undone


def test_an_old_timer_is_not_offered_again(starter, web):
    start_timer(web, jobs(15)[0])
    owner_sql("UPDATE home_timers SET started_at = now() - interval '7 hours', ends_at = now() - interval '6 hours'")
    assert "Start timer" in web[0].get("/web/home/pick").text


# ---- sessions and the goal -----------------------------------------------------------------------------

def test_a_session_is_a_day_with_a_job_done_or_a_timer_run_out_one_a_day_at_most(starter, web):
    with connect() as conn, conn.cursor() as cur:
        today = home.today(cur)
        assert home.week(cur) == {"goal": 3, "this": 0, "weeks": [0, 0, 0, 0]}
    wd = today.weekday()
    act(web, "/web/home/alldone", chore=str(cid("Dust")), key="a1")  # eight rows, one day: one session
    age_days("Vacuum", "Kitchen", wd + 3)  # the Friday before this week
    age_days("Mop", "Kitchen", wd + 4)  # the same week, another day
    age_days("Mop", "Workstation", wd + 10)  # two weeks before
    start_timer(web, jobs(15)[0])
    owner_sql("UPDATE home_timers SET day = %s, ends_at = now() - interval '1 minute'", today - timedelta(days=wd + 17))  # three weeks before, ran out
    with connect() as conn, conn.cursor() as cur:
        assert home.week(cur) == {"goal": 3, "this": 1, "weeks": [2, 1, 1, 0]}
    owner_sql("UPDATE home_timers SET ends_at = NULL, left_s = 100")  # paused: not a session
    with connect() as conn, conn.cursor() as cur:
        assert home.week(cur)["weeks"] == [2, 1, 0, 0]


def test_the_weekly_goal_is_set_on_edit_zones(starter, web):
    assert "Weekly goal" in web[0].get("/web/home/edit").text and 'name="goal"' in web[0].get("/web/home/edit").text
    assert "Weekly goal: 5 sessions." in act(web, "/web/home/goalset", goal="5").text
    with connect() as conn, conn.cursor() as cur:
        assert home.goal(cur) == 5
    for bad in ("0", "8", "x", ""):
        assert "1 to 7 sessions" in act(web, "/web/home/goalset", goal=bad).text
    assert home.GOAL == 3 and n("SELECT count(*) AS n FROM ui_settings WHERE key = 'home'") == 1


# ---- Now and Today ------------------------------------------------------------------------------------

def test_now_has_the_time_chips_the_week_card_and_a_banner_for_a_running_timer(starter, web):
    c = web[0]
    page = c.get("/web/home").text
    assert page.count('<a class="hochip') == 4 and 'class="hochip on" href="/web/home?min=15"' in page and ">1 hour<" in page
    assert 'href="/web/home/pick?min=15">Pick a job for me' in page and "0 of 3 sessions" in page and page.count('class="hoseg') == 3
    assert "Last 4 weeks: 0 · 0 · 0 · 0" in page and "Evening resets count on Habits." in page and "Timer</span>" not in page
    assert 'class="hochip on" href="/web/home?min=30"' in c.get("/web/home?min=30").text
    assert 'href="/web/home/pick?min=30">Pick a job for me' in c.get("/web/home?min=30").text
    act(web, "/web/home/alldone", chore=str(cid("Dust")), key="a1")
    act(web, "/web/home/goalset", goal="2")
    page = c.get("/web/home").text
    assert "1 of 2 sessions" in page and page.count('class="hoseg on"') == 1 and page.count('class="hoseg') == 2
    start_timer(web, jobs(15)[0])
    banner = c.get("/web/home").text
    assert "Timer</span>" in banner and "left" in banner and 'href="/web/home/pick">Open the timer' in banner


def test_the_today_card_has_this_weeks_sessions_and_a_15_minutes_button(starter, web):
    c = web[0]
    page = c.get("/web/").text
    assert "this week 0 of 3" in page and 'href="/web/home/pick?min=15">15 minutes' in page and 'href="/web/home">Open Home' in page
    act(web, "/web/home/alldone", chore=str(cid("Dust")), key="a1")
    assert "this week 1 of 3" in c.get("/web/").text


def test_the_countdown_script_is_served_and_the_page_hooks_into_it(starter, web):
    js = web[0].get("/web/static/app.js").text
    assert "function setupClock" in js and '.hoclock[data-left]' in js and "data-over" in js
    start_timer(web, jobs(15)[0])
    assert "data-over hidden" in web[0].get("/web/home/pick").text
