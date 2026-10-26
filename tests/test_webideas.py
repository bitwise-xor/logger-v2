"""The Ideas and tasks page: thoughts (filters, Save as the yes, stage buttons with Undo, delete after one yes) and the
open Todoist tasks of Lists and the Inbox (tick off with Undo, text and section changed after a Yes, delete only what
the logger added, due dates; shopping is not shown)."""
import html
from datetime import timedelta

import pytest

from app import todos
from app.todoist import FakeTodoist
from conftest import owner_sql
from test_web import act, client, n, web  # noqa: F401
from test_webtoday import day, raw


def thought(summary, stage="inbox", tt="idea", area="projects", tags=(), text=None):
    return owner_sql("""INSERT INTO thoughts (raw_id, original_text, summary, stage, thought_type, area, tags)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                     raw(), text or summary.lower(), summary, stage, tt, area, list(tags))[0]["id"]


def row(tid):
    return owner_sql("SELECT summary, stage, thought_type, area, next_step, tags FROM thoughts WHERE id = %s", tid)[0]


def text(page):
    return html.unescape(page.text)


@pytest.fixture
def todo():
    L, I, S = todos.LISTS, todos.INBOX, todos.SHOPPING
    d = day()
    f = FakeTodoist(sections={L: [("l1", "Paperwork"), ("l2", "Home")]},
                    tasks=[dict(id="k1", content="Renew the passport", project_id=L, section_id="l1",
                                due={"date": (d - timedelta(days=2)).isoformat()}),
                           dict(id="k2", content="Descale the kettle", project_id=L, section_id="l2"),
                           dict(id="k3", content="Look into monitors", project_id=I, due={"date": (d + timedelta(days=3)).isoformat()}),
                           dict(id="k4", content="Milk | Lait | Milch", project_id=S),
                           dict(id="k5", content="Old done task", project_id=L, checked=True)])
    todos.CLIENT = f
    todos.forget()
    yield f
    todos.forget()


def test_the_pages_need_sign_in_and_the_app_host():
    assert client().get("/web/ideas").headers["location"] == "/web/login"
    assert client().get("/web/ideas/todoist").headers["location"] == "/web/login"
    assert client("log.example.test").get("/web/ideas").status_code == 404


def test_open_thoughts_newest_first_with_filters(web):
    a = thought("Weekly protein review", tags=["logger"])
    b = thought("Book the blood test", stage="active", tt="task", area="health", tags=["labs"])
    thought("Restore test passed", stage="done", tt="progress")
    c, _ = web
    page = c.get("/web/ideas").text
    assert 'href="/web/ideas" aria-current="page">Ideas and tasks' in page
    assert "2 thoughts" in page and page.index("Book the blood test") < page.index("Weekly protein review")
    assert "Restore test passed" not in page  # done is not open
    assert "Restore test passed" in c.get("/web/ideas?stage=done").text
    assert "Weekly protein review" not in c.get("/web/ideas?area=health").text
    assert "Book the blood test" not in c.get("/web/ideas?tag=logger").text
    assert "1 thought" in c.get("/web/ideas?q=blood").text
    opened = c.get(f"/web/ideas?open={b}").text
    assert "your words" in opened and 'action="/web/ideas/thought"' in opened and f'value="{a}"' not in opened


def test_save_is_the_yes_for_the_own_fields(web):
    tid = thought("Weekly protein review", tags=["logger"])
    page = act(web, "/web/ideas/thought", id=str(tid), act="save", summary="Weekly protein review in the app", thought_type="idea",
               area="health", stage="active", next_step="plan", tags=" #Logger, web app ,logger")
    assert "Saved:" in text(page)
    assert row(tid) == {"summary": "Weekly protein review in the app", "stage": "active", "thought_type": "idea", "area": "health",
                        "next_step": "plan", "tags": ["logger", "web app"]}
    assert "Nothing changed" in act(web, "/web/ideas/thought", id=str(tid), act="save", summary="Weekly protein review in the app",
                                    thought_type="idea", area="health", stage="active", next_step="plan", tags="logger, web app").text


def test_stage_buttons_work_at_once_with_undo(web):
    tid = thought("Book the blood test", stage="active", tt="task")
    page = act(web, "/web/ideas/thought", id=str(tid), act="stage", stage="done", back="/web/ideas")
    assert "from active to done" in text(page) and ">Undo</button>" in page.text and ">Close</a>" in page.text
    assert row(tid)["stage"] == "done"
    act(web, "/web/ideas/thought", id=str(tid), act="stage", stage="active")  # what the Undo button sends
    assert row(tid)["stage"] == "active"


def test_deleting_a_thought_asks_once(web):
    tid = thought("Old idea")
    page = act(web, "/web/ideas/thought", id=str(tid), act="delete")
    assert "Yes, delete" in page.text and n("SELECT count(*) AS n FROM thoughts") == 1
    act(web, "/web/ideas/thought", id=str(tid), act="delete", confirmed="1")
    assert n("SELECT count(*) AS n FROM thoughts") == 0


def test_bad_thought_input_changes_nothing(web):
    tid = thought("Weekly protein review")
    assert "Unknown thought" in act(web, "/web/ideas/thought", id="1; drop", act="save").text
    assert "not there any more" in act(web, "/web/ideas/thought", id="999999", act="save").text
    assert "cannot be empty" in act(web, "/web/ideas/thought", id=str(tid), act="save", summary=" ", thought_type="idea", stage="inbox").text
    assert "from the lists" in act(web, "/web/ideas/thought", id=str(tid), act="save", summary="x", thought_type="poem", stage="inbox").text
    assert "Unknown stage" in act(web, "/web/ideas/thought", id=str(tid), act="stage", stage="finished").text
    assert row(tid)["summary"] == "Weekly protein review"


def test_todoist_tasks_by_section_and_by_due_date(web, todo):
    c, _ = web
    page = c.get("/web/ideas/todoist").text
    assert "Renew the passport" in page and "Descale the kettle" in page and "Look into monitors" in page
    assert "Milk" not in page and "Old done task" not in page  # no shopping, no closed tasks
    assert "Lists › Paperwork" in page and "Inbox" in page and "overdue ·" in page
    assert page.index("Lists › Home") < page.index("Lists › Paperwork")  # sections by name
    by_due = c.get("/web/ideas/todoist?sort=due").text
    assert by_due.index("Renew the passport") < by_due.index("Look into monitors") < by_due.index("Descale the kettle")
    todo.down = True
    todos.forget()
    assert "Todoist did not answer" in c.get("/web/ideas/todoist").text


def test_tick_off_at_once_then_undo(web, todo):
    page = act(web, "/web/ideas/task", task="k2", act="close", back="/web/ideas/todoist")
    assert 'Ticked off "Descale the kettle"' in text(page) and ">Undo</button>" in page.text
    assert todo.tasks["k2"]["checked"] and "ticked off today" in page.text
    assert owner_sql("SELECT op, task_id FROM todo_actions") == [{"op": "closed", "task_id": "k2"}]
    page = act(web, "/web/ideas/task", task="k2", act="reopen", back="/web/ideas/todoist")
    assert "is back on the list" in text(page) and not todo.tasks["k2"]["checked"]
    assert "Only a task ticked off here" in text(act(web, "/web/ideas/task", task="k5", act="reopen"))


def test_text_and_section_change_after_a_yes(web, todo):
    form = dict(task="k2", act="edit", content="Descale the kettle and the iron", place="s:l1", back="/web/ideas/todoist")
    page = act(web, "/web/ideas/task", **form)
    assert "please confirm" in page.text and "Section: Lists › Home → Lists › Paperwork" in text(page)
    assert todo.tasks["k2"]["content"] == "Descale the kettle"
    act(web, "/web/ideas/task", **form, confirmed="1")
    assert (todo.tasks["k2"]["content"], todo.tasks["k2"]["section_id"]) == ("Descale the kettle and the iron", "l1")
    act(web, "/web/ideas/task", task="k2", act="edit", content="Descale the kettle and the iron", place=f"p:{todos.INBOX}", confirmed="1")
    assert (todo.tasks["k2"]["project_id"], todo.tasks["k2"]["section_id"]) == (todos.INBOX, None)
    assert [x[0] for x in todo.log] == ["update", "move", "move"]


def test_only_tasks_the_logger_added_can_be_deleted(web, todo):
    assert "Only tasks the logger added" in text(act(web, "/web/ideas/task", task="k2", act="delete"))
    owner_sql("INSERT INTO todo_actions (batch, op, item, task_id, content) VALUES ('b1', 'created', 'monitors', 'k3', 'Look into monitors')")
    page = act(web, "/web/ideas/task", task="k3", act="delete")
    assert "Yes, delete" in page.text and "k3" in todo.tasks
    act(web, "/web/ideas/task", task="k3", act="delete", confirmed="1")
    assert "k3" not in todo.tasks and "k2" in todo.tasks


def test_shopping_and_unknown_tasks_are_refused(web, todo):
    assert "not open in Lists or the Inbox" in text(act(web, "/web/ideas/task", task="k4", act="close"))  # shopping
    assert "Unknown task" in text(act(web, "/web/ideas/task", task="k1; drop", act="close"))
    assert "Pick a section" in text(act(web, "/web/ideas/task", task="k1", act="edit", content="x", place="s:nope"))
    todo.down = True
    todos.forget()
    assert "Todoist did not answer" in text(act(web, "/web/ideas/task", task="k1", act="close"))
    assert not todo.tasks["k4"]["checked"] and not todo.tasks["k1"]["checked"]
