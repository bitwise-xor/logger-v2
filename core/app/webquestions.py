"""Open questions, from any channel, each with the form that fits it: food choices as buttons, an amount field,
a found food with its values, a read-back with Yes / No. Answers go through logbook.answer, like the chat's `answer`."""
from . import rules
from .db import connect
from .logbook import ANSWER_TOOLS
from .webentry import esc, hidden, readable

LIMIT = 50
TODAY_SHOWN = 3  # Today shows the newest few; the Questions page shows all
BACKS = ("/web/", "/web/questions")
# What a yes does, for questions whose stored text is a read-back rather than a question.
_YES_NO = {"log_confirm": "Save this?", "log_repeat": "Log it again?", "add_food_confirm": "Add this food?",
           "lookup_confirm": "Add this food?", "report_confirm": "Save this report?", "command_confirm": "Make this change?"}
_CORRECTABLE = ("log_confirm", "log_repeat")  # the read-back of a typed message: "Correct it" puts it back in the box


def count() -> int:
    with connect() as c:
        return c.execute("SELECT count(*) AS n FROM pending WHERE resolved_at IS NULL AND expires_at > now() AND tool = ANY(%s)",
                         (list(ANSWER_TOOLS),)).fetchone()["n"]


def load(limit: int = LIMIT) -> list[dict]:
    """Open questions, newest first, with the message they came from and the foods they name."""
    with connect() as c:
        # A 'log' question keeps its own question in payload.text; the message is the raw_inbox row it belongs to.
        rows = c.execute("""SELECT p.id, p.tool, p.payload, p.reason, p.created_at, p.expires_at,
                                   CASE WHEN p.tool = 'log' THEN r.content ELSE coalesce(p.payload->>'text', r.content) END AS message
                            FROM pending p LEFT JOIN raw_inbox r ON r.id = (p.payload->>'raw_id')::bigint
                            WHERE p.resolved_at IS NULL AND p.expires_at > now() AND p.tool = ANY(%s)
                            ORDER BY p.created_at DESC LIMIT %s""", (list(ANSWER_TOOLS), limit)).fetchall()
        ids = {i for r in rows for i in (r["payload"].get("candidates") or [])} | \
              {r["payload"]["food_id"] for r in rows if r["payload"].get("food_id")}
        foods = {f["id"]: f for f in c.execute("SELECT id, name, basis FROM foods WHERE id = ANY(%s)", (list(ids),)).fetchall()} if ids else {}
    for r in rows:
        r["foods"] = foods
    return rows


def _form(q: dict, csrf: str, back: str, inner: str) -> str:
    return (f'<form method="post" action="/web/answer">{hidden("csrf", csrf)}{hidden("question_id", q["id"])}'
            f'{hidden("back", back)}{inner}</form>')


def _skip() -> str:
    return '<button class="ghost dim" type="submit" name="quick" value="skip" formnovalidate>Skip</button>'


def _free(label: str = "Answer", placeholder: str = "Your answer") -> str:
    return (f'<div class="row"><input name="reply" autocomplete="off" placeholder="{esc(placeholder)}" aria-label="{esc(placeholder)}">'
            f'<button class="ghost" type="submit">{esc(label)}</button>{_skip()}</div>')


def card(q: dict, csrf: str, back: str = "/web/") -> str:
    p, tool = q["payload"], q["tool"]
    asked = q["created_at"].astimezone(rules.BERLIN).strftime("%H:%M")
    until = q["expires_at"].astimezone(rules.BERLIN).strftime("%d.%m.")
    msg = f' · your message: "{esc(q["message"])}"' if q["message"] else ""
    head = f'<div class="small">{asked}{msg} · open until {until}</div>'
    kind = p.get("kind") if tool == "log" else None
    if tool == "log" and kind == "food":
        cands = "".join(f'<button class="cand" type="submit" name="quick" value="N{i}" formnovalidate><b>N{i}</b>{esc(q["foods"][i]["name"])}</button>'
                        for i in p.get("candidates") or [] if i in q["foods"])
        word = (p.get("item") or {}).get("food", "")
        body = ""
        lk = p.get("lookup")
        if lk:
            v = lk.get("values", {})
            src = "USDA" if lk.get("src") == "usda" else "Open Food Facts"
            body += (f'<div class="ask">Not in your catalogue: "{esc(word)}". Found in {src}:</div>'
                     f'<table class="num"><tr><th>{esc(lk.get("name", ""))}</th><th>per 100 g</th></tr>'
                     + "".join(f'<tr><td>{lbl}</td><td>{esc(v.get(k, "–"))}{u}</td></tr>'
                               for lbl, k, u in (("kcal", "kcal", ""), ("Protein", "protein", " g"), ("Fat", "fat", " g"), ("Carbs", "carbs", " g")))
                     + '</table><div class="row"><button class="primary" type="submit" name="quick" value="yes" formnovalidate>'
                     + ("Use it" if lk.get("existing") else "Add this food") + "</button></div>")
        else:
            body += f'<div class="ask">Which food is "{esc(word)}"?</div>'
        if cands:
            body += f'<div class="row">{cands}</div>'
        body += (f'<div class="row"><input name="reply" autocomplete="off" placeholder="Another food: name or N number" aria-label="Another food">'
                 '<button class="ghost" type="submit">Use</button>'
                 f'<button class="ghost" type="submit" formaction="/web/lookup">Look it up</button>{_skip()}</div>')
        return f'<div class="q">{head}{_form(q, csrf, back, body)}</div>'
    if tool == "log" and kind == "amount":
        f = q["foods"].get(p.get("food_id")) or {}
        unit = f.get("basis", "g")
        body = (f'<div class="ask">{esc(q["reason"])}</div><div class="row">'
                f'<input name="reply" type="number" min="0" step="any" inputmode="decimal" aria-label="Amount in {unit}" class="num">'
                f' {unit} <button class="primary" type="submit">Save</button>{_skip()}</div>')
        return f'<div class="q">{head}{_form(q, csrf, back, body)}</div>'
    if tool == "log" and kind in ("time", "type"):
        body = (f'<div class="ask">{esc(q["reason"])}</div><div class="row">'
                + (f'<button class="ghost" type="button" data-fill="{esc(q["message"])}">Log again</button>' if q["message"] else "")
                + f'{_skip()}</div>')
        return f'<div class="q">{head}{_form(q, csrf, back, body)}</div>'
    if tool in _YES_NO and not (tool == "command_confirm" and p.get("ask_past")):
        fix = (f'<button class="ghost dim" type="button" data-fill="{esc(q["message"])}">Correct it</button>'
               if tool in _CORRECTABLE and q["message"] else "")
        body = (f'<pre>{esc(readable(q["reason"]))}</pre><div class="ask">{_YES_NO[tool]}</div><div class="row">'
                '<button class="primary" type="submit" name="quick" value="yes">Yes</button>'
                f'<button class="ghost" type="submit" name="quick" value="no">No</button>{fix}</div>')
        return f'<div class="q">{head}{_form(q, csrf, back, body)}</div>'
    if tool == "command_confirm":  # a food correction that past entries use
        body = (f'<pre>{esc(readable(q["reason"]))}</pre><div class="ask">Change the past entries too?</div><div class="row">'
                '<button class="primary" type="submit" name="quick" value="yes">Yes, past ones too</button>'
                '<button class="ghost" type="submit" name="quick" value="no">Only new ones</button>'
                '<button class="ghost dim" type="submit" name="quick" value="cancel">Cancel</button></div>')
        return f'<div class="q">{head}{_form(q, csrf, back, body)}</div>'
    body = f'<div class="ask">{esc(q["reason"])}</div>{_free()}'  # which entry was meant, which shopping item, ...
    return f'<div class="q">{head}{_form(q, csrf, back, body)}</div>'


def today_section(csrf: str) -> str:
    rows = load(TODAY_SHOWN + 1)
    if not rows:
        return ""
    n = count()
    more = f'<a href="/web/questions">see all {n} on Questions</a>' if n > TODAY_SHOWN else ""
    return (f'<section class="qwrap"><h2><span>Waiting for your answer · {n}</span>{more}</h2>'
            + "".join(card(q, csrf, "/web/") for q in rows[:TODAY_SHOWN]) + "</section>")


def render(csrf: str) -> str:
    rows = load()
    if not rows:
        return '<h1>Questions</h1><p class="empty">Nothing is waiting for an answer.</p>'
    return "<h1>Questions</h1>" + "".join(card(q, csrf, "/web/questions") for q in rows)
