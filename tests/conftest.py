import os
import sys

os.environ.update(DB_HOST="127.0.0.1", DB_PORT="55433", APP_OWNER_PASSWORD="testowner", CORE_RW_PASSWORD="testcore",
                  MCP_PATH="/mcp-testpath", ALLOWED_HOSTS="log.example.test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import pytest  # noqa: E402

from app import migrate  # noqa: E402
from app.db import connect  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    migrate.run()


@pytest.fixture(autouse=True)
def clean():
    with connect(owner=True) as c:
        c.execute("TRUNCATE measurements, events, raw_inbox, pending, idempotency_keys, audit_log RESTART IDENTITY CASCADE")
    yield


def owner_sql(sql, *args):
    with connect(owner=True) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else []
