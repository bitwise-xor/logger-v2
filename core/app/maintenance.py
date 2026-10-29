"""Housekeeping: rows that only matter for a short time are removed once a day, so the tables do not grow for ever.
- idempotency keys older than 30 days (a retry of the same message comes within minutes)
- questions (pending) that were answered or expired more than 30 days ago
Everything else (entries, the audit log, the change log) is history and stays."""
import logging
import threading
import time

from .db import connect

log = logging.getLogger("logger.maintenance")
KEY_DAYS = 30
PENDING_DAYS = 30
EVERY_SECONDS = 24 * 3600
FIRST_AFTER_SECONDS = 120  # after a start, once the app has settled


def cleanup() -> dict:
    """-> how many rows of each kind were removed."""
    with connect() as c:
        keys = c.execute("DELETE FROM idempotency_keys WHERE created_at < now() - make_interval(days => %s)", (KEY_DAYS,)).rowcount
        pend = c.execute("""DELETE FROM pending WHERE coalesce(resolved_at, expires_at) < now() - make_interval(days => %s)
                            AND (resolved_at IS NOT NULL OR expires_at < now())""", (PENDING_DAYS,)).rowcount
    return {"idempotency_keys": keys, "pending": pend}


def _loop() -> None:
    time.sleep(FIRST_AFTER_SECONDS)
    while True:
        try:
            done = cleanup()
            log.info("clean-up done: %s", done)
        except Exception as e:  # noqa: BLE001 - a failed clean-up must never stop the app
            log.error("clean-up failed: %s", type(e).__name__)
        time.sleep(EVERY_SECONDS)


def start() -> None:
    threading.Thread(target=_loop, name="maintenance", daemon=True).start()
