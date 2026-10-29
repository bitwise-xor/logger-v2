"""The web app's Settings page (decided 29.10.2026). Tabs: Food targets (the day's targets, new ones start on a date and the old ones
stay as history), Habits (label, daily count, order, active; add a habit), Today page (what shows on Today and in which order),
Devices (the signed-in devices). The forms post to websettings.py; this module only reads."""
import re
from urllib.parse import urlencode

from . import rules, uiprefs
from .db import connect
from .todaypage import HEALTH_ROWS, _plain
from .webentry import esc, hidden

TABS = (("targets", "Food targets"), ("habits", "Habits"), ("today", "Today page"), ("devices", "Devices"))
BACK = re.compile(r"/web/settings(\?[A-Za-z0-9=&._%+-]*)?")
TARGET_FIELDS = (("kcal", "Calories", "kcal"), ("protein", "Protein", "g"), ("fat", "Fat", "g"), ("sat_fat", "Saturated fat", "g"),
                 ("carbs", "Carbs", "g"), ("fibre", "Fibre", "g"), ("sugar_added", "Added sugar", "g"), ("sodium_mg", "Sodium", "mg"))


def url(**q) -> str:
    q = {k: v for k, v in q.items() if v not in (None, "", False)}
    return "/web/settings" + ("?" + urlencode(q) if q else "")


def safe_back(b) -> str:
    b = str(b or "")
    return b if BACK.fullmatch(b) else "/web/settings"


def device_name(ua: str) -> str:
    """"iPhone, Safari" from a browser's user-agent text (a rough reading; unknown parts are left out)."""
    ua = ua or ""
    system = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                                  ("Macintosh", "Mac"), ("Linux", "Linux")) if k in ua), "")
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("CriOS", "Chrome"), ("FxiOS", "Firefox"),
                                   ("Chrome/", "Chrome"), ("Safari/", "Safari")) if k in ua), "")
    return ", ".join(x for x in (system, browser) if x) or "Unknown device"


def _tabs(on: str) -> str:
    links = "".join(f'<a href="{esc(url(tab=k))}"{' aria-current="page"' if k == on else ""}>{label}</a>' for k, label in TABS)
    return f'<div class="head"><h1>Settings</h1><nav class="seg" aria-label="Settings">{links}</nav></div>'


# ---- food targets ----------------------------------------------------------------------------------

def _targets(session, csrf: str) -> str:
    with connect() as c:
        today = c.execute("SELECT log_day(now()) AS d").fetchone()["d"]
        rows = c.execute("SELECT * FROM food_targets ORDER BY valid_from DESC").fetchall()
    cur = next((r for r in rows if r["valid_from"] <= today), rows[0] if rows else None)
    fields = "".join(f'<label>{lbl} <span class="small">{unit}</span><input type="number" name="{k}" min="0" step="any" inputmode="decimal" '
                     f'value="{_plain(cur[k]) if cur else ""}" placeholder="none"></label>' for k, lbl, unit in TARGET_FIELDS)
    hist = "".join(f'<tr><td>{r["valid_from"]:%d.%m.%Y}</td>' + "".join(f'<td>{_plain(r[k]) or "–"}</td>' for k, _l, _u in TARGET_FIELDS[:5])
                   + f'<td class="l">{esc(r["note"] or "")}</td></tr>' for r in rows)
    since = f"valid from {cur['valid_from']:%d.%m.%Y}" if cur else "none set yet"
    return (f'<div class="card"><h2>Daily food targets <span class="small">· now {since}</span></h2>'
            f'<form method="post" action="/web/settings/targets">{hidden("csrf", csrf)}<div class="fields">'
            f'<label>Valid from<input type="date" name="valid_from" value="{today.isoformat()}" required></label>{fields}</div>'
            '<div class="fields wide"><label>Note (optional)<input name="note" maxlength="200" placeholder="why it changed"></label></div>'
            '<p class="row"><button class="primary" type="submit">Save targets</button>'
            '<span class="small">New targets count from the date; earlier days keep the targets they had. An empty field means no target for it.</span></p></form></div>'
            f'<div class="card"><h2>History</h2><div class="tablewrap"><table class="ft plain"><thead><tr><th>From</th><th>kcal</th><th>Protein</th><th>Fat</th><th>Sat fat</th>'
            f'<th>Carbs</th><th>Note</th></tr></thead><tbody>{hist or "<tr><td colspan=7 class=empty>No targets yet.</td></tr>"}</tbody></table></div></div>')


# ---- habits ----------------------------------------------------------------------------------------

def _habits(session, csrf: str) -> str:
    with connect() as c:
        rows = c.execute("""SELECT name, label, daily_target, sort_order, active FROM log_types WHERE on_checklist AND kind = 'event'
                            ORDER BY active DESC, sort_order, name""").fetchall()
    out = []
    for r in rows:
        out.append(f'<form class="habrow" method="post" action="/web/settings/habit">{hidden("csrf", csrf)}{hidden("name", r["name"])}'
                   f'<label class="grow">Name shown<input name="label" value="{esc(r["label"])}" maxlength="40" required></label>'
                   f'<label>Times a day<input type="number" name="daily_target" min="1" max="10" value="{r["daily_target"]}"></label>'
                   f'<label>Order<input type="number" name="sort_order" min="0" max="999" value="{r["sort_order"]}"></label>'
                   f'<label class="row small"><input type="checkbox" name="active" value="1"{" checked" if r["active"] else ""}> Active</label>'
                   f'<button class="primary sm" type="submit">Save</button><span class="small">{esc(r["name"])}</span></form>')
    return (f'<div class="card"><h2>Habits <span class="small">· {sum(1 for r in rows if r["active"])} active</span></h2>{"".join(out)}'
            '<p class="small hint">Order: the smaller number comes first. Taking Active off retires a habit (its history stays; it leaves Today, '
            'the Habits page and the Sheet). Which habits show on the Today page is set under Today page.</p></div>'
            f'<div class="card"><h2>Add a habit</h2><form class="row" method="post" action="/web/settings/habit_new">{hidden("csrf", csrf)}'
            '<input name="label" maxlength="40" required class="grow" placeholder="Name, e.g. Yoga" aria-label="Name of the new habit">'
            '<label class="small">Times a day <input type="number" name="daily_target" min="1" max="10" value="1"></label>'
            '<button class="primary" type="submit">Add</button></form>'
            '<p class="small hint">Then you can log it in the chat or the entry box by its name ("did yoga") or tick it on Today.</p></div>')


# ---- the Today page --------------------------------------------------------------------------------

def today_items(cur) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """(kind, heading, [(id, label)]) in the order the Today page shows them by default."""
    cur.execute("SELECT name, label FROM log_types WHERE on_checklist AND active AND kind = 'event' ORDER BY sort_order, name")
    habits = [(r["name"], r["label"]) for r in cur.fetchall()]
    health = [(k, f"{label}{' ' + sub if sub else ''}") for k, label, sub, _t, _p, _u in HEALTH_ROWS]
    cur.execute("SELECT id, name FROM usual_meals ORDER BY lower(name)")
    usual = [(str(r["id"]), r["name"]) for r in cur.fetchall()]
    return [("habit", "Habits", habits), ("health", "Health card rows", health), ("usual", "Usual meal buttons", usual),
            ("review", "Box", [("box", "To review")])]


def _today_tab(session, csrf: str) -> str:
    with connect() as c, c.cursor() as cur:
        groups, prefs = today_items(cur), uiprefs.load(cur)
    out = []
    for kind, heading, items in groups:
        items = uiprefs.ordered(items, lambda i: i[0], prefs["order"].get(kind, []))
        rows = "".join(f'<tr><td><label class="row"><input type="checkbox" name="show_{kind}_{i}" value="1"'
                       f'{"" if f"{kind}:{i}" in prefs["hidden"] else " checked"}> {esc(lbl)}</label></td>'
                       + (f'<td><input type="number" name="pos_{kind}_{i}" min="1" max="99" value="{n}" aria-label="Order of {esc(lbl)}"></td>' if len(items) > 1 else "<td></td>")
                       + "</tr>" for n, (i, lbl) in enumerate(items, 1))
        out.append(f'<h3>{heading}</h3><div class="tablewrap"><table class="ft plain"><tbody>{rows or "<tr><td class=empty>None yet.</td></tr>"}</tbody></table></div>')
    return (f'<div class="card"><h2>What shows on the Today page</h2><form method="post" action="/web/settings/today">{hidden("csrf", csrf)}'
            f'{"".join(out)}<p class="row"><button class="primary" type="submit">Save</button></p></form>'
            f'<form class="row" method="post" action="/web/settings/today_reset">{hidden("csrf", csrf)}'
            '<button class="ghost" type="submit">Show everything in the default order</button></form>'
            '<p class="small hint">Food and drinks always show. A hidden item is only hidden on the Today page: it stays on the Habits page, in the Sheet '
            'and in your records. Order: the smaller number comes first. A hidden review box still shows one line while something waits for review.</p></div>')


# ---- devices ---------------------------------------------------------------------------------------

def _devices(session, csrf: str) -> str:
    with connect() as c:
        rows = c.execute("""SELECT sid, token_hash, user_agent, created_at, last_seen, expires_at FROM web_sessions
                            WHERE expires_at > now() ORDER BY last_seen DESC""").fetchall()
    out = []
    for r in rows:
        here = r["token_hash"] == session["token_hash"]
        seen = r["last_seen"].astimezone(rules.BERLIN).strftime("%d.%m.%Y %H:%M")
        made = r["created_at"].astimezone(rules.BERLIN).strftime("%d.%m.%Y")
        until = r["expires_at"].astimezone(rules.BERLIN).strftime("%d.%m.%Y")
        btn = ('<span class="pill ok">this device</span>' if here else
               f'<form method="post" action="/web/settings/device">{hidden("csrf", csrf)}{hidden("sid", str(r["sid"]))}'
               '<button class="ghost sm bad" type="submit">Sign out</button></form>')
        out.append(f'<div class="dev"><div><b>{esc(device_name(r["user_agent"]))}</b>'
                   f'<span class="small">signed in {made} · last used {seen} · stays signed in until {until}</span></div>{btn}</div>')
    others = sum(1 for r in rows if r["token_hash"] != session["token_hash"])
    allout = (f'<form class="row" method="post" action="/web/settings/others">{hidden("csrf", csrf)}'
              f'<button class="ghost bad" type="submit">Sign out all {others} other device{"" if others == 1 else "s"}</button></form>'
              if others else "")
    return (f'<div class="card"><h2>Signed-in devices <span class="small">· {len(rows)}</span></h2>{"".join(out)}{allout}'
            '<p class="small hint">A device stays signed in for 30 days after it signs in. If a phone is lost, sign it out here. '
            'The Sign out button at the top ends this device only.</p></div>')


def render(session, q) -> str:
    tab = q.get("tab") if q.get("tab") in dict(TABS) else TABS[0][0]
    csrf = session["csrf"]
    return _tabs(tab) + {"targets": _targets, "habits": _habits, "today": _today_tab, "devices": _devices}[tab](session, csrf)
