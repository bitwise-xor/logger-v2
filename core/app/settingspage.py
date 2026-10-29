"""The web app's Settings page. Tabs: Devices (the signed-in devices, sign out one or all the others). The forms post to
websettings.py; this module only reads."""
import re
from urllib.parse import urlencode

from . import rules
from .db import connect
from .webentry import esc, hidden

TABS = (("devices", "Devices"),)
BACK = re.compile(r"/web/settings(\?[A-Za-z0-9=&._%+-]*)?")


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
    return _tabs(tab) + {"devices": _devices}[tab](session, csrf)
