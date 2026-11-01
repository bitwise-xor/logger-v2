"""Never lose a message (01.11.2026, core items 1 and 2): a typed message is kept in `private.incoming` before the AI
reads it, and deleted in the transaction that saves what it said (logbook.read_held). When the AI or the save fails it
stays "not read yet": cron reads it again every 5 minutes for 2 hours (`docker exec logger-v2-core python -m app.held`),
then the phone is told once; the web app shows it on Today and Recent with Try again and Delete. A retry reads it with
its original time. Kept until read or deleted (the owner's choice); a deleted one is removed for good a day later.
Only the kind of an error is stored, never the words outside this table."""
import json
from datetime import datetime, timezone
from typing import Optional

from .db import connect

AUTO_HOURS = 2  # automatic tries for this long after the message came in
LEASE_MINUTES = 10  # a read that started longer ago than this was dropped (the container stopped): it may be taken again
RETRY_EVERY_MINUTES = 4  # cron runs every 5 minutes; a try a moment ago by the web app is not repeated at once
NEEDS_YOU = "needs you: "  # last_error of a message the logger read but would not save: no more automatic tries


def keep(cur, text: str, channel: str, key: Optional[str], now: datetime) -> int:
    cur.execute("INSERT INTO private.incoming (received_at, channel, content, idempotency_key) VALUES (%s, %s, %s, %s) RETURNING id",
                (now, channel, text, key))
    return cur.fetchone()["id"]


def find(cur, key: Optional[str]) -> Optional[int]:
    """The kept message sent with this idempotency key (the chat or the browser sending the same message again)."""
    if not key:
        return None
    cur.execute("SELECT id FROM private.incoming WHERE idempotency_key = %s AND deleted_at IS NULL ORDER BY id LIMIT 1", (key,))
    r = cur.fetchone()
    return r["id"] if r else None


def waiting_same(cur, text: str, now: datetime) -> Optional[datetime]:
    """When the same words are already waiting to be read (sent in the last 30 minutes): the repeat check asks first."""
    cur.execute("""SELECT received_at FROM private.incoming WHERE deleted_at IS NULL AND lower(content) = lower(%s)
                   AND received_at > %s - interval '30 minutes' ORDER BY received_at DESC LIMIT 1""", (text, now))
    r = cur.fetchone()
    return r["received_at"] if r else None


def claim(hid: int):
    """Take a kept message for reading, in its own short transaction. -> the row; "busy" while another reader has it;
    None when it is gone (saved or deleted meanwhile)."""
    with connect() as c:
        r = c.execute(f"""UPDATE private.incoming SET reading_since = now(), tries = tries + 1, last_try_at = now()
                          WHERE id = %s AND deleted_at IS NULL
                            AND (reading_since IS NULL OR reading_since < now() - interval '{LEASE_MINUTES} minutes')
                          RETURNING *""", (hid,)).fetchone()
        if r:
            return r
        gone = not c.execute("SELECT 1 FROM private.incoming WHERE id = %s AND deleted_at IS NULL", (hid,)).fetchone()
    return None if gone else "busy"


def failed(cur, hid: int, error: str) -> None:
    """The read or the save failed: free the message for the next try and note the kind of error (a class name only)."""
    cur.execute("UPDATE private.incoming SET reading_since = NULL, last_error = %s WHERE id = %s", (error[:80], hid))


def needs_you(cur, hid: int, why: str) -> None:
    """Read by the cron job, but the logger refused it or would not log it: it waits for the owner (no more automatic tries)."""
    cur.execute("UPDATE private.incoming SET reading_since = NULL, last_error = %s WHERE id = %s",
                (f"{NEEDS_YOU}{why}"[:300], hid))


def done(cur, hid: int) -> None:
    """Read and saved: the words now live where the message put them (raw_inbox or the private store). Same transaction as the save."""
    cur.execute("DELETE FROM private.incoming WHERE id = %s", (hid,))


def rows() -> list[dict]:
    """Every message still waiting, oldest first (Today and Recent)."""
    with connect() as c:
        return c.execute("""SELECT id, received_at, channel, content, tries, last_error, reading_since FROM private.incoming
                            WHERE deleted_at IS NULL ORDER BY received_at, id""").fetchall()


def delete(hid: int) -> bool:
    with connect() as c:
        return bool(c.execute("UPDATE private.incoming SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL RETURNING id",
                              (hid,)).fetchone())


def restore(hid: int) -> bool:
    """Undo of a delete."""
    with connect() as c:
        return bool(c.execute("UPDATE private.incoming SET deleted_at = NULL WHERE id = %s AND deleted_at IS NOT NULL RETURNING id",
                              (hid,)).fetchone())


def purge(cur) -> int:
    """The daily clean-up: deleted messages go for good after a day (until then Undo can bring one back)."""
    cur.execute("DELETE FROM private.incoming WHERE deleted_at < now() - interval '1 day'")
    return cur.rowcount


def run(now=None) -> dict:
    """The cron job: read again what is waiting (for AUTO_HOURS after it came in), then tell the phone once about what
    is still not read after that."""
    from . import logbook, nudges  # here: both import this module's neighbours, and cron needs them only now
    now = now or datetime.now(timezone.utc)
    with connect() as c:
        due = [r["id"] for r in c.execute(f"""SELECT id FROM private.incoming WHERE deleted_at IS NULL
                   AND received_at > %s - interval '{AUTO_HOURS} hours' AND coalesce(last_error, '') NOT LIKE %s
                   AND (reading_since IS NULL OR reading_since < %s - interval '{LEASE_MINUTES} minutes')
                   AND (last_try_at IS NULL OR last_try_at < %s - interval '{RETRY_EVERY_MINUTES} minutes')
                   ORDER BY received_at, id""", (now, NEEDS_YOU + "%", now, now)).fetchall()]
    saved = 0
    for hid in due:
        out = logbook.read_held(hid, background=True)
        saved += not out.get("held_id") and out.get("outcome") != "rejected"
    with connect() as c:
        late = c.execute(f"""SELECT count(*) AS n FROM private.incoming WHERE deleted_at IS NULL AND notified_at IS NULL
                             AND received_at <= %s - interval '{AUTO_HOURS} hours'""", (now,)).fetchone()["n"]
        devices = c.execute("SELECT count(*) AS n FROM push_subscriptions").fetchone()["n"]
    told = 0
    if late:
        many = "messages" if late > 1 else "message"
        if devices:  # never the words: only that something waits
            told = nudges.deliver({"title": "Not read yet", "url": "/web/", "tag": "held",
                                   "body": f"{late} {many} could not be read for {AUTO_HOURS} hours. Open Logger to try again."})
        with connect() as c, c.cursor() as cur:  # marked even when no device took it: no repeats every 5 minutes
            cur.execute(f"""UPDATE private.incoming SET notified_at = now() WHERE deleted_at IS NULL AND notified_at IS NULL
                            AND received_at <= %s - interval '{AUTO_HOURS} hours'""", (now,))
            logbook._audit(cur, "held", "notified", {"messages": late, "devices": told}, "held")
    return {"tried": len(due), "saved": saved, "late": late, "told": told}


if __name__ == "__main__":
    print(json.dumps(run()))
