"""What the Settings page's forms do. Each takes (form, key, session) and returns the reply shown under the entry box."""
import re
from typing import Optional

from . import logbook
from .db import connect

SID = re.compile(r"[0-9a-f-]{36}")


def _rejected(reason: str) -> dict:
    return {"outcome": "rejected", "reason": reason}


def device(form, key: Optional[str], session) -> dict:
    """Sign one other device out."""
    sid = str(form.get("sid", ""))
    if not SID.fullmatch(sid):
        return _rejected("Unknown device.")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT token_hash FROM web_sessions WHERE sid = %s", (sid,))
        row = cur.fetchone()
        if not row:
            return {"outcome": "unchanged", "reason": "That device is already signed out."}
        if row["token_hash"] == session["token_hash"]:
            return _rejected("This is the device you are using; use Sign out at the top.")
        cur.execute("DELETE FROM web_sessions WHERE sid = %s", (sid,))
        logbook._audit(cur, "web_change", "changed", {"what": "device signed out"}, logbook.WEB)
    return {"outcome": "changed", "head": "Signed that device out."}


def others(form, key: Optional[str], session) -> dict:
    """Sign every other device out."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM web_sessions WHERE token_hash <> %s", (session["token_hash"],))
        n = cur.rowcount
        logbook._audit(cur, "web_change", "changed", {"what": "other devices signed out", "n": n}, logbook.WEB)
    return {"outcome": "changed", "head": f"Signed out {n} other device{'' if n == 1 else 's'}." if n else "No other device was signed in."}


ACTIONS = {"device": device, "others": others}
