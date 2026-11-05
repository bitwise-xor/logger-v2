"""The web app's search (decided 29.10.2026): one box over the foods of the catalogue, what you ate (food names and notes), the
thoughts, gym workouts (exercise and day names) and chores done (chore and zone names, since 05.11.2026) and the lab tests. Private and gratitude entries are never searched here, only on the Private page."""
import re
from urllib.parse import urlencode

from . import gym, home
from .db import connect
from .webentry import esc

LIMIT = 15


def fold(s: str) -> str:
    """ü, ö, ä, ß as ue, oe, ae, ss, like the entry box's suggestions (04.11.2026: "muesli" did not find "Müsli")."""
    return s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def F(col: str) -> str:
    """The same folding in SQL, for a column (a fixed name)."""
    return f"replace(replace(replace(replace(lower({col}), 'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'ß', 'ss')"


def _like(term: str) -> str:
    return "%" + re.sub(r"([%_\\])", r"\\\1", fold(term)) + "%"


def _section(title: str, rows: list[str], more: str = "") -> str:
    body = "".join(rows) or '<p class="empty">Nothing found.</p>'
    return f'<div class="card"><h2>{title} <span class="small">{len(rows)}{"+" if len(rows) == LIMIT else ""}</span></h2>{body}{more}</div>'


def _workouts(cur, like: str) -> list[str]:
    """Workouts whose day of the split or one of whose exercises (name or other words) matches, newest first: the date (to that
    workout on Gym > History), the day, and the matching exercises with what was done (all exercise names when only the day matches)."""
    hit = f"({F('x.name')} LIKE %s OR EXISTS (SELECT 1 FROM unnest(x.words) a WHERE {F('a')} LIKE %s))"
    cur.execute(f"""SELECT w.id, w.day, d.name AS split FROM workouts w LEFT JOIN workout_days d ON d.id = w.split_day_id
                    WHERE {F("coalesce(d.name, '')")} LIKE %s
                       OR EXISTS (SELECT 1 FROM workout_sets s JOIN exercises x ON x.id = s.exercise_id WHERE s.workout_id = w.id AND {hit})
                    ORDER BY w.day DESC, w.id DESC LIMIT %s""", (like, like, like, LIMIT))
    ws = cur.fetchall()
    cur.execute(f"""SELECT s.*, x.name, x.kind, {hit} AS hit FROM workout_sets s JOIN exercises x ON x.id = s.exercise_id
                    WHERE s.workout_id = ANY(%s) ORDER BY s.sort_order, s.id""", (like, like, [w["id"] for w in ws]))
    sets: dict = {}
    for r in cur.fetchall():
        sets.setdefault(r["workout_id"], {}).setdefault(r["exercise_id"], []).append(r)
    out = []
    for w in ws:
        by = list(sets.get(w["id"], {}).values())
        hits = [rs for rs in by if rs[0]["hit"]]
        what = ("; ".join(f'{rs[0]["name"]} {", ".join(gym.row_text(r) for r in rs)}' for rs in hits) if hits
                else ", ".join(rs[0]["name"] for rs in by) or "nothing logged")
        out.append(f'<p><a href="/web/gym/history#w{w["id"]}">{w["day"]:%a %d.%m.%Y}</a> · {esc(w["split"] or "Free workout")} '
                   f'<span class="small">{esc(what)}</span></p>')
    return out


def render(csrf: str, q) -> str:
    term = " ".join(str(q.get("q", "")).split())[:80]
    box = ('<form class="row" method="get" action="/web/search"><input type="search" name="q" class="grow" autocomplete="off" '
           f'value="{esc(term)}" placeholder="Search foods, what you ate, thoughts, gym, chores, lab tests" aria-label="Search"{"" if term else " autofocus"}>'
           '<button class="primary" type="submit">Search</button></form>')
    head = f'<div class="head"><h1>Search</h1></div><div class="card">{box}'
    if len(term) < 2:
        return head + '<p class="small hint">Type at least two letters. Private and gratitude entries are searched on the Private page only.</p></div>'
    like = _like(term)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT id, name FROM foods WHERE active AND ({F("name")} LIKE %s OR EXISTS (SELECT 1 FROM unnest(aliases) a WHERE {F("a")} LIKE %s))
                        ORDER BY lower(name) LIMIT %s""", (like, like, LIMIT))
        foods = [f'<p><a href="/web/food/catalogue?{esc(urlencode({"q": term, "food": f"N{r["id"]}"}))}#N{r["id"]}"><span class="small">N{r["id"]}</span> {esc(r["name"])}</a></p>'
                 for r in cur.fetchall()]
        cur.execute(f"""SELECT e.day, f.name, e.amount, f.basis, e.note FROM food_entries e JOIN foods f ON f.id = e.food_id
                        WHERE {F("f.name")} LIKE %s OR {F("coalesce(e.note, '')")} LIKE %s
                              OR EXISTS (SELECT 1 FROM unnest(f.aliases) a WHERE {F("a")} LIKE %s)
                        ORDER BY e.day DESC, e.id DESC LIMIT %s""", (like, like, like, LIMIT))
        ate = [f'<p><a href="/web/food?day={r["day"].isoformat()}">{r["day"]:%a %d.%m.%Y}</a> · {esc(r["name"])} <span class="small">{r["amount"]:g} {r["basis"]}</span>'
               + (f'<span class="notel">{esc(r["note"])}</span>' if r["note"] else "") + "</p>" for r in cur.fetchall()]
        cur.execute(f"""SELECT id, summary, stage, thought_type, logged_at FROM thoughts WHERE {F("summary")} LIKE %s OR {F("original_text")} LIKE %s
                        ORDER BY logged_at DESC LIMIT %s""", (like, like, LIMIT))
        thoughts = [f'<p><a href="/web/ideas?{esc(urlencode({"q": term, "stage": "all", "open": r["id"]}))}">{esc(r["summary"])}</a> '
                    f'<span class="small">{r["logged_at"]:%d.%m.%Y} · {esc(r["thought_type"] or "note")} · {esc(r["stage"])}</span></p>' for r in cur.fetchall()]
        workouts = _workouts(cur, like)
        chores = [f'<p>{e["day"]:%a %d.%m.%Y} · <a href="/web/home/chore?id={e["chore_id"]}">{esc(e["label"])}</a>'
                  + (' <span class="small">starting point</span>' if e["source"] == "start" else "") + "</p>"
                  for e in home.recent(cur, LIMIT, starts=True, match=(
                      f"{F('c.name')} LIKE %s OR {F('z.name')} LIKE %s OR {F('z.short')} LIKE %s OR EXISTS (SELECT 1 FROM unnest(c.words || z.words) a WHERE {F('a')} LIKE %s)",
                      (like,) * 4))]
        cur.execute(f"SELECT name FROM lab_tests WHERE {F('name')} LIKE %s ORDER BY lower(name) LIMIT %s", (like, LIMIT))
        labs = [f'<p>{esc(r["name"])}</p>' for r in cur.fetchall()]
    return (head + '<p class="small hint">Private and gratitude entries are searched on the Private page only.</p></div>'
            + _section("Foods in the catalogue", foods) + _section("What you ate", ate) + _section("Thoughts", thoughts)
            + _section("Gym workouts", workouts) + _section("Chores done", chores)
            + _section("Lab tests", labs, '<p class="small"><a href="/web/labs/tests">Open Tests over time</a></p>'))
