import os
import sys

os.environ.update(DB_HOST="127.0.0.1", DB_PORT="55433", APP_OWNER_PASSWORD="testowner", CORE_RW_PASSWORD="testcore",
                  MCP_PATH="/mcp-testpath", ALLOWED_HOSTS="log.example.test", PUBLIC_URL="https://log.example.test", APP_HOST="app.example.test", PILOT_TOOLS="1")
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
        c.execute("TRUNCATE food_entries, food_units, food_defaults, foods, thoughts, thought_updates, thought_links, private.entries, measurements, events, raw_inbox, pending, idempotency_keys, audit_log, owner_credentials, oauth_clients, oauth_requests, oauth_codes, oauth_tokens, login_failures, web_sessions, trusted_devices RESTART IDENTITY CASCADE")
    yield


def owner_sql(sql, *args):
    with connect(owner=True) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else []


@pytest.fixture
def bearer():
    """An access token for MCP calls, issued directly (the sign-in flow itself is tested in test_auth)."""
    import hashlib
    import secrets
    tok = secrets.token_urlsafe(32)
    owner_sql("INSERT INTO oauth_tokens (token_hash,kind,client_id,scopes,expires_at) VALUES (%s,'access','c',ARRAY['log'], now()+interval '1 hour')",
              hashlib.sha256(tok.encode()).hexdigest())
    return {"Authorization": "Bearer " + tok}
