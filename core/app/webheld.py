"""Messages not read yet (01.11.2026, held.py) on the web app: a section on Today and on Recent with the words (under
Show), why they wait, Try again and Delete (with Undo). Built from the page's existing classes, so no new styles."""
from datetime import datetime, timedelta, timezone

from . import held, logbook, rules
from .webentry import esc, here, hidden

CHANNELS = {"web": "Web app", "mcp": "Claude", "claude-chat": "Claude"}


def _why(r: dict) -> str:
    err = r["last_error"] or ""
    if err.startswith(held.NEEDS_YOU):
        return f"Read, but nothing was saved: {err[len(held.NEEDS_YOU):] or 'see the reply after Try again'}."
    if r["reading_since"] and r["reading_since"] > datetime.now(timezone.utc) - timedelta(minutes=held.LEASE_MINUTES):
        return "Being read right now."
    until = r["received_at"] + timedelta(hours=held.AUTO_HOURS)
    tried = f"Tried {r['tries']} time{'' if r['tries'] == 1 else 's'}"
    if until > datetime.now(timezone.utc):
        return f"{tried}; the logger tries again by itself until {until.astimezone(rules.BERLIN):%H:%M}."
    return f"{tried}; the automatic tries are over. Try again, or delete it."


def _rows(csrf: str, back: str, rows: list) -> str:
    out = []
    for r in rows:
        at = r["received_at"].astimezone(rules.BERLIN)
        chan = CHANNELS.get(r["channel"], r["channel"])
        retry = (f'<form method="post" action="/web/held/retry">{hidden("csrf", csrf)}{hidden("id", str(r["id"]))}{here(back)}'
                 '<button class="primary" type="submit">Try again</button></form>')
        delete = (f'<form method="post" action="/web/held/delete">{hidden("csrf", csrf)}{hidden("id", str(r["id"]))}{here(back)}'
                  '<button class="ghost sm" type="submit">Delete</button></form>')
        out.append(f'<article class="rc"><div class="rc-top"><span class="num">{at:%d.%m. %H:%M}</span><span class="pill">{chan}</span></div>'
                   f'<p class="small">{esc(_why(r))}</p>'
                   f'<details><summary>Show the words</summary><p class="rc-text">{esc(r["content"])}</p>{delete}</details>'
                   f'<div class="row">{retry}</div></article>')
    return "".join(out)


def section(csrf: str, back: str = "/web/") -> str:
    """Today and Recent: every message still waiting, or nothing at all."""
    rows = held.rows()
    if not rows:
        return ""
    return (f'<section class="qwrap"><h2><span>Not read yet · {len(rows)}</span></h2>'
            f'<div class="card recent">{_rows(csrf, back, rows)}</div></section>')


def _id(form) -> int:
    v = str(form.get("id", ""))[:12]
    return int(v) if v.isdigit() else 0


def retry(form, key=None) -> dict:
    """Try again: read it now (the reply shows what was saved). Refused or not loggable: the words come back in the box."""
    hid = _id(form)
    words = next((r["content"] for r in held.rows() if r["id"] == hid), "")
    out = logbook.read_held(hid)
    if out.get("outcome") in ("rejected", "not_logged") and words and not out.get("held_id"):
        out = {**out, "retry_text": words}
    return out


def delete(form, key=None) -> dict:
    hid = _id(form)
    if not held.delete(hid):
        return {"outcome": "rejected", "head": "Nothing deleted.", "reason": "That message is no longer waiting."}
    return {"outcome": "removed", "head": "Deleted the unread message.", "held_undo": hid}


ACTIONS = {"retry": retry, "delete": delete}
