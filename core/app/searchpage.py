"""The web app's search (decided 29.10.2026): one box over the foods of the catalogue, what you ate (food names and notes), the
thoughts and the lab tests. Private and gratitude entries are never searched here, only on the Private page."""
import re
from urllib.parse import urlencode

from .db import connect
from .webentry import esc

LIMIT = 15


def _like(term: str) -> str:
    return "%" + re.sub(r"([%_\\])", r"\\\1", term) + "%"


def _section(title: str, rows: list[str], more: str = "") -> str:
    body = "".join(rows) or '<p class="empty">Nothing found.</p>'
    return f'<div class="card"><h2>{title} <span class="small">· {len(rows)}{"+" if len(rows) == LIMIT else ""}</span></h2>{body}{more}</div>'


def render(csrf: str, q) -> str:
    term = " ".join(str(q.get("q", "")).split())[:80]
    box = ('<form class="row" method="get" action="/web/search"><input type="search" name="q" class="grow" autocomplete="off" '
           f'value="{esc(term)}" placeholder="Search foods, what you ate, thoughts, lab tests" aria-label="Search"{"" if term else " autofocus"}>'
           '<button class="primary" type="submit">Search</button></form>')
    head = f'<div class="head"><h1>Search</h1></div><div class="card">{box}'
    if len(term) < 2:
        return head + '<p class="small hint">Type at least two letters. Private and gratitude entries are searched on the Private page only.</p></div>'
    like = _like(term)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT id, name FROM foods WHERE active AND (name ILIKE %s OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE a ILIKE %s))
                       ORDER BY lower(name) LIMIT %s""", (like, like, LIMIT))
        foods = [f'<p><a href="/web/food/catalogue?{esc(urlencode({"q": term, "food": f"N{r["id"]}"}))}#N{r["id"]}"><span class="small">N{r["id"]}</span> {esc(r["name"])}</a></p>'
                 for r in cur.fetchall()]
        cur.execute("""SELECT e.day, f.name, e.amount, f.basis, e.note FROM food_entries e JOIN foods f ON f.id = e.food_id
                       WHERE f.name ILIKE %s OR e.note ILIKE %s ORDER BY e.day DESC, e.id DESC LIMIT %s""", (like, like, LIMIT))
        ate = [f'<p><a href="/web/food?day={r["day"].isoformat()}">{r["day"]:%a %d.%m.%Y}</a> · {esc(r["name"])} <span class="small">{r["amount"]:g} {r["basis"]}</span>'
               + (f'<span class="notel">{esc(r["note"])}</span>' if r["note"] else "") + "</p>" for r in cur.fetchall()]
        cur.execute("""SELECT id, summary, stage, thought_type, logged_at FROM thoughts WHERE summary ILIKE %s OR original_text ILIKE %s
                       ORDER BY logged_at DESC LIMIT %s""", (like, like, LIMIT))
        thoughts = [f'<p><a href="/web/ideas?{esc(urlencode({"q": term, "stage": "all", "open": r["id"]}))}">{esc(r["summary"])}</a> '
                    f'<span class="small">{r["logged_at"]:%d.%m.%Y} · {esc(r["thought_type"] or "note")} · {esc(r["stage"])}</span></p>' for r in cur.fetchall()]
        cur.execute("SELECT name FROM lab_tests WHERE name ILIKE %s ORDER BY lower(name) LIMIT %s", (like, LIMIT))
        labs = [f'<p>{esc(r["name"])}</p>' for r in cur.fetchall()]
    return (head + '<p class="small hint">Private and gratitude entries are searched on the Private page only.</p></div>'
            + _section("Foods in the catalogue", foods) + _section("What you ate", ate) + _section("Thoughts", thoughts)
            + _section("Lab tests", labs, '<p class="small"><a href="/web/labs/tests">Open Tests over time</a></p>'))
