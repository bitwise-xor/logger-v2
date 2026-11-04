"""The coaching on the web app (web item 52, 04.11.2026; mockup https://claude.ai/artifact/4z4HLimQeLi2jo4Z6ZX3ND): the Coach card on
Today (for the day shown), the Week review card on the Week page (for the period shown), the Coach page under More (every coaching,
newest first) and the "My goals" tab in Settings. While the coach reads, the card says so and app.js asks /web/coach/status every few
seconds and shows the result when it is there. The data and the AI call are in coach.py."""
from . import coach, rules
from .db import connect
from .webentry import esc, hidden, icon, key

CAUTION = "Comments only on what you logged; not medical advice."
WAIT = {"day": "Coaching… about 30 seconds", "week": "Reviewing… about 45 seconds"}


def _items(xs) -> str:
    return "".join(f"<li>{esc(x)}</li>" for x in xs or [] if str(x).strip())


def _section(title: str, xs, cls: str = "") -> str:
    li = _items(xs)
    attr = f' class="{cls}"' if cls else ""
    return f'<div class="cosec"><h3{attr}>{esc(title)}</h3><ul>{li}</ul></div>' if li else ""


def body_html(row: dict) -> str:
    """A finished coaching's sections."""
    b = row["body"] or {}
    if row["kind"] == "day":
        meal = b.get("next_meal")
        meal_html = ""
        if meal:
            macros = " · ".join(f"{v} {u}" for v, u in ((round(meal.get("kcal") or 0), "kcal"), (round(meal.get("protein") or 0), "g protein"),
                                                         (round(meal.get("carbs") or 0), "g carbs"), (round(meal.get("fat") or 0), "g fat")))
            meal_html = (f'<div class="comeal"><div class="small">Next meal</div><b>{esc(meal.get("title", ""))}</b>'
                         f'<div>{esc(macros)}</div><div class="small">{esc(meal.get("why", ""))}</div></div>')
        out = (_section("What went well", b.get("went_well"), "ok") + _section("To improve", b.get("improve"), "warn") + meal_html
               + _section("Missing in the log", b.get("gaps")))
    else:
        focus = _items(b.get("focus"))
        days = "".join(f"<li>{esc(x)}</li>" for x in (b.get("best_day"), b.get("hardest_day")) if x)
        out = (_section("Against your targets", b.get("targets")) + _section("Trends", b.get("trends")) + _section("Habits, gym and home", b.get("habits"))
               + (f'<div class="cosec"><h3>Best and hardest day</h3><ul>{days}</ul></div>' if days else "")
               + (f'<div class="comeal"><div class="small">Focus for next week</div><ol>{focus}</ol></div>' if focus else ""))
    doctor = f'<p class="small">{esc(b["doctor"])}</p>' if b.get("doctor") else ""
    return out + doctor + f'<p class="small">{CAUTION}</p>'


def _meta(row: dict) -> str:
    at = (row["finished_at"] or row["created_at"]).astimezone(rules.BERLIN)
    usd = f' · ${float(row["usd"]):.2f}' if row["usd"] is not None else ""
    return f'Claude Opus{usd} · {at:%d.%m. %H:%M}'


def _button(kind: str, a, b, csrf: str, back: str, label: str, primary: bool) -> str:
    return (f'<form method="post" action="/web/coach/start">{hidden("csrf", csrf)}{hidden("key", key())}{hidden("kind", kind)}'
            f'{hidden("start", a.isoformat())}{hidden("end", b.isoformat())}{hidden("back", back)}'
            f'<button class="{"primary" if primary else "ghost"}" type="submit">{label}</button></form>')


def card(kind: str, a, b, csrf: str, back: str) -> str:
    """The Coach card (day) or the Week review card (week) for one period."""
    with connect() as conn, conn.cursor() as cur:
        row = coach.latest(cur, kind, a, b)
    title = "Coach" if kind == "day" else "Week review"
    again = "Coach me again" if kind == "day" else "Review again"
    first = "Coach me" if kind == "day" else "Review this week"
    links = '<p class="row small"><a href="/web/coach">All coachings ›</a><a href="/web/settings?tab=coach">Your goals ›</a></p>'
    if row and row["status"] == "working" and not row["stale"]:
        inner = (f'<div class="cowait" data-coach-wait="{row["id"]}" role="status"><b>{WAIT[kind]}</b>'
                 '<span class="small">You can leave this page: the coaching is kept and shows here when it is ready.</span></div>')
    elif row and row["status"] == "done":
        inner = (body_html(row) + f'<div class="cofoot"><span class="small">{esc(_meta(row))}</span>'
                 f'{_button(kind, a, b, csrf, back, again, False)}</div>')
    else:
        failed = (f'<p class="notice">That did not work ({esc("cut off" if row["stale"] else row["error"] or "an error")}); nothing was charged '
                  'if the AI did not answer. Try again.</p>' if row else "")
        intro = ("A short look at this day: what went well, what to improve, your next meal and what is missing in the log."
                 if kind == "day" else "The period against your targets and goals: trends, habits, gym and home, best and hardest day, "
                 "and one or two things to focus on next.")
        cost = "about 30 seconds · about 6 cents" if kind == "day" else "about 45 seconds · about 20 cents"
        inner = f'{failed}<p>{intro}</p>{_button(kind, a, b, csrf, back, first, True)}<p class="small coc">{cost}</p>'
    return (f'<div class="card coachcard"><h2><span>{icon("coach")}{title}</span>'
            f'<span class="small">{"this day" if kind == "day" else "this period"} · on demand</span></h2>{inner}{links}</div>')


def today_card(day, csrf: str) -> str:
    return card("day", day, day, csrf, f"/web/?day={day.isoformat()}")


def week_card(a, b, p: str, csrf: str) -> str:
    return card("week", a, b, csrf, f"/web/week?p={p}")


FILTERS = (("all", "All"), ("days", "Days"), ("weeks", "Weeks"))


def _first(row: dict) -> str:
    b = row["body"] or {}
    xs = (b.get("improve") or b.get("went_well") or []) if row["kind"] == "day" else (b.get("focus") or b.get("trends") or [])
    return xs[0] if xs else ""


def render(csrf: str, q=None) -> str:
    """The Coach page: every finished coaching, newest first, each opening in place."""
    f = (q or {}).get("f") if (q or {}).get("f") in dict(FILTERS) else "all"
    kinds = {"all": ["day", "week"], "days": ["day"], "weeks": ["week"]}[f]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM coachings WHERE status = 'done' AND kind = ANY(%s) ORDER BY created_at DESC LIMIT 100""", (kinds,))
        rows = cur.fetchall()
        cur.execute("""SELECT count(*) AS n, coalesce(sum(usd), 0) AS usd FROM coachings
                       WHERE status = 'done' AND date_trunc('month', created_at AT TIME ZONE 'Europe/Berlin') = date_trunc('month', now() AT TIME ZONE 'Europe/Berlin')""")
        month = cur.fetchone()
    chips = "".join(f'<a href="/web/coach?f={k}"' + (' aria-current="page"' if k == f else "") + f">{lbl}</a>" for k, lbl in FILTERS)
    items = []
    for r in rows:
        when = f'{r["start_day"]:%a %d.%m.}' if r["kind"] == "day" else f'Week {r["start_day"]:%d.%m.} to {r["end_day"]:%d.%m.}'
        items.append(f'<details class="coitem"><summary><span class="row"><b>{esc(when)}</b><span class="pill ok">{r["kind"]}</span></span>'
                     f'<span>{esc(_first(r))}</span><span class="small">{esc(_meta(r))}</span></summary>{body_html(r)}</details>')
    body = "".join(items) or '<p class="empty">No coachings yet. Press "Coach me" on Today or "Review this week" on the Week page.</p>'
    return (f'<div class="head"><h1>Coach</h1><nav class="seg" aria-label="Coachings">{chips}</nav></div>'
            f'<div class="card">{body}</div>'
            f'<p class="small">Every coaching is kept; "Coach me again" adds a new one. This month: {month["n"]} coaching'
            f'{"" if month["n"] == 1 else "s"} · ${float(month["usd"]):.2f} (also on the Usage page).</p>')


def settings_tab(session, csrf: str) -> str:
    """Settings > Coach: the owner's goals, read by the coach every time."""
    with connect() as conn, conn.cursor() as cur:
        text = coach.goals(cur)
    return (f'<div class="card"><h2>My goals</h2><p class="small">The coach reads this every time. Empty: it coaches against your food targets only.</p>'
            f'<form method="post" action="/web/settings/coach_goals">{hidden("csrf", csrf)}{hidden("back", "/web/settings?tab=coach")}'
            f'<label for="coach-goals">Your goals, in your own words</label>'
            f'<textarea id="coach-goals" class="wide" name="goals" rows="6" maxlength="{coach.GOALS_MAX}" '
            f'placeholder="For example: lose 5 kg by March; fasting sugar under 100; three gym sessions a week; dinner before 21:00.">{esc(text)}</textarea>'
            '<p class="row"><button class="primary" type="submit">Save</button></p></form></div>'
            '<div class="card"><h2>How the coach works</h2><ul class="small">'
            '<li>Only when you press Coach me (Today) or Review this week (Week page).</li>'
            '<li>Reads food, health values, sleep, habits, gym and home; never thoughts, private or gratitude entries.</li>'
            f'<li>Claude Opus 5.5; about 6 cents a day coaching, about 20 cents a week review; at most {coach.DAILY_LIMIT} a day.</li>'
            '<li>Every coaching is kept on the Coach page (under More); its cost is on the Usage page.</li></ul></div>')
