"""The database's own bookkeeping (migration 010): meal numbers and the change log hold for every write path, also for
edits that bypass the logger (NocoDB, the web app, a psql session), done here as the owner login."""
import json

import pytest

from conftest import owner_sql
from test_log import call, fake  # noqa: F401 - fixtures reused


@pytest.fixture
def food():
    owner_sql("TRUNCATE change_log")
    raw = owner_sql("INSERT INTO raw_inbox (channel, content) VALUES ('nocodb', 'by hand') RETURNING id")[0]["id"]
    fid = owner_sql("""INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source)
                       VALUES ('Test oats', 'g', 380, 13, 7, 60, 'label') RETURNING id""")[0]["id"]
    return raw, fid


def entry(raw, fid, at):
    return owner_sql("""INSERT INTO food_entries (raw_id, food_id, amount, eaten_at, time_known, day, day_rule, extractor_version)
                        VALUES (%s, %s, 50, %s::timestamptz, true, '2026-10-20', 'wake', 'test') RETURNING id""",
                     raw, fid, f"2026-10-20 {at}+02")[0]["id"]


def meals():
    return [r["meal"] for r in owner_sql("SELECT meal FROM food_entries WHERE day = '2026-10-20' ORDER BY eaten_at, id")]


def test_meal_numbers_follow_edits_that_bypass_the_logger(food):
    raw, fid = food
    a, b, c = entry(raw, fid, "08:00"), entry(raw, fid, "08:30"), entry(raw, fid, "12:00")
    assert meals() == [1, 1, 2]  # nobody called assign_meals: the trigger did
    owner_sql("UPDATE food_entries SET eaten_at = '2026-10-20 09:00+02' WHERE id = %s", c)
    assert meals() == [1, 1, 1]
    owner_sql("UPDATE food_entries SET eaten_at = '2026-10-20 15:00+02' WHERE id = %s", c)
    owner_sql("DELETE FROM food_entries WHERE id = %s", a)
    assert meals() == [1, 2]


def test_the_change_log_names_rows_columns_and_login_but_no_values(food):
    raw, fid = food
    a = entry(raw, fid, "08:00")
    entry(raw, fid, "12:00")
    owner_sql("UPDATE food_entries SET amount = 70 WHERE id = %s", a)
    owner_sql("DELETE FROM food_entries WHERE id = %s", a)
    rows = owner_sql("SELECT * FROM change_log WHERE table_name = 'food_entries' ORDER BY id")
    assert [r["op"] for r in rows] == ["insert", "insert", "update", "delete"]
    assert rows[2]["row_id"] == a and {"amount", "kcal"} <= set(rows[2]["columns"]) and rows[2]["changed_by"] == "app_owner"
    assert set(rows[0]) == {"id", "at", "table_name", "row_id", "op", "columns", "changed_by", "raw_id"}  # no values anywhere
    assert "meal" not in sum((r["columns"] or [] for r in rows), [])  # the renumbering itself is not logged


def test_a_meal_set_by_hand_on_an_entry_without_time_is_logged(food):
    raw, fid = food
    e = owner_sql("""INSERT INTO food_entries (raw_id, food_id, amount, eaten_at, time_known, day, day_rule, extractor_version)
                     VALUES (%s, %s, 50, '2026-10-20 12:00+02', false, '2026-10-20', 'asked', 'test') RETURNING id""", raw, fid)[0]["id"]
    owner_sql("UPDATE food_entries SET meal = 3 WHERE id = %s", e)
    assert owner_sql("SELECT columns FROM change_log WHERE op = 'update' AND row_id = %s", e)[0]["columns"] == ["meal"]
    assert owner_sql("SELECT meal FROM food_entries WHERE id = %s", e)[0]["meal"] == 3  # no time: the 2-hour rule leaves it


def test_the_loggers_writes_are_logged_with_their_message(call, fake):
    owner_sql("TRUNCATE change_log")
    owner_sql("INSERT INTO foods (name, basis, kcal, protein, fat, carbs, source) VALUES ('Skyr natural', 'g', 63, 11, 0.2, 4, 'label')")
    call("log", text="150 g skyr, weight 82.4")
    raw = owner_sql("SELECT id FROM raw_inbox ORDER BY id DESC LIMIT 1")[0]["id"]
    rows = owner_sql("SELECT table_name, op, changed_by, raw_id FROM change_log WHERE table_name IN ('food_entries', 'measurements')")
    assert {r["table_name"] for r in rows} == {"food_entries", "measurements"}
    assert all(r["changed_by"] == "core_rw" and r["raw_id"] == raw for r in rows)
    assert "82.4" not in json.dumps(owner_sql("SELECT * FROM change_log"), default=str)