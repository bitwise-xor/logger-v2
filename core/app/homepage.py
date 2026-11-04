"""The web app's Home page (03.11.2026, step 1 of the plan in the doc "Logger v2: status and web app plan", tab Current; mockups in
docs/specs/home-page-mockup/ on the branch docs/home-page-spec): the Now tab (the chores waiting longest, each with a tick circle,
and what was done lately), the Zones tab (the whole-flat chores zone by zone with "All done", then each zone's chores), the
History tab (what was ticked, with Remove) and Edit zones (zones and chores; retired, never deleted), plus the Home card on
Today. Ages are plain text, never red. Step 2 (03.11.2026): the time chips and "Pick a job for me" on Now, the Pick page with its
timer (the end is kept on the server; app.js only draws the countdown), "This week N of 3", and the weekly goal on Edit zones. The
actions are in home.py."""
from . import home, rules
from .db import connect
from .webentry import esc, here, hidden, icon, key

TABS = (("now", "/web/home", "Now"), ("zones", "/web/home/zones", "Zones"), ("history", "/web/home/history", "History"))
SHOWN = 6  # chores on the Now tab; the rest is on Zones
RECENT = 5
HISTORY = 60
LABEL = {5: "5 min", 15: "15 min", 30: "30 min", 60: "1 hour"}
TITLE = {5: "5 minutes", 15: "15 minutes", 30: "30 minutes", 60: "hour"}
TILE_DONE = 0.5  # a zone tile is tinted when it was done less than this share of the interval ago


def _tabs(on: str) -> str:
    links = "".join(f'<a href="{href}"' + (' aria-current="page"' if k == on else "") + f">{label}</a>" for k, href, label in TABS)
    return f'<div class="head"><h1>Home</h1><nav class="seg" aria-label="Home">{links}</nav></div>'


def _when(ts) -> str:
    return f"{ts.astimezone(rules.BERLIN):%d.%m. %H:%M}"


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _tick(p: dict, csrf: str, back: str, name: str) -> str:
    done = p["age"] == 0
    return (f'<form method="post" action="/web/home/tick">{hidden("csrf", csrf)}{hidden("chore", str(p["chore"]["id"]))}{hidden("zone", str(p["zone"]["id"]))}'
            f'{hidden("key", key())}{here(back)}<button class="gtick{" done" if done else ""}" type="submit" aria-pressed="{"true" if done else "false"}" '
            f'aria-label="{"Take the tick off: " if done else "Done: "}{esc(name)}"><span class="box">{icon("check")}</span></button></form>')


def _row(p: dict, csrf: str, back: str, zone_first: bool = False, plain_name: bool = False) -> str:
    c, z = p["chore"], p["zone"]
    name = c["name"] if plain_name else home.label(c, z)
    age, every = home.age_text(p["age"]), home.interval_text(p["interval"])
    meta = f"{every} · {age}" if zone_first else f"{age} · {every}"
    return (f'<div class="horow">{_tick(p, csrf, back, home.label(c, z))}<div class="hobody"><b>{esc(name)}</b><span class="small">{esc(meta)}</span></div>'
            f'<span class="small homin">{c["minutes"]} min</span></div>')


def _minutes(q) -> int:
    try:
        m = int((q or {}).get("min", home.DEFAULT_MINUTES))
    except (TypeError, ValueError):
        return home.DEFAULT_MINUTES
    return m if m in home.HOURS else home.DEFAULT_MINUTES


def _time_card(minutes: int) -> str:
    chips = "".join(f'<a class="hochip{" on" if m == minutes else ""}" href="/web/home?min={m}"' + (' aria-current="true"' if m == minutes else "") + f">{LABEL[m]}</a>"
                    for m in home.HOURS)
    return ('<div class="card"><h2>How much time have you got?</h2><p class="small">One job, picked for you. Stop when the timer ends, finished or not.</p>'
            f'<div class="hochips">{chips}</div><a class="primary gopen" href="/web/home/pick?min={minutes}">Pick a job for me</a></div>')


def _week_card(w: dict) -> str:
    bar = "".join(f'<span class="hoseg{" on" if i < w["this"] else ""}"></span>' for i in range(w["goal"]))
    return (f'<div class="card"><h2><span>This week</span><span class="small">{w["this"]} of {w["goal"]} sessions</span></h2><div class="hobar">{bar}</div>'
            f'<p class="small">Last 4 weeks: {" · ".join(str(x) for x in w["weeks"])}</p>'
            '<p class="small">A session is a day with a job done or a timer run out. Evening resets count on Habits.</p></div>')


def _timer_banner(t: dict, job: dict) -> str:
    """The Timer card on Now; a running timer counts down there too (app.js, data-countdown)."""
    left = "time is up" if t["state"] == "over" else f'{t["left"] // 60}:{t["left"] % 60:02d} left' + (" (paused)" if t["state"] == "paused" else "")
    tick = f' data-countdown="{t["left"]}"' if t["state"] == "running" else ""
    return (f'<div class="card"><h2><span>Timer</span><span class="small"{tick}>{esc(left)}</span></h2><p><b>{esc(job["title"])}</b></p>'
            '<a class="primary gopen" href="/web/home/pick">Open the timer</a></div>')


def render_now(csrf: str, q=None) -> str:
    minutes = _minutes(q)
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
        done = home.recent(cur, RECENT)
        w = home.week(cur)
        t = home.timer_open(cur)
        skips = home.skipped(cur, d["today"])
    if not d["chores"]:
        return (_tabs("now") + '<div class="card"><h2>Home</h2><p class="empty">No zones or chores yet.</p>'
                '<p class="small">Add them on <a href="/web/home/edit">Edit zones</a>.</p></div>')
    job = home.job_of(d, [tuple(x) for x in t["pairs"]]) if t else None
    shown = home.visible(d["pairs"], skips)
    rows = home.worst(shown)
    top = "".join(_row(p, csrf, "/web/home") for p in rows[:SHOWN])
    more = f'<p class="row"><a href="/web/home/zones">Show all {len(rows)} chores</a></p>' + _hidden_line(d, shown) if len(rows) > SHOWN else _hidden_line(d, shown)
    lately = "".join(f'<li><span>{esc(e["label"])}</span><span class="small">{_when(e["at"])}</span></li>' for e in done)
    return (_tabs("now") + (_timer_banner(t, job) if t and job else "") + _time_card(minutes) + _week_card(w)
            + f'<div class="card"><h2><span>Longest since last done</span><span class="small">{_n(len(rows), "chore")}</span></h2>{top}{more}</div>'
            + ('<div class="card"><h2>Recently done</h2><ul class="horecent">' + lately + "</ul></div>" if lately else ""))


def _hidden_line(d: dict, shown: list) -> str:
    """"1 hidden for today": the jobs marked "Not today" (they come back on the next logging day)."""
    n = len(d["pairs"]) - len(shown)
    return f'<p class="small">{n} hidden for today</p>' if n else ""


# ---- Pick a job ---------------------------------------------------------------------------------------

def _post(action: str, back: str, csrf: str, fields: dict, label: str, cls: str = "ghost", key_: bool = False, attrs: str = "") -> str:
    inner = "".join(hidden(k, v) for k, v in fields.items())
    return (f'<form method="post" action="/web/home/{action}"{attrs}>{hidden("csrf", csrf)}{inner}{hidden("back", back)}{hidden("key", key()) if key_ else ""}'
            f'<button class="{cls}" type="submit">{label}</button></form>')


def _clock(t: dict | None, minutes: int) -> str:
    if t is None:
        return f'<div class="hoclock">{minutes:02d}:00</div>'
    left = f'{t["left"] // 60:02d}:{t["left"] % 60:02d}'
    if t["state"] == "running":
        return f'<div class="hoclock" data-left="{t["left"]}">{left}</div><p class="hoover" data-over hidden>Time is up · well done</p>'
    if t["state"] == "over":
        return f'<div class="hoclock over">00:00</div><p class="hoover">Time is up · well done</p>'
    return f'<div class="hoclock">{left}</div><p class="small">Paused.</p>'


def render_pick(csrf: str, q=None) -> str:
    q = q or {}
    minutes = _minutes(q)
    try:
        n = max(0, int(q.get("n", 0)))
    except (TypeError, ValueError):
        n = 0
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
        t = home.timer_open(cur)
        hidden_ = home.skipped(cur, d["today"])
    top = '<p class="row"><a href="/web/home">‹ Home</a></p>'
    if not d["chores"]:
        return top + '<div class="card"><p class="empty">No zones or chores yet.</p></div>'
    job = home.job_of(d, [tuple(x) for x in t["pairs"]]) if t else None
    if t and job:
        minutes, jobs = t["minutes"], []
    else:
        t = None
        jobs = home.candidates(d, minutes, hidden_)
        if not jobs:
            return (top + f'<h1>Your {TITLE.get(minutes, str(minutes) + " minutes")}</h1><div class="card"><p class="empty">Nothing is waiting for you right now.</p>'
                    '<p class="small">Everything is done today, hidden for today, or retired.</p></div>')
        n %= len(jobs)
        job = jobs[n]
    back = f"/web/home/pick?min={minutes}" + (f"&n={n}" if jobs else "")
    pairs = home.pairs_value(job["pairs"])
    chips = (f'<span class="pill ok">{esc(job["zone"])}</span><span class="pill">{esc(home.age_text(job["age"]))}</span>'
             + (f'<span class="small">Job {n + 1} of {len(jobs)}</span>' if len(jobs) > 1 else ""))
    steps = ('<ol class="steps">' + "".join(f"<li>{esc(x)}</li>" for x in job["steps"]) + "</ol>") if job["steps"] else ""
    note = (f'<p class="small">This job takes about {job["minutes"]} minutes: do what fits, stopping counts.</p>' if job["over"] else "")
    howto = f'<p class="small">{esc(job["howto"])}</p>' if job["howto"] else ""
    card = f'<div class="card hojob"><p class="row">{chips}</p><h2>{esc(job["title"])}</h2>{steps}{howto}{note}</div>'
    if t:
        # at zero (on the server, or in app.js while the page is open) Pause goes and "Stop timer" becomes "Finished"
        tid = {"timer": str(t["id"]), "pairs": pairs}
        ctl = (_post("pause", back, csrf, tid, "Pause", "primary", attrs=" data-gone-at-zero") if t["state"] == "running" else
               _post("resume", back, csrf, tid, "Resume", "primary") if t["state"] == "paused" else "")
        timer = f'<div class="card hotimer">{_clock(t, minutes)}<div class="row">{ctl}</div></div>'
        buttons = (_post("pickdone", "/web/home", csrf, {"pairs": pairs, "timer": str(t["id"])}, "Done", "primary", True)
                   + _post("stop", back, csrf, {"timer": str(t["id"])}, "Finished" if t["state"] == "over" else "Stop timer",
                           attrs=' data-finish-at-zero' if t["state"] == "running" else ""))
        also = ""
    else:
        timer = (f'<div class="card hotimer">{_clock(None, minutes)}'
                 + _post("start", back, csrf, {"pairs": pairs, "minutes": str(minutes)}, "Start timer", "primary")
                 + '<p class="small">When the timer ends, stop. It counts as a session, finished or not.</p></div>')
        buttons = (_post("pickdone", "/web/home", csrf, {"pairs": pairs}, "Done", "ghost", True)  # "Start timer" is the one main button
                   + (f'<a class="ghost" href="/web/home/pick?min={minutes}&n={n + 1}">Another job</a>' if len(jobs) > 1 else "")
                   + _post("skip", back, csrf, {"pairs": pairs}, "Not today"))
        more = [(i, jobs[i]) for i in ((n + 1) % len(jobs), (n + 2) % len(jobs)) if i != n]
        more = list(dict(more).items())
        also = (f'<h3 class="small">Also fits in {minutes} minutes</h3><ul class="horecent">'
                + "".join(f'<li><a href="/web/home/pick?min={minutes}&n={i}">{esc(j["title"])}</a></li>' for i, j in more) + "</ul>") if more else ""
    return (top + f'<h1>Your {TITLE.get(minutes, str(minutes) + " minutes")}</h1>{card}{timer}<div class="hobtns">{buttons}</div>{also}')


def _tile(p: dict, csrf: str, back: str) -> str:
    z = p["zone"]
    tint = p["age"] is not None and (p["age"] == 0 or p["age"] < p["interval"] * TILE_DONE)
    return (f'<form method="post" action="/web/home/tick">{hidden("csrf", csrf)}{hidden("chore", str(p["chore"]["id"]))}{hidden("zone", str(z["id"]))}'
            f'{hidden("key", key())}{here(back)}<button class="hotile{" done" if tint else ""}" type="submit" title="{esc(z["name"])}: {esc(home.interval_text(p["interval"]))}" '
            f'aria-label="{esc(p["chore"]["name"])} {esc(z["name"])}: {esc(home.age_text(p["age"]))}. Tap when it is done."><b>{esc(z["short"])}</b>'
            f'<span>{esc(home.age_short(p["age"]))}</span></button></form>')


def _flat(d: dict, csrf: str) -> str:
    out = []
    for c in (c for c in d["chores"] if c["zone_id"] is None):
        mine = [p for p in d["pairs"] if p["chore"]["id"] == c["id"]]
        mine.sort(key=lambda p: p["zone"]["sort_order"])
        all_today = bool(mine) and all(p["age"] == 0 for p in mine)  # a second tap takes today's ticks back, like a tile
        label = ("All done ✓", ' aria-pressed="true" title="Done in every zone today. Tap again to take it back."') if all_today else ("All done", "")
        out.append(f'<div class="hoflat"><div class="hoflathead"><h3>{esc(c["name"])} <span class="small">· {esc(home.interval_text(c["interval_days"]))}</span></h3>'
                   f'<form method="post" action="/web/home/alldone">{hidden("csrf", csrf)}{hidden("chore", str(c["id"]))}{hidden("key", key())}{here("/web/home/zones")}'
                   f'<button class="ghost sm" type="submit"{label[1]}>{label[0]}</button></form></div>'
                   f'<div class="hotiles">{"".join(_tile(p, csrf, "/web/home/zones") for p in mine)}</div></div>')
    if not out:
        return ""
    return ('<div class="card"><h2>Whole flat</h2><p class="small">Do it all, or one zone at a time. Tap a zone when it’s done.</p>' + "".join(out) + "</div>")


def _zone(z: dict, d: dict, csrf: str) -> str:
    mine = [p for p in d["pairs"] if p["zone"]["id"] == z["id"] and p["chore"]["zone_id"] == z["id"]]
    if not mine:
        if z["name"].lower() == "storage":
            return f'<a class="hozrow" href="/web/ideas/todoist"><b>{esc(z["name"])}</b><span class="small">sorting jobs in Todoist ›</span></a>'
        return f'<div class="hozrow"><b>{esc(z["name"])}</b><span class="small">no recurring chores</span></div>'
    mine.sort(key=lambda p: (p["chore"]["sort_order"], p["chore"]["id"]))
    oldest = min(mine, key=lambda p: p["rank"])
    recent = any(p["age"] is not None and p["age"] <= 14 for p in mine)
    rows = "".join(_row(p, csrf, "/web/home/zones", zone_first=True, plain_name=True) for p in mine)
    return (f'<details class="card hozone"{" open" if recent else ""}><summary><b>{esc(z["name"])}</b>'
            f'<span class="small">{_n(len(mine), "chore")} · oldest {"not yet" if oldest["age"] is None else home.age_text(oldest["age"])}</span></summary>{rows}</details>')


def render_zones(csrf: str, q=None) -> str:
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
    edit = '<p class="row"><a class="ghost" href="/web/home/edit">Edit zones</a></p>'
    if not d["chores"]:
        return _tabs("zones") + edit + '<div class="card"><p class="empty">No zones or chores yet.</p></div>'
    return _tabs("zones") + edit + _flat(d, csrf) + "".join(_zone(z, d, csrf) for z in d["zones"])


def render_history(csrf: str, q=None) -> str:
    with connect() as conn, conn.cursor() as cur:
        rows = home.recent(cur, HISTORY)
    if not rows:
        return _tabs("history") + '<div class="card"><p class="empty">Nothing ticked yet.</p></div>'
    lines = "".join(
        f'<tr><td>{_when(e["at"])}</td><td>{esc(e["label"])}</td><td class="small">{esc(e["source"])}</td>'
        f'<td><form method="post" action="/web/home/remove">{hidden("csrf", csrf)}{hidden("ids", ",".join(str(i) for i in e["ids"]))}{here("/web/home/history")}'
        f'<button class="icon" type="submit" aria-label="Remove {esc(e["label"])}" title="Remove">&times;</button></form></td></tr>' for e in rows)
    return (_tabs("history") + f'<div class="card"><h2><span>Ticked lately</span><span class="small">last {len(rows)}</span></h2>'
            f'<div class="tablewrap"><table class="ft plain"><tbody>{lines}</tbody></table></div></div>')


# ---- Edit zones ------------------------------------------------------------------------------------

def _chore_form(c: dict | None, zones: list, csrf: str, action: str, button: str, back: str) -> str:
    """`zones` is every zone: the active ones are offered, a retired one only when it is this chore's own (so Save keeps it)."""
    c = c or {}
    here_zone = "flat" if c and c.get("zone_id") is None else str(c.get("zone_id") or "")
    opts = '<option value="flat"' + (" selected" if here_zone == "flat" else "") + ">Whole flat (each zone on its own)</option>" + "".join(
        f'<option value="{z["id"]}"{" selected" if here_zone == str(z["id"]) else ""}>{esc(z["name"])}{"" if z["active"] else " (retired)"}</option>'
        for z in zones if z["active"] or here_zone == str(z["id"]))
    ident = hidden("chore", str(c["id"])) if c.get("id") else ""
    return (f'<form class="fbox" method="post" action="/web/home/{action}">{hidden("csrf", csrf)}{ident}{here(back)}<div class="fields">'
            f'<label>Name<input name="name" maxlength="60" required value="{esc(c.get("name", ""))}" placeholder="e.g. Clean the oven"></label>'
            f'<label>Where<select name="zone">{opts}</select></label>'
            f'<label>Every … days<input type="number" name="days" min="1" max="730" required inputmode="numeric" value="{esc(c.get("interval_days", ""))}" '
            'placeholder="7 = weekly"></label>'
            f'<label>Minutes<input type="number" name="minutes" min="1" max="240" inputmode="numeric" value="{esc(c.get("minutes", 15))}"></label>'
            f'<label>Steps (one per line)<textarea class="wide" name="steps" rows="3" maxlength="600">{esc(chr(10).join(c.get("steps") or []))}</textarea></label>'
            f'<label>How-to line (optional)<input name="howto" maxlength="300" value="{esc(c.get("howto") or "")}"></label>'
            f'<label>Other words for typed lines<input name="words" maxlength="200" value="{esc(", ".join(c.get("words") or []))}" placeholder="e.g. oven, stove"></label>'
            f'</div><p class="row"><button class="primary sm" type="submit">{button}</button></p></form>')


def _chore_edit(c: dict, zones: list, csrf: str, back: str) -> str:
    on = c["active"]
    toggle = (f'<form method="post" action="/web/home/choreactive">{hidden("csrf", csrf)}{hidden("chore", str(c["id"]))}{hidden("on", "0" if on else "1")}{here(back)}'
              f'<button class="ghost sm" type="submit">{"Retire" if on else "Bring back"}</button></form>')
    meta = f'{home.interval_text(c["interval_days"])} · {c["minutes"]} min' + ("" if on else " · retired")
    return (f'<div class="hm{"" if on else " retired"}"><details class="hm-pop"><summary class="hm-main">{esc(c["name"])}<span class="small">{esc(meta)}</span></summary>'
            f'{_chore_form(c, zones, csrf, "choreedit", "Save", back)}</details><div class="hm-ctl">{toggle}</div></div>')


def _zone_edit(z: dict, d: dict, csrf: str, back: str) -> str:
    mine = [c for c in d["all_chores"] if c["zone_id"] == z["id"]]
    on = z["active"]
    form = (f'<details class="lookup"><summary class="ghost">Rename or change the zone</summary><form class="fbox" method="post" action="/web/home/zoneedit">'
            f'{hidden("csrf", csrf)}{hidden("zone", str(z["id"]))}{here(back)}<div class="fields">'
            f'<label>Name<input name="name" maxlength="40" required value="{esc(z["name"])}"></label>'
            f'<label>Tile label<input name="short" maxlength="12" value="{esc(z["short"])}"></label>'
            f'<label>Spots (one per line; the steps of a whole-flat chore here)<textarea class="wide" name="spots" rows="4" maxlength="600">{esc(chr(10).join(z["spots"]))}</textarea></label>'
            f'<label>Other words for typed lines<input name="words" maxlength="200" value="{esc(", ".join(z["words"]))}" placeholder="e.g. bedroom"></label></div>'
            '<p class="row"><button class="primary sm" type="submit">Save</button></p></form></details>')
    toggle = (f'<form method="post" action="/web/home/zoneactive">{hidden("csrf", csrf)}{hidden("zone", str(z["id"]))}{hidden("on", "0" if on else "1")}{here(back)}'
              f'<button class="ghost sm" type="submit">{"Retire this zone" if on else "Bring back"}</button><span class="small"> History stays.</span></form>')
    return (f'<div class="card{"" if on else " retired"}"><h2><span>{esc(z["name"])}{"" if on else " · retired"}</span><span class="small">{_n(len([c for c in mine if c["active"]]), "chore")}</span></h2>'
            + "".join(_chore_edit(c, d["all_zones"], csrf, back) for c in mine) + form + toggle + "</div>")


def render_edit(csrf: str, q=None) -> str:
    back = "/web/home/edit"
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
    flat = [c for c in d["all_chores"] if c["zone_id"] is None]
    top = '<p class="row"><a href="/web/home/zones">‹ Zones</a></p>'
    with connect() as conn, conn.cursor() as cur:
        goal = home.goal(cur)
    goal_card = (f'<div class="card"><h2>Weekly goal</h2><form class="row" method="post" action="/web/home/goalset">{hidden("csrf", csrf)}{here(back)}'
                 f'<label>Sessions a week <input type="number" name="goal" min="1" max="7" value="{goal}" inputmode="numeric"></label>'
                 '<button class="primary sm" type="submit">Save</button></form>'
                 '<p class="small">A session is a day with a job done or a timer run out.</p></div>')
    flat_card = ('<div class="card"><h2><span>Whole flat</span><span class="small">each zone is tracked on its own</span></h2>'
                 + "".join(_chore_edit(c, d["all_zones"], csrf, back) for c in flat) + "</div>") if flat else ""
    new_chore = (f'<details class="lookup"><summary class="ghost">+ New chore</summary>{_chore_form(None, d["all_zones"], csrf, "chorenew", "Add", back)}</details>')
    new_zone = (f'<details class="lookup"><summary class="ghost">+ New zone</summary><form class="fbox" method="post" action="/web/home/zonenew">{hidden("csrf", csrf)}{here(back)}'
                '<div class="fields"><label>Name<input name="name" maxlength="40" required placeholder="e.g. Balcony"></label>'
                '<label>Tile label (optional)<input name="short" maxlength="12"></label>'
                '<label>Spots (one per line, optional)<textarea class="wide" name="spots" rows="3" maxlength="600"></textarea></label>'
                '<label>Other words for typed lines<input name="words" maxlength="200" placeholder="e.g. terrace"></label></div>'
                '<p class="row"><button class="primary sm" type="submit">Add</button><span class="small">A retired zone of the same name comes back with its history.</span></p></form></details>')
    return (_tabs("zones") + top + goal_card + flat_card + "".join(_zone_edit(z, d, csrf, back) for z in d["all_zones"])
            + f'<div class="card"><h2>Add</h2>{new_chore}{new_zone}</div>')


# ---- the Home card on Today --------------------------------------------------------------------------

def today_card() -> str:
    """"Home": the chore waiting longest (not one marked "Not today"), this week's sessions, a 15 minutes button and Open Home.
    Empty before there are chores."""
    with connect() as conn, conn.cursor() as cur:
        d = home.load(cur)
        w = home.week(cur)
        skips = home.skipped(cur, d["today"])
    if not d["pairs"]:
        return ""
    shown = home.visible(d["pairs"], skips)
    rows = home.worst(shown)
    if rows:
        p = rows[0]
        nxt = (f'<p class="small">Waiting longest</p><p class="honext"><b>{esc(home.label(p["chore"], p["zone"]))}</b></p>'
               f'<p class="small">{esc(home.age_text(p["age"]))} · {esc(home.interval_text(p["interval"]))}</p>')
    else:
        nxt = '<p class="small">Nothing else is waiting today.</p>'
    return (f'<div class="card homecard"><h2><span>{icon("home")}Home</span><span class="small">this week {w["this"]} of {w["goal"]}</span></h2>'
            f'{nxt}{_hidden_line(d, shown)}'
            '<div class="hobtns"><a class="primary" href="/web/home/pick?min=15">15 minutes</a><a class="ghost" href="/web/home">Open Home</a></div></div>')
