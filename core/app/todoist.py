"""Todoist behind one small interface, so tests (and a later replacement) can swap it. TodoistAPI talks to Todoist's
current API (api/v1; the old REST v2 answers 410 since 2026). FakeTodoist keeps tasks in memory for tests and replays."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

BASE = "https://api.todoist.com/api/v1"


class TodoistError(Exception):
    pass


class Todoist(Protocol):
    def open_tasks(self, project_id: str) -> list[dict]: ...
    def completed_tasks(self, project_id: str, days: int = 90) -> list[dict]: ...
    def filter_tasks(self, query: str) -> list[dict]: ...
    def sections(self, project_id: str) -> list[dict]: ...
    def add(self, content: str, project_id: str, section_id: Optional[str] = None, labels=()) -> dict: ...
    def close(self, task_id: str) -> None: ...
    def reopen(self, task_id: str) -> None: ...
    def delete(self, task_id: str) -> None: ...
    def update(self, task_id: str, content: str) -> None: ...
    def set_due(self, task_id: str, due_date: Optional[str]) -> None: ...
    def move(self, task_id: str, section_id: Optional[str] = None, project_id: Optional[str] = None) -> None: ...


class TodoistAPI:
    def __init__(self, token: str, timeout: float = 15):
        self.token, self.timeout = token, timeout

    def _call(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None):
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, **({"Content-Type": "application/json"} if data else {})})
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                    time.sleep(1.5)
                    continue
                raise TodoistError(f"Todoist answered {e.code} for {method} {path}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 1:
                    time.sleep(1.5)
                    continue
                raise TodoistError(f"Todoist is not reachable ({type(e).__name__})") from e

    def _pages(self, path: str, params: dict, key: str = "results") -> list[dict]:
        out, cursor = [], None
        while True:
            body = self._call("GET", path, {**params, **({"cursor": cursor} if cursor else {})})
            out += body.get(key) or []
            cursor = body.get("next_cursor")
            if not cursor or len(out) >= 1000:
                return out

    def open_tasks(self, project_id: str) -> list[dict]:
        return self._pages("/tasks", {"project_id": project_id, "limit": 200})

    def completed_tasks(self, project_id: str, days: int = 90) -> list[dict]:
        now = datetime.now(timezone.utc)
        return self._pages("/tasks/completed/by_completion_date", {
            "project_id": project_id, "limit": 200, "since": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": now.strftime("%Y-%m-%dT%H:%M:%SZ")}, key="items")

    def filter_tasks(self, query: str) -> list[dict]:
        return self._pages("/tasks/filter", {"query": query, "limit": 200})

    def sections(self, project_id: str) -> list[dict]:
        return self._pages("/sections", {"project_id": project_id, "limit": 200})

    def add(self, content: str, project_id: str, section_id: Optional[str] = None, labels=()) -> dict:
        body = {"content": content, "project_id": project_id, **({"section_id": section_id} if section_id else {}),
                **({"labels": list(labels)} if labels else {})}
        return self._call("POST", "/tasks", body=body)

    def close(self, task_id: str) -> None:
        self._call("POST", f"/tasks/{task_id}/close")

    def reopen(self, task_id: str) -> None:
        self._call("POST", f"/tasks/{task_id}/reopen")

    def delete(self, task_id: str) -> None:
        self._call("DELETE", f"/tasks/{task_id}")

    def update(self, task_id: str, content: str) -> None:
        self._call("POST", f"/tasks/{task_id}", body={"content": content})

    def set_due(self, task_id: str, due_date: Optional[str]) -> None:
        """due_date: YYYY-MM-DD, or None to remove the due date."""
        self._call("POST", f"/tasks/{task_id}", body={"due_date": due_date} if due_date else {"due_string": "no date"})

    def move(self, task_id: str, section_id: Optional[str] = None, project_id: Optional[str] = None) -> None:
        self._call("POST", f"/tasks/{task_id}/move", body={"section_id": section_id} if section_id else {"project_id": project_id})


class FakeTodoist:
    """In memory: tasks are dicts with id, content, project_id, section_id, labels, checked, due."""

    def __init__(self, sections: dict[str, list[tuple[str, str]]] = None, tasks: list[dict] = ()):
        self.secs = sections or {}
        self.tasks = {t["id"]: {"section_id": None, "labels": [], "checked": False, "due": None, **t} for t in tasks}
        self.log, self.down = [], False
        self._n = 1000

    def _up(self):
        if self.down:
            raise TodoistError("Todoist is not reachable (fake)")

    def open_tasks(self, project_id):
        self._up()
        return [dict(t) for t in self.tasks.values() if t["project_id"] == project_id and not t["checked"]]

    def completed_tasks(self, project_id, days=90):
        self._up()
        return [dict(t) for t in self.tasks.values() if t["project_id"] == project_id and t["checked"]]

    def filter_tasks(self, query):
        self._up()
        return [dict(t) for t in self.tasks.values() if not t["checked"] and t.get("due")]

    def sections(self, project_id):
        self._up()
        return [{"id": i, "name": n, "project_id": project_id} for i, n in self.secs.get(project_id, [])]

    def add(self, content, project_id, section_id=None, labels=()):
        self._up()
        self._n += 1
        t = {"id": f"t{self._n}", "content": content, "project_id": project_id, "section_id": section_id,
             "labels": list(labels), "checked": False, "due": None}
        self.tasks[t["id"]] = t
        self.log.append(("add", t["id"], content))
        return dict(t)

    def close(self, task_id):
        self._up()
        self.tasks[task_id]["checked"] = True
        self.log.append(("close", task_id))

    def reopen(self, task_id):
        self._up()
        self.tasks[task_id]["checked"] = False
        self.log.append(("reopen", task_id))

    def delete(self, task_id):
        self._up()
        self.tasks.pop(task_id)
        self.log.append(("delete", task_id))

    def update(self, task_id, content):
        self._up()
        self.tasks[task_id]["content"] = content
        self.log.append(("update", task_id, content))

    def set_due(self, task_id, due_date):
        self._up()
        self.tasks[task_id]["due"] = {"date": due_date} if due_date else None
        self.log.append(("due", task_id, due_date))

    def move(self, task_id, section_id=None, project_id=None):
        self._up()
        t = self.tasks[task_id]
        if section_id:
            t["section_id"] = section_id
            t["project_id"] = next(p for p, secs in self.secs.items() if any(i == section_id for i, _ in secs))
        else:
            t["section_id"], t["project_id"] = None, project_id
        self.log.append(("move", task_id, section_id or project_id))