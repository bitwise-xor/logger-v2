"""The web app's entry box and the reply box. The reply is built from the same result the chat gets (logbook.log /
logbook.answer) or from a web action; open questions are not repeated here, they have their own section."""
import html
import secrets

MAX_TEXT = 4000
MAX_REPLY = 1000

_HEAD = {"saved": "Saved.", "partly_saved": "Saved what was clear. A question is still open.",
         "needs_choice": "Nothing saved yet: a question is open.", "kept": "Kept as text.",
         "not_logged": "Not logged: this reads as a question for the assistant, not an entry.",
         "needs_confirmation": "Nothing saved yet: please confirm.", "rejected": "Nothing saved.", "cancelled": "Skipped.",
         "replayed": "Already saved earlier (same message).", "answered": "Found.", "changed": "Changed.",
         "removed": "Removed.", "unchanged": "Nothing changed."}


def esc(x) -> str:
    return html.escape(str(x))


def hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{esc(value)}">'


def key() -> str:
    """A new idempotency key for one form: sending the same form twice saves once."""
    return secrets.token_urlsafe(16)


def here(back: str = "/web/") -> str:
    """A hidden "back" field; app.js fills in the page the form is on, so the browser returns there after the action."""
    return f'<input type="hidden" name="back" value="{esc(back)}" data-here>'


def entry_form(csrf: str, text: str = "") -> str:
    """text: a message that was not saved, put back in the box (and the box focused) so it need not be typed again."""
    return ('<form class="entry" method="post" action="/web/log">'
            f'{hidden("csrf", csrf)}{hidden("key", key())}{here()}'
            f'<textarea id="entry-text" name="text" rows="1" maxlength="{MAX_TEXT}" required aria-label="Log something"{" autofocus" if text else ""} '
            'placeholder="Log something: 150 g skyr, weight 82.4, took meds ...  (Enter to send, Shift+Enter for a new line)">'
            f'{esc(text)}</textarea>'
            # the microphone shows only where the browser can turn speech into text (app.js); the words land in the box to check first
            f'<button class="iconbtn mic" type="button" data-mic hidden aria-pressed="false" aria-label="Speak instead of typing">{icon("mic")}</button>'
            '<button class="primary" type="submit">Log</button></form>')


def undo_form(resp: dict, csrf: str) -> str:
    """"Undo" under a reply that saved something: it removes that message's entries, if it is still the newest one."""
    if not (resp.get("raw_id") and resp.get("outcome") in ("saved", "partly_saved") and not resp.get("replayed")):
        return ""
    return (f'<form method="post" action="/web/undo">{hidden("csrf", csrf)}{hidden("raw_id", str(resp["raw_id"]))}{here()}'
            '<button class="ghost sm" type="submit">Undo</button></form>')


def undo_bar(rec, csrf: str) -> str:
    """The Undo bar (31.10.2026): the device's last action, with Undo, until its next action (webundo.record)."""
    if not rec:
        return ""
    if rec.get("raw_id"):
        button = undo_form({"raw_id": rec["raw_id"], "outcome": "saved"}, csrf)
    else:
        button = (f'<form method="post" action="/web/undo/last">{hidden("csrf", csrf)}{hidden("token", rec.get("token", ""))}{here()}'
                  '<button class="ghost sm" type="submit">Undo</button></form>')
    return f'<div class="undobar" role="status">{icon("check")}<span>{esc(rec.get("head") or "Saved.")}</span>{button}</div>'


def readable(text: str) -> str:
    """A read-back as a person reads it: without the lines that tell the chat app what to do next."""
    keep = [ln for ln in str(text).splitlines()
            if not ln.lstrip().startswith(("Ask the user", "Then resend")) and "resend the whole call" not in ln]
    out = "\n".join(keep).strip()
    return out.replace("READ BACK, nothing saved (nothing else in this message was saved either):", "Nothing saved yet:")


def _body(resp: dict) -> str:
    parts = []
    if resp.get("receipt"):
        parts.append("<ul>" + "".join(f"<li>{esc(r)}</li>" for r in resp["receipt"]) + "</ul>")
    for note in resp.get("notes", []):
        parts.append(f'<p class="small">{esc(note)}</p>')
    if resp.get("result"):
        parts.append(f"<pre>{esc(resp['result'])}</pre>")
    if resp.get("command"):
        sub = resp["command"]
        parts.append(f"<b>{esc(_HEAD.get(sub['outcome'], sub['outcome']))}</b>" + _body(sub))
    if resp.get("read_back") and not resp.get("questions"):  # with a question, its card shows the read-back
        parts.append(f"<pre>{esc(readable(resp['read_back']))}</pre>")
    if resp.get("reason"):
        parts.append(f"<p>{esc(resp['reason'])}</p>")
    return "".join(parts)


def reply_html(resp: dict, csrf: str) -> str:
    """The box under the entry field after an action. resp["confirm"] = a form to send again with the user's yes
    (a Health correction the logger read back)."""
    head = resp.get("head") or _HEAD.get(resp["outcome"], resp["outcome"])
    if resp["outcome"] == "rejected" and resp.get("steps"):  # a change that matched nothing
        head = "Nothing changed."
    if resp.get("replayed"):
        head = _HEAD["replayed"]
    extra = ""
    if resp.get("questions"):
        extra += '<p class="small">The question is in "Waiting for your answer" below.</p>'
    c = resp.get("confirm")
    if c:
        fields = "".join(hidden(k, v) for k, v in c["fields"].items())
        extra += (f'<form method="post" action="{esc(c["action"])}">{hidden("csrf", csrf)}{fields}'
                  f'<button class="primary" type="submit">{esc(c["label"])}</button>'
                  f'<a class="ghost dim" href="{esc(c.get("no", "/web/"))}">{esc(c.get("no_label", "No"))}</a></form>')
    if resp.get("retry_text"):
        extra += '<p class="small">Your message is back in the box above.</p>'
    warn = " warn" if resp["outcome"] in ("rejected", "needs_confirmation", "needs_choice", "not_logged") else ""
    return f'<section class="reply{warn}" aria-live="polite"><b>{esc(head)}</b>{_body(resp)}{extra}</section>'


# ---- icons: line drawings on a 24 px grid, sized and coloured by app.css (svg.ic), hidden from screen readers -------------

ICONS = {
    "logo": '<rect x="3" y="3" width="18" height="18" rx="6"/><path d="m8 12.5 2.6 2.6L16 9.5"/>',
    "today": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "food": '<path d="M3 12h18a9 9 0 0 1-18 0z"/><path d="M8 3.5v4M12 3v5M16 3.5v4"/>',
    "health": '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8z"/>',
    "habits": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "ideas": '<path d="M9 18h6M10 21.5h4M12 2.5a6.5 6.5 0 0 0-4 11.6V16h8v-1.9a6.5 6.5 0 0 0-4-11.6z"/>',
    "week": '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "insights": '<path d="M4 19.5h16"/><path d="m5 15 4.5-4.5 3.5 3.5L19 7.5"/><path d="M15 7.5h4v4"/>',
    "barcode": '<path d="M4 7V5.5A1.5 1.5 0 0 1 5.5 4H7M17 4h1.5A1.5 1.5 0 0 1 20 5.5V7M20 17v1.5a1.5 1.5 0 0 1-1.5 1.5H17M7 20H5.5A1.5 1.5 0 0 1 4 18.5V17"/><path d="M8 8v8M11 8v8M13.5 8v8M16 8v8"/>',
    "camera": '<path d="M4 8.5A1.5 1.5 0 0 1 5.5 7H8l1.5-2h5L16 7h2.5A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z"/><circle cx="12" cy="13" r="3.5"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>',
    "recent": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.4"/><path d="M3.5 3.8v4.6h4.6M12 7.5V12l3 2"/>',
    "questions": '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.4c-.6.3-1 .8-1 1.5v.4M12 17h.01"/>',
    "private": '<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5v-3a4 4 0 0 1 8 0v3"/>',
    "usage": '<path d="M5 20v-7M11 20V5M17 20v-10M3 20h18"/>',
    "labs": '<path d="M9 3h6M10 3v6l-5.4 9.4A1.7 1.7 0 0 0 6.1 21h11.8a1.7 1.7 0 0 0 1.5-2.6L14 9V3"/><path d="M7.4 15h9.2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "more": '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    "check": '<path d="m6 12.5 4 4 8-9"/>',
    "left": '<path d="m15 6-6 6 6 6"/>',
    "right": '<path d="m9 6 6 6-6 6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
    "sun": '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
}


def icon(name: str, cls: str = "") -> str:
    return f'<svg class="ic{" " + cls if cls else ""}" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'
