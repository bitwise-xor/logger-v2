"""What the Private page's forms do. New entries are saved straight into the private store through log with their label
(privateline.py turns them into ready parser answers), so the words never go to the AI (decided 26.10.2026); a gratitude
text gives one entry per line.
Changes and deletions go through the logger's change function (P… ids). Replies never repeat the private words."""
import re
from datetime import datetime
from typing import Optional

from . import logbook, privateline, privatepage
from .db import connect
from .todaypage import AREAS, THOUGHT_TYPES
from .webactions import TIME, _change, _rejected
from .webfood import _again
from .when import BERLIN


def new(form, key: Optional[str]) -> dict:
    kind = form.get("kind")
    if kind not in privatepage.KINDS:
        return _rejected("Pick Gratitude or Private.")
    text = str(form.get("text", "")).replace("\r\n", "\n").strip()[:4000]
    try:  # the same rules as a labelled message typed anywhere (privateline.py): one entry per gratitude line, at most 20
        privateline.entries(kind, text)
    except privateline.Refused as e:
        return _rejected("Type something first." if e.empty else str(e))
    out = logbook.log(f"{kind}: {text}", key, channel=logbook.WEB)  # the label routes it past the AI, all lines in one go
    n = len(out.get("private_ids") or [])
    if out["outcome"] != "saved" or not n:
        return _rejected("Nothing was saved; nothing went anywhere else. Try it again.")
    what = (f"{n} gratitude entries" if n > 1 else "1 gratitude entry") if kind == "gratitude" else "1 private entry"
    return {"outcome": "saved", "head": f"Saved {what} privately, without the AI."}


def entry(form, key: Optional[str]) -> dict:
    ref, act = str(form.get("id", "")), form.get("act")
    back = privatepage.safe_back(form.get("back"))
    if not re.fullmatch(r"P\d{1,9}", ref):
        return _rejected("Unknown entry.")
    with connect() as c:
        e = c.execute("SELECT id, label, summary, thought_type, area, tags, event_at, received_at FROM private.entries WHERE id = %s",
                      (int(ref[1:]),)).fetchone()
    if not e:
        return _rejected("That entry is not there any more.")
    if act == "delete":
        if form.get("confirmed") != "1":
            return {"outcome": "needs_confirmation", "read_back": f"Delete this {e['label']} entry ({ref})?",
                    "confirm": {"action": "/web/private/entry", "label": "Yes, delete", "no": back, "fields": _again(form) | {"confirmed": "1"}}}
        return _change([{"action": "change", "args": {"id": ref, "delete": True}}], "private delete", f"Deleted the {e['label']} entry.")
    if act != "save":
        return _rejected("Unknown action.")
    summary = " ".join(str(form.get("summary", "")).split())[:300]
    tt, area = form.get("thought_type", ""), form.get("area", "")
    if not summary:
        return _rejected("The summary cannot be empty.")
    if (tt and tt not in THOUGHT_TYPES) or (area and area not in AREAS):
        return _rejected("Pick a type and an area from the lists.")
    tags = []
    for x in str(form.get("tags", "")).split(","):
        x = " ".join(x.split()).lower().lstrip("#")[:40]
        if x and x not in tags:
            tags.append(x)
    new_vals = {"summary": summary, "thought_type": tt or None, "area": area or None, "tags": tags[:12]}
    fields = {k: v for k, v in new_vals.items() if v != (list(e[k] or []) if k == "tags" else e[k])}
    day, t = str(form.get("day", "")), str(form.get("time", "")).strip()
    if day or t:
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            return _rejected("Give the date as a date.")
        if not TIME.match(t):
            return _rejected("Give the time as hh:mm.")
        old = (e["event_at"] or e["received_at"]).astimezone(BERLIN)
        h, m = t.split(":")
        if (d, f"{int(h):02d}:{m}") != (old.date(), old.strftime("%H:%M")):
            fields["when"] = f"{d:%d.%m.%Y} {int(h):02d}:{m}"
    if not fields:
        return {"outcome": "unchanged", "reason": "Nothing changed."}
    return _change([{"action": "change", "args": {"id": ref, "fields": fields}}], "private save",
                   f"Saved: {', '.join('date' if k == 'when' else k.replace('_', ' ') for k in fields)}.")


# Every form of the page: path under /web/private/, the function
ACTIONS = {"new": new, "entry": entry}
