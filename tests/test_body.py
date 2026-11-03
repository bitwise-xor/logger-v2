"""Body measurements, T1 (03.11.2026; spec docs/specs/body-measurements-spec.md, the owner's answers in the doc "Logger v2:
status and web app plan", Current tab): five tape measurements typed as pairs and saved without the AI, the read-backs (a
second value on a health day, a jump of 4 cm or more, out of range), the Health card with its form, Today's due line,
Settings, and the Week and Recent lines. Every value here is invented."""
from datetime import date, datetime, time, timedelta

from app import logbook, migrate, tape
from app.when import BERLIN
from conftest import owner_sql
from test_log import ANSWERS, H, P, call, fake  # noqa: F401 - fixtures reused
from test_web import act, n, web  # noqa: F401

BODY = ("waist", "hips", "chest", "upper_arm", "thigh")
MEAS = """SELECT t.name, m.value1, m.day FROM measurements m JOIN log_types t ON t.id = m.type_id
          WHERE t.name IN ('waist','hips','chest','upper_arm','thigh') ORDER BY m.id"""


def today() -> date:
    return owner_sql("SELECT log_day(now()) AS d")[0]["d"]


def raw() -> int:
    return owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('web', 'test') RETURNING id")[0]["id"]


def seed(day: date, **vals) -> None:
    """A session as if measured at 08:00 on that day."""
    rid = raw()
    for k, v in vals.items():
        owner_sql("""INSERT INTO measurements (raw_id, type_id, day, event_at, time_known, value1, extractor_version)
                     SELECT %s, id, %s, %s, true, %s, 'test' FROM log_types WHERE name = %s""",
                  rid, day, datetime.combine(day, time(8), tzinfo=BERLIN), v, k)


def woke(day: date) -> None:
    owner_sql("""INSERT INTO events (raw_id, type_id, day, event_at, time_known, extractor_version)
                 SELECT %s, id, %s, %s, true, 'test' FROM log_types WHERE name = 'wake'""",
              raw(), day, datetime.combine(day, time(7), tzinfo=BERLIN))


def vals() -> list:
    return [(r["name"], float(r["value1"])) for r in owner_sql(MEAS)]


# ---- typed lines (no AI) ----------------------------------------------------------------------------

def test_lines_of_pairs_are_read_and_anything_else_is_not():
    assert tape.pairs("waist 92.5 hips 101") == [("waist", 92.5, False), ("hips", 101.0, False)]
    assert tape.pairs("Measured: waist 92,5 cm, hips 101cm and upper arm 33") == [
        ("waist", 92.5, False), ("hips", 101.0, False), ("upper arm", 33.0, False)]
    assert tape.pairs('waist 36 in, hips 40"') == [("waist", 36.0, True), ("hips", 40.0, True)]
    for text in ("", "waist", "92.5", "waist 92.5 at 8:00", "had a big lunch"):
        assert tape.pairs(text) is None, text


def test_a_typed_line_is_saved_without_the_ai(call, fake):
    out = call("log", text="waist 92.5 hips 101 chest 98 arm 33.5 thigh 57", idempotency_key="b1")
    assert out["outcome"] == "saved" and fake.calls == []
    assert vals() == [("waist", 92.5), ("hips", 101.0), ("chest", 98.0), ("upper_arm", 33.5), ("thigh", 57.0)]
    assert n("SELECT count(*) AS n FROM raw_inbox WHERE channel = 'mcp' AND content = %s", "waist 92.5 hips 101 chest 98 arm 33.5 thigh 57") == 1
    assert n("SELECT count(*) AS n FROM audit_log WHERE tool = 'log' AND detail->'ai' = '[]'::jsonb") == 1  # no AI usage recorded


def test_the_log_bar_reads_it_too_with_undo(web, fake):
    page = act(web, "/web/log", text="Waist 90,5", key="b2")
    assert "Saved." in page.text and 'action="/web/undo"' in page.text and fake.calls == []
    assert vals() == [("waist", 90.5)]


def test_inches_become_cm_and_the_receipt_says_so(call, fake):
    out = call("log", text="waist 36 in", idempotency_key="b3")
    assert out["outcome"] == "saved" and "waist 36 in = 91.4 cm" in out["message"] and vals() == [("waist", 91.4)]


def test_an_unknown_word_is_asked_about_and_the_rest_saved(call, fake):
    out = call("log", text="waist 92 nekc 40", idempotency_key="b4")
    assert out["outcome"] == "partly_saved" and fake.calls == [] and vals() == [("waist", 92.0)]
    assert "'nekc' is not a body measurement I know" in out["questions"][0]["question"]


def test_a_line_with_other_things_in_it_goes_to_the_ai(call, fake, monkeypatch):
    monkeypatch.setitem(ANSWERS, "waist 92, weight 80", P(health=[H("waist", 92), H("weight", 80)]))
    out = call("log", text="waist 92, weight 80", idempotency_key="b5")
    assert out["outcome"] == "saved" and fake.calls == [("waist 92, weight 80", "claude-haiku-5-5")]
    assert vals() == [("waist", 92.0)]


# ---- rules -----------------------------------------------------------------------------------------

def test_a_second_value_on_the_same_day_asks_and_yes_replaces_it(call, fake):
    call("log", text="waist 92", idempotency_key="b6")
    out = call("log", text="waist 93", idempotency_key="b7")
    assert out["outcome"] == "needs_confirmation" and "Waist already 92.0 on" in out["message"] and "Replace?" in out["message"]
    yes = call("answer", question_id=out["questions"][0]["question_id"], reply="yes")
    assert yes["outcome"] == "saved" and vals() == [("waist", 93.0)]


def test_a_jump_of_4_cm_asks_and_less_does_not(call, fake):
    seed(today() - timedelta(days=28), waist=92.0, hips=100.0)
    out = call("log", text="waist 96 hips 103.9", idempotency_key="b8")
    assert out["outcome"] == "needs_confirmation" and "Waist 96.0 cm is 4.0 cm away from last time (92.0). Right?" in out["message"]
    assert "hips 103.9:" not in out["message"] and len(vals()) == 2  # nothing of the message saved yet
    ok = call("log", text="hips 103.9", idempotency_key="b9")
    assert ok["outcome"] == "saved"


def test_a_value_out_of_range_asks(call, fake):
    out = call("log", text="arm 80", idempotency_key="b10")
    assert out["outcome"] == "needs_confirmation" and "outside the plausible range 15-70 cm" in out["message"] and vals() == []


def test_before_five_in_the_morning_counts_for_the_day_before(call, fake, monkeypatch):
    monkeypatch.setattr(logbook, "clock", lambda: datetime(2026, 3, 10, 4, 30, tzinfo=BERLIN))
    call("log", text="waist 92", idempotency_key="b11")
    assert owner_sql(MEAS)[0]["day"] == date(2026, 3, 9)


# ---- the Health card -------------------------------------------------------------------------------

def test_the_card_before_the_first_measurement(web):
    page = web[0].get("/web/health").text
    assert 'id="body"' in page and "No measurements yet." in page and "Measure</a>" in page
    assert page.index('id="c-mood"') < page.index('id="body"') < page.index("<h2>Entries")  # after the charts (owner's answer 6)
    form = web[0].get("/web/health?measure=1").text
    assert 'action="/web/body"' in form and "Morning, before eating" in form and form.count('class="brow"') == 5


def test_table_chips_chart_and_waist_to_height(web):
    t = today()
    seed(t - timedelta(days=56), waist=94.0, hips=102.0)
    seed(t - timedelta(days=28), waist=93.0, hips=102.0, chest=98.0)
    seed(t, waist=92.5, hips=102.5, chest=98.0)
    page = web[0].get("/web/health").text
    waist = page[page.index("<td>Waist</td>"):page.index("<td>Hips</td>")]
    assert ">92.5</a>" in waist and "−0.5" in waist and "−1.5" in waist
    chest = page[page.index("<td>Chest</td>"):]
    assert chest.count("±0") >= 1 and f"Start = change since {t - timedelta(days=56):%d.%m.%Y}" in page
    assert 'id="c-body"' in page and "Waist in cm" in page and 'class="chip on"' in page
    assert "Hips in cm" in web[0].get("/web/health?bx=hips").text
    assert "add your height in" in page and "BMI" not in page
    owner_sql("INSERT INTO body_height (valid_from, height_cm) VALUES (%s, 180)", t - timedelta(days=100))
    owner_sql("INSERT INTO body_height (valid_from, height_cm) VALUES (%s, 170)", t + timedelta(days=1))  # a later height: past ratios keep theirs
    page = web[0].get("/web/health").text
    assert "Waist ÷ height: <b>0.51</b>" in page and "Under 0.5 is the common target." in page


def test_the_form_saves_a_session_and_reads_back_a_jump(web, fake):
    t = today()
    seed(t - timedelta(days=28), waist=92.0, hips=100.0)
    empty = act(web, "/web/body", day=t.isoformat(), r="30", key="f1", waist="", hips="")
    assert "Nothing to save. Fill at least one field." in empty.text
    ask = act(web, "/web/body", day=t.isoformat(), r="30", key="f2", waist="97", hips="100,5")
    assert "Check: Waist 97.0 cm is 5.0 cm away from last time (92.0). Right?" in ask.text
    assert "Yes, save" in ask.text and "Fix it" in ask.text and len(vals()) == 2
    done = act(web, "/web/body", day=t.isoformat(), r="30", key="f3", waist="97", hips="100,5", confirmed="1")
    assert "Waist 97.0 added" in done.text and "Hips 100.5 added" in done.text and fake.calls == []
    assert vals()[2:] == [("waist", 97.0), ("hips", 100.5)]


def test_changing_and_clearing_a_past_session(web, fake):
    d = today() - timedelta(days=10)
    seed(d, waist=92.0, hips=100.0)
    form = web[0].get(f"/web/health?measure=1&mday={d.isoformat()}").text
    assert f"Changing the session of {d:%d.%m.%Y}" in form and 'value="92.0"' in form
    changed = act(web, "/web/body", day=d.isoformat(), r="30", key="f4", waist="92.5", hips="100")
    assert "Waist 92.0 → 92.5" in changed.text
    ask = act(web, "/web/body", day=d.isoformat(), r="30", key="f5", waist="92.5", hips="")
    assert "Delete on" in ask.text and "Hips 100.0?" in ask.text and len(vals()) == 2
    act(web, "/web/body", day=d.isoformat(), r="30", key="f6", waist="92.5", hips="", confirm_del="1")
    assert vals() == [("waist", 92.5)]


# ---- Today -----------------------------------------------------------------------------------------

def test_today_shows_the_due_line_after_waking_and_not_today_hides_it(web):
    t = today()
    assert "Body measurements" not in web[0].get("/web/").text  # not before the wake-up
    woke(t)
    page = web[0].get("/web/").text
    assert "not measured yet" in page and 'href="/web/health?measure=1#body"' in page
    seed(t - timedelta(days=29), waist=92.0)
    page = web[0].get("/web/").text
    assert "Body measurements due" in page and f"Last {t - timedelta(days=29):%d.%m.} · 29 days ago" in page
    assert "Body measurements due" not in act(web, "/web/body/skip").text
    owner_sql("DELETE FROM ui_settings")
    seed(t - timedelta(days=10), waist=92.5)  # measured since: not due
    assert "Body measurements" not in web[0].get("/web/").text


# ---- Settings, Week, Recent ------------------------------------------------------------------------

def test_settings_height_interval_and_hidden_measures(web):
    t = today()
    back = "/web/settings?tab=body"
    assert "Height" in web[0].get(back).text
    assert "Height: give a number from 120 to 230 cm." in act(web, "/web/settings/height", valid_from=t.isoformat(), height="300", back=back).text
    assert "Height 180.5 cm saved" in act(web, "/web/settings/height", valid_from=t.isoformat(), height="180,5", back=back).text
    assert n("SELECT count(*) AS n FROM body_height") == 1
    assert "from 7 to 90" in act(web, "/web/settings/body", interval="5", show="waist", back=back).text
    assert "Keep at least one measure." in act(web, "/web/settings/body", interval="14", back=back).text
    act(web, "/web/settings/body", interval="14", show=["waist", "hips"], back=back)
    assert web[0].get("/web/health?measure=1").text.count('class="brow"') == 2
    woke(t)
    seed(t - timedelta(days=15), waist=92.0)
    assert "Body measurements due" in web[0].get("/web/").text  # due after 14 days now


def test_week_and_recent_lines(web, fake):
    y = today() - timedelta(days=1)
    seed(y - timedelta(days=28), waist=94.0)
    act(web, "/web/body", day=y.isoformat(), r="30", key="f7", waist="92.5", hips="101")
    week = web[0].get("/web/week").text
    assert f"Measured {y:%d.%m.}: waist −1.5 cm since last" in week and "(2 values)" in week
    assert "Body measurements: waist 92.5, hips 101.0 cm (2 values)" in web[0].get("/web/recent").text


def test_migration_is_done_once_and_the_new_words_are_unique():
    assert migrate.run() == []
    rows = owner_sql("SELECT name, aliases FROM log_types")
    mine = {w for r in rows if r["name"] in BODY for w in [r["name"].replace("_", " "), *r["aliases"]]}
    others = {w for r in rows if r["name"] not in BODY for w in [r["name"].replace("_", " "), *r["aliases"]]}
    assert len(mine) == 14 and not mine & others
