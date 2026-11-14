"""The gratitude list (step B, 14.11.2026): the page /web/gratitude (the list alone and large) and the compact card on Today.
One component for both, made to work as plain forms; app.js (data-grat) saves in place. The HTML never holds the words of
a saved line: earlier lines show "Saved at HH:MM" until Show asks for them (/web/gratitude/lines.json)."""
from . import gratitude
from .webentry import esc, hidden, key

STARTERS = (("A person", "A person I am glad about …"), ("Something small", "A small thing that went well …"),
            ("My body", "Something my body did for me …"), ("A place", "A place I like being in …"),
            ("Something hard", "Something hard that taught me something …"), ("Easy to overlook", "Something I usually overlook …"))
PLACEHOLDER = "Grateful for …"
FOOT = "Each line is saved when you press Enter, straight to your private store and not read by the AI."
_WORDS = ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
          "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen", "Twenty")


def done_text(goal: int) -> str:
    return f"{_WORDS[goal] if goal < len(_WORDS) else goal} for today."


def private_link(day=None) -> str:
    return "/web/private?view=grat" + (f"&day={day.isoformat()}" if day else "")


def _dots(n: int, g: int) -> str:
    return "".join(f'<span class="gdot{" on" if i < n else ""}"></span>' for i in range(g))


def _form(csrf: str, back: str, autofocus: bool) -> str:
    return (f'<form class="gform" method="post" action="/web/gratitude/line" data-grat-form>{hidden("csrf", csrf)}{hidden("key", key())}'
            f'{hidden("back", back)}<textarea name="text" rows="1" maxlength="4000" required data-grat-input enterkeyhint="send" '
            f'placeholder="{PLACEHOLDER}" aria-label="Grateful for"{" autofocus" if autofocus else ""}></textarea>'
            '<button class="primary" type="submit">Save</button></form>')


def component(csrf: str, page: bool) -> str:
    st = gratitude.state()
    n, g, day = st["count"], st["goal"], st["day"]
    form = _form(csrf, "/web/gratitude" if page else "/web/", page)
    title = ('<h1 class="gt">Gratitude</h1>' if page else '<h2 class="gt"><a href="/web/gratitude">Gratitude</a></h2>')
    head = (f'<div class="ghead">{title}<span class="gcount"><b data-grat-count>{n}</b> of <span data-grat-goal>{g}</span></span></div>'
            f'<div class="gdots" data-grat-dots role="img" aria-label="{n} of {g}">{_dots(n, g)}</div>')
    stuck = ('<div class="gstuck"><button class="ghost sm" type="button" data-grat-stuck hidden aria-expanded="false">Stuck?</button>'
             '<div class="chips" data-grat-starters hidden>'
             + "".join(f'<button class="chip" type="button" data-grat-start="{esc(p)}">{esc(lbl)}</button>' for lbl, p in STARTERS)
             + '</div></div>')
    finished = (f'<p class="gdone" data-grat-done{"" if n >= g else " hidden"}><b>{done_text(g)}</b>'
                '<button class="ghost sm" type="button" data-grat-more hidden>One more</button></p>')
    msg = '<p class="gmsg" data-grat-msg role="status" aria-live="polite"></p>'
    if page:
        early = "".join(f'<li class="gline early" data-id="{pid}"><span class="gn">{i}</span><span class="gbody">'
                        f'<span class="gtext" data-at="{at}">Saved at {at}</span></span></li>' for i, (pid, at) in enumerate(st["times"], 1))
        empty = "".join(f'<li class="gempty" aria-hidden="true"><span class="gn">{i}</span></li>' for i in range(n + 2, g + 1))
        show = (f'<p class="row gshow"><a class="ghost sm" href="{esc(private_link(day))}" data-grat-show>Show</a>'
                f'<span class="small">Earlier lines of today are hidden.</span></p>' if n else "")
        body = (f'{show}<ol class="glist" data-grat-list>{early}<li class="gslot" data-grat-slot><span class="gn" data-grat-next>{n + 1}</span>'
                f'<div class="gbody">{form}{msg}</div></li>{empty}</ol>{finished}{stuck}'
                f'<p class="small gfoot">{FOOT}</p>'
                f'<p class="row gend"><a class="primary" href="/web/">Done</a>'
                f'<a class="small" href="{esc(private_link())}">Older days on the Private page</a></p>')
    else:
        body = (f'<div data-grat-slot>{form}{msg}</div>{finished}{stuck}<ol class="glist" data-grat-list></ol>'
                f'<p class="small gfoot">{FOOT}</p>')
    return (f'<section class="card grat{" big" if page else ""}" id="gratitude" data-grat data-csrf="{esc(csrf)}" data-goal="{g}" '
            f'data-count="{n}" data-done="{esc(done_text(g))}">{head}{body}</section>')


def card(csrf: str) -> str:
    return component(csrf, page=False)


def render(csrf: str) -> str:
    return component(csrf, page=True)
