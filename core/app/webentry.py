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


def entry_form(csrf: str) -> str:
    return ('<form class="entry" method="post" action="/web/log">'
            f'{hidden("csrf", csrf)}{hidden("key", key())}'
            f'<textarea id="entry-text" name="text" rows="1" maxlength="{MAX_TEXT}" required aria-label="Log something" '
            'placeholder="Log something: 150 g skyr, weight 82.4, took meds ...  (Enter to send, Shift+Enter for a new line)"></textarea>'
            '<button class="primary" type="submit">Log</button></form>')


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
                  '<a class="ghost dim" href="/web/">No</a></form>')
    warn = " warn" if resp["outcome"] in ("rejected", "needs_confirmation", "needs_choice", "not_logged") else ""
    return f'<section class="reply{warn}" aria-live="polite"><b>{esc(head)}</b>{_body(resp)}{extra}</section>'
