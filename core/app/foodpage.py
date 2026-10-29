"""The web app's Food page: the food log (one day with its entries to change, or a date range with day totals against
the targets), the catalogue (the foods with their values, words, units and barcode) and the usual meals.
The buttons and forms post to webfood.py; this module only reads."""
import re
from datetime import date, timedelta
from urllib.parse import urlencode

from . import rules
from .db import connect
from .todaypage import MACROS, _cols, _head, _n, _plain
from .webentry import esc, hidden, key

# The per-100 values a food can be corrected in (v1's names)
NUT = (("kcal", "kcal"), ("protein", "Protein g"), ("fat", "Fat g"), ("sat_fat", "Sat fat g"), ("carbs", "Carbs g"),
       ("fibre", "Fibre g"), ("sugar_total", "Sugar g"), ("sugar_added", "Added sugar g"), ("sodium_mg", "Sodium mg"),
       ("caffeine_mg", "Caffeine mg"))
FILTERS = (("all", "All"), ("drinks", "Drinks"), ("new", "Added in the last 14 days"), ("review", "Needs review"))
# The catalogue's orders (fixed SQL texts, picked by key): key, label, ORDER BY
SORTS = (("eaten", "Most eaten", "count(e.id) DESC, lower(f.name)"),
         ("recent", "Most eaten in the last 30 days",
          "count(e.id) FILTER (WHERE e.day > (now() AT TIME ZONE 'Europe/Berlin')::date - 30) DESC, count(e.id) DESC, lower(f.name)"),
         ("last", "Eaten most recently", "max(e.day) DESC NULLS LAST, lower(f.name)"),
         ("added", "Newest added", "f.created_at DESC, f.id DESC"),
         ("kcal", "Most calories", "f.kcal DESC, lower(f.name)"),
         ("kcal_low", "Fewest calories", "f.kcal ASC, lower(f.name)"),
         ("protein", "Most protein", "f.protein DESC, lower(f.name)"),
         ("name", "Name A to Z", "lower(f.name)"))
MAX_RANGE = 366
LIST_LIMIT = 400
BACK = re.compile(r"/web/food(/catalogue|/usual)?(\?[A-Za-z0-9=&._%-]*)?")


def url(path: str, **q) -> str:
    q = {k: v for k, v in q.items() if v not in (None, "", False)}
    return path + ("?" + urlencode(q) if q else "")


def safe_back(b) -> str:
    b = str(b or "")
    return b if BACK.fullmatch(b) else "/web/food"


def parse_day(s, default: date) -> date:
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        return default


def _today(cur) -> date:
    cur.execute("SELECT log_day(now()) AS d")
    return cur.fetchone()["d"]


def _dm(d: date) -> str:
    return d.strftime("%a %d.%m.")


def _food_options(cur, selected=None) -> str:
    cur.execute("SELECT id, name FROM foods WHERE active ORDER BY lower(name)")
    return "".join(f'<option value="{r["id"]}"{" selected" if r["id"] == selected else ""}>{esc(r["name"])}</option>' for r in cur.fetchall())


# ---- one day ---------------------------------------------------------------------------------------

def _day_rows(cur, day: date):
    cur.execute("""SELECT meal, meal_label, kcal, protein, fat, carbs, target_kcal, target_protein, target_fat, target_carbs
                   FROM v_food_day WHERE day = %s ORDER BY meal IS NULL, meal = 0, meal""", (day,))
    sums = cur.fetchall()
    cur.execute("""SELECT l.id, l.meal, l.time, l.food, l.amount, l.unit, l.portion_text, l.kcal, l.protein, l.fat, l.carbs, l.note,
                          e.food_id, e.time_known FROM v_food_log l JOIN food_entries e ON e.id = l.id
                   WHERE l.day = %s ORDER BY l.meal, l.time NULLS LAST, l.id""", (day,))
    return sums, cur.fetchall()


def _entry_panel(cur, csrf: str, i: dict, back: str) -> str:
    t = i["time"].strftime("%H:%M") if i["time"] and i["time_known"] else ""
    base = hidden("csrf", csrf) + hidden("id", f"f{i['id']}") + hidden("back", back)
    return (f'<tr class="panel"><td colspan="5"><form class="fbox" method="post" action="/web/food/entry">{base}<div class="fields">'
            f'<label>Food<select name="food">{_food_options(cur, i["food_id"])}</select></label>'
            f'<label>Amount ({esc(i["unit"])})<input type="number" name="amount" min="0" step="any" inputmode="decimal" value="{_plain(i["amount"])}"></label>'
            f'<label>Time<input type="time" name="time" value="{t}"></label></div>'
            '<p class="row"><button class="primary sm" type="submit" name="act" value="save">Save</button>'
            '<button class="ghost sm bad" type="submit" name="act" value="delete">Delete</button>'
            f'<a class="ghost sm dim" href="{esc(back)}">Cancel</a>'
            '<span class="small">You see the change first, then Yes. The meal follows the time (2-hour rule).</span></p></form></td></tr>')


def _note_panel(csrf: str, i: dict, back: str) -> str:
    return (f'<tr class="panel"><td colspan="5"><form class="fbox row" method="post" action="/web/food/note">{hidden("csrf", csrf)}'
            f'{hidden("id", "f" + str(i["id"]))}{hidden("back", back)}'
            f'<input name="note" value="{esc(i["note"] or "")}" maxlength="300" placeholder="Note on this food" aria-label="Note" class="grow">'
            f'<button class="primary sm" type="submit">Save</button><a class="ghost sm dim" href="{esc(back)}">Cancel</a></form></td></tr>')


def _meal_actions(csrf: str, day: date, today: date, meal: int, back: str) -> str:
    base = hidden("csrf", csrf) + hidden("day", day.isoformat()) + hidden("meal", str(meal))
    copy = (f'<details><summary class="ghost sm">{"Log again" if day == today else "Copy to today"}</summary>'
            f'<form class="fbox row" method="post" action="/web/food/copy">{base}{hidden("key", key())}'
            '<label class="small">at <input type="time" name="time" aria-label="Time"></label>'
            '<button class="primary sm" type="submit">Log meal</button><span class="small">Empty time = now. No AI call.</span></form></details>')
    usual = "" if meal == 0 else (
        f'<details><summary class="ghost sm">Save as usual meal</summary>'
        f'<form class="fbox row" method="post" action="/web/food/usualnew">{base}{hidden("back", back)}'
        '<input name="name" maxlength="60" required placeholder="Name, e.g. usual lunch" aria-label="Name of the usual meal" class="grow">'
        '<button class="primary sm" type="submit">Save as usual meal</button></form></details>')
    return f'<tr class="acts"><td colspan="5"><div class="row">{copy}{usual}</div></td></tr>'


def _pct_class(k: str, value: float, target: float) -> str:
    """kcal, fat and carbs are targets not to go over; protein is one to reach (decided 26.10.2026)."""
    p = value / target * 100
    if k == "protein":
        return "good" if p >= 100 else "warn" if p >= 80 else ""
    return "good" if p <= 100 else "warn" if p <= 110 else "bad"


def _target_rows(total) -> str:
    """Total, target, % of target and what is left (+) or over (−), under the day's food."""
    keys = [k for k, _, _ in MACROS]
    if not total or not any(total["target_" + k] for k in keys):
        return ""
    v = {k: float(total[k] or 0) for k in keys}
    t = {k: float(total["target_" + k]) if total["target_" + k] else None for k in keys}
    pct = "".join(f'<td class="{_pct_class(k, v[k], t[k])}">{round(v[k] / t[k] * 100)}%</td>' if t[k] else "<td>–</td>" for k in keys)
    left = "".join((f'<td class="{_pct_class(k, v[k], t[k])}">{"+" if t[k] - v[k] >= 0 else "−"}{_n(abs(t[k] - v[k]))}</td>') if t[k] else "<td>–</td>"
                   for k in keys)
    return ('<tfoot><tr class="sum"><td>Total</td>' + "".join(f"<td>{_n(v[k])}</td>" for k in keys) + "</tr>"
            '<tr class="tg"><td>Target</td>' + "".join(f"<td>{_n(t[k]) if t[k] else '–'}</td>" for k in keys) + "</tr>"
            f'<tr class="tg"><td>% of target</td>{pct}</tr><tr class="tg"><td>Left (+) / over (−)</td>{left}</tr></tfoot>')


def day_table(cur, csrf: str, day: date, today: date, edit=None, note=None, editable: bool = True) -> str:
    sums, items = _day_rows(cur, day)
    if not items:
        return '<p class="empty">Nothing logged on this day.</p>'
    back = url("/web/food", day=day.isoformat())
    by: dict = {}
    for i in items:
        by.setdefault(i["meal"], []).append(i)
    groups = []
    for r in sums:
        if r["meal"] is None or r["meal"] not in by:
            continue
        rows = by[r["meal"]]
        times = [i["time"] for i in rows if i["time"]]
        when = f' <span class="small">{min(times).strftime("%H:%M")}</span>' if times and r["meal"] != 0 else ""
        lines = []
        for i in rows:
            fid = f"f{i['id']}"
            amount = i["portion_text"] or f"{_n(i['amount'])} {i['unit']}"
            name = esc(i["food"])
            if editable:
                name = f'<a href="{esc(url("/web/food", day=day.isoformat(), edit=fid))}#{fid}" title="Change or delete">{name}</a>'
                name += f' <a class="icon" href="{esc(url("/web/food", day=day.isoformat(), note=fid))}#{fid}" title="Add or change the note" aria-label="Note">&#9998;</a>'
            nl = f'<span class="notel">{esc(i["note"])}</span>' if i["note"] else ""
            lines.append(f'<tr class="item{" sel" if fid in (edit, note) else ""}" id="{fid}"><td>{name} <span class="small">{esc(amount)}</span>{nl}</td>{_cols(i)}</tr>')
            if editable and fid == edit:
                lines.append(_entry_panel(cur, csrf, i, back))
            elif editable and fid == note:
                lines.append(_note_panel(csrf, i, back))
        if editable:
            lines.append(_meal_actions(csrf, day, today, r["meal"], back))
        groups.append(f'<tbody><tr class="mealrow"><td>{esc(r["meal_label"])}{when}</td>{_cols(r)}</tr>{"".join(lines)}</tbody>')
    foot = _target_rows(next((r for r in sums if r["meal"] is None), None)) if editable else ""
    return f'<div class="tablewrap"><table class="ft">{_head()}{"".join(groups)}{foot}</table></div>'


def _day_view(cur, csrf: str, day: date, today: date, edit, note) -> str:
    sums, _ = _day_rows(cur, day)
    total = next((r for r in sums if r["meal"] is None), None)
    line = (f' <span class="small">· {_n(total["kcal"])} kcal · P {_n(total["protein"])} · F {_n(total["fat"])} · C {_n(total["carbs"])}</span>'
            if total else "")
    prev, nxt = day - timedelta(days=1), day + timedelta(days=1)
    nav = (f'<form class="row" method="get" action="/web/food">'
           f'<a class="ghost" href="{esc(url("/web/food", day=prev.isoformat()))}" aria-label="Previous day">&larr;</a>'
           f'<input type="date" name="day" value="{day.isoformat()}" max="{today.isoformat()}" aria-label="Day" class="autosubmit">'
           f'<button class="ghost sm" type="submit">Show</button>'
           + (f'<a class="ghost" href="{esc(url("/web/food", day=nxt.isoformat()))}" aria-label="Next day">&rarr;</a>' if day < today else "")
           + ('' if day == today else '<a class="ghost dim" href="/web/food">Today</a>') + '</form>')
    return (f'<div class="card"><div class="head sub">{_modes("day")}{nav}</div>'
            f'<h2>{_dm(day)}{" · today" if day == today else ""}{line}</h2>{day_table(cur, csrf, day, today, edit, note)}'
            '<p class="small hint">Click a food to change its amount, time or food, or to delete it. The pencil adds a note. '
            'Colours: kcal, fat and carbs green up to the target, amber up to 10% over, red beyond; '
            'protein green once reached, amber from 80%.</p></div>')


# ---- date range ------------------------------------------------------------------------------------

def _range_view(cur, csrf: str, start: date, end: date, today: date, open_day) -> str:
    cur.execute("""SELECT day, kcal, protein, fat, carbs, target_kcal, target_protein, target_fat, target_carbs
                   FROM v_food_day WHERE meal IS NULL AND day BETWEEN %s AND %s""", (start, end))
    tot = {r["day"]: r for r in cur.fetchall()}
    newest = next((tot[d] for d in sorted(tot, reverse=True) if tot[d]["target_kcal"]), None)
    keys = (("kcal", "kcal", "kcal"), ("protein", "Protein g", "P"), ("fat", "Fat g", "F"), ("carbs", "Carbs g", "C"))
    heads = "".join(f'<th><span class="long">{lg}</span><span class="short">{sh}</span>'
                    + (f' <span class="small">/ {_n(newest["target_" + k])}</span>' if newest and newest["target_" + k] else "") + "</th>"
                    for k, lg, sh in keys)
    rows, full = [], []
    d = end
    while d >= start:
        r = tot.get(d)
        here = url("/web/food", **{"from": start.isoformat(), "to": end.isoformat()})
        if r and r["kcal"] is not None:
            if d != today:
                full.append(r)
            cells = ""
            for k, _l, _s in keys:
                t, v = r["target_" + k], r[k]
                cls = "over" if k == "kcal" and t and v > t else "met" if k == "protein" and t and v >= t else ""
                cells += f'<td class="{cls}">{_n(v)}</td>'
            link = url("/web/food", **{"from": start.isoformat(), "to": end.isoformat(), "open": "" if open_day == d else d.isoformat()})
            rows.append(f'<tr class="click{" sel" if open_day == d else ""}"><td><a href="{esc(link)}#d{d.isoformat()}" id="d{d.isoformat()}">{_dm(d)}</a>'
                        f'{" <span class=small>so far</span>" if d == today else ""}</td>{cells}</tr>')
            if open_day == d:
                rows.append(f'<tr class="panel"><td colspan="5">{day_table(cur, csrf, d, today, editable=False)}'
                            f'<p><a class="ghost sm" href="{esc(url("/web/food", day=d.isoformat()))}">Open this day to change it</a></p></td></tr>')
        else:
            rows.append(f'<tr><td>{_dm(d)}</td><td colspan="4" class="small">nothing logged</td></tr>')
        d -= timedelta(days=1)
    if full:
        avg = "".join(f"<td>{_n(sum(float(r[k]) for r in full) / len(full))}</td>" for k, _l, _s in keys)
        rows.append(f'<tr class="sum"><td>Average <span class="small">{len(full)} full days</span></td>{avg}</tr>')
    m0 = today.replace(day=1)
    lm_end = m0 - timedelta(days=1)
    quick = (("7 days", today - timedelta(days=6), today), ("30 days", today - timedelta(days=29), today),
             ("This month", m0, today), ("Last month", lm_end.replace(day=1), lm_end))
    chips = "".join(f'<a class="chip{" on" if (a, b) == (start, end) else ""}" href="{esc(url("/web/food", **{"from": a.isoformat(), "to": b.isoformat()}))}">{lbl}</a>'
                    for lbl, a, b in quick)
    pick = (f'<form class="row" method="get" action="/web/food"><input type="date" name="from" value="{start.isoformat()}" max="{today.isoformat()}" aria-label="From">'
            f' to <input type="date" name="to" value="{end.isoformat()}" max="{today.isoformat()}" aria-label="To">'
            '<button class="ghost sm" type="submit">Show</button></form>')
    return (f'<div class="card"><div class="head sub">{_modes("range", start, end)}{pick}</div><p class="chips">{chips}</p>'
            f'<div class="tablewrap"><table class="ft"><thead><tr><th>Day</th>{heads}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="small hint">Day totals against your targets: kcal over the target in amber, protein reached in green. '
            'Today is left out of the average because it is not over yet. Click a day to see its log.</p></div>')


def _modes(on: str, start=None, end=None) -> str:
    rng = url("/web/food", **({"from": start.isoformat(), "to": end.isoformat()} if start else {"range": "7"}))
    return (f'<nav class="seg" aria-label="View"><a href="/web/food"{' aria-current="page"' if on == "day" else ""}>One day</a>'
            f'<a href="{esc(rng)}"{' aria-current="page"' if on == "range" else ""}>Date range</a></nav>')


def _tabs(on: str) -> str:
    return (f'<div class="head"><h1>Food</h1><nav class="seg" aria-label="Food"><a href="/web/food"{' aria-current="page"' if on == "log" else ""}>Food log</a>'
            f'<a href="/web/food/catalogue"{' aria-current="page"' if on == "cat" else ""}>Catalogue</a>'
            f'<a href="/web/food/usual"{' aria-current="page"' if on == "usual" else ""}>Usual meals</a></nav></div>')


def render_log(csrf: str, q) -> str:
    with connect() as conn, conn.cursor() as cur:
        today = _today(cur)
        if q.get("from") or q.get("to") or q.get("range"):
            end = min(parse_day(q.get("to"), today), today)
            start = parse_day(q.get("from"), end - timedelta(days=6))
            if start > end:
                start, end = end, start
            start = max(start, end - timedelta(days=MAX_RANGE - 1))
            open_day = parse_day(q.get("open"), None) if q.get("open") else None
            body = _range_view(cur, csrf, start, end, today, open_day)
        else:
            day = min(parse_day(q.get("day"), today), today)
            body = _day_view(cur, csrf, day, today, q.get("edit"), q.get("note"))
    return _tabs("log") + body


# ---- catalogue -------------------------------------------------------------------------------------

def _food_list(cur, text: str, flt: str, sort: str = "eaten"):
    where, args = ["f.active"], []
    t = text.strip().lower()
    if t:
        m = re.fullmatch(r"n?(\d{1,7})", t)
        where.append("(lower(f.name) LIKE %s OR EXISTS (SELECT 1 FROM unnest(f.aliases) a WHERE lower(a) LIKE %s) OR f.barcode = %s"
                     + (" OR f.id = %s" if m else "") + ")")
        args += [f"%{t}%", f"%{t}%", t] + ([int(m[1])] if m else [])
    where.append({"drinks": "f.is_drink", "new": "f.created_at > now() - interval '14 days'", "review": "f.needs_review"}.get(flt, "true"))
    order = next((o for k, _l, o in SORTS if k == sort), SORTS[0][2])
    cur.execute(f"""SELECT f.id, f.name, f.basis, f.kcal, f.protein, f.fat, f.carbs, f.is_drink, f.needs_review, f.created_at,
                           count(e.id) AS n, max(e.day) AS last
                    FROM foods f LEFT JOIN food_entries e ON e.food_id = f.id WHERE {" AND ".join(where)}
                    GROUP BY f.id ORDER BY {order} LIMIT %s""", args + [LIST_LIMIT])  # where and order: fixed texts only
    return cur.fetchall()


def _food_detail(cur, csrf: str, fid: int, back: str) -> str:
    cur.execute("SELECT * FROM foods WHERE id = %s", (fid,))
    f = cur.fetchone()
    if not f:
        return '<p class="empty">That food is not there any more.</p>'
    cur.execute("SELECT count(*) AS n, min(day) AS a, max(day) AS b FROM food_entries WHERE food_id = %s", (fid,))
    use = cur.fetchone()
    cur.execute("SELECT unit, grams, source FROM food_units WHERE food_id = %s ORDER BY unit", (fid,))
    units = cur.fetchall()
    base = hidden("csrf", csrf) + hidden("id", f"N{fid}") + hidden("back", back)
    vals = "".join(f'<label>{lbl}<input type="number" name="{k}" step="any" inputmode="decimal" value="{_plain(f[k])}" placeholder="unknown"></label>'
                   for k, lbl in NUT)
    past = (f'<label class="row small"><input type="checkbox" name="past" value="yes"> Also recalculate the {use["n"]} past '
            f'entr{"y" if use["n"] == 1 else "ies"} ({use["a"]:%d.%m.%Y} to {use["b"]:%d.%m.%Y})</label>' if use["n"] else "")
    words = "".join(f'<form class="word" method="post" action="/web/food/words">{base}{hidden("act", "remove")}{hidden("word", w)}'
                    f'{esc(w)}<button class="icon" type="submit" aria-label="Remove {esc(w)}" title="Remove">&times;</button></form>'
                    for w in (f["aliases"] or []))
    review = (f'<form class="row" method="post" action="/web/food/reviewed">{base}<span class="pill warn">needs review</span>'
              f'<span class="small">{esc(f["review_reason"] or "")}</span><button class="ghost sm" type="submit">Looks right</button></form>'
              if f["needs_review"] else "")
    src = " · ".join(x for x in (f["source"], f["brand"], f["source_note"]) if x)
    return (f'<div class="fbox stack"><p class="small">Source: {esc(src)} · added {f["created_at"]:%d.%m.%Y} · eaten {use["n"]} times'
            f'{f", last " + use["b"].strftime("%d.%m.%Y") if use["b"] else ""} · values per 100 {esc(f["basis"])}</p>{review}'
            f'<form method="post" action="/web/food/values">{base}<div class="fields">{vals}</div>{past}'
            '<p class="row"><button class="primary sm" type="submit">Save</button><span class="small">You see the change first, then Yes. '
            'Without the tick only new entries use the new values.</span></p></form>'
            f'<div><p class="small">Words the logger knows it by</p><div class="row">{words or "<span class=empty>none</span>"}'
            f'<form class="row" method="post" action="/web/food/words">{base}{hidden("act", "add")}'
            '<input name="word" maxlength="60" placeholder="add a word" aria-label="Add a word"><button class="ghost sm" type="submit">Add</button></form></div></div>'
            f'<div class="grid2"><div><p class="small">Units</p>{"<br>".join(f"1 {esc(u["unit"])} = {_plain(u["grams"])} {esc(f["basis"])}" for u in units) or "<span class=empty>none</span>"}</div>'
            f'<form method="post" action="/web/food/barcode">{base}<p class="small">Barcode</p><div class="row">'
            f'<input name="barcode" value="{esc(f["barcode"] or "")}" inputmode="numeric" maxlength="14" placeholder="type or paste" aria-label="Barcode">'
            '<button class="primary sm" type="submit">Save</button></div></form></div></div>')


def _usual_meals(cur, csrf: str, um, back: str) -> str:
    cur.execute("""SELECT u.id, u.name, i.id AS item, i.grams, f.id AS food_id, f.name AS food, f.basis, f.kcal, f.protein
                   FROM usual_meals u LEFT JOIN usual_meal_items i ON i.meal_id = u.id LEFT JOIN foods f ON f.id = i.food_id
                   ORDER BY lower(u.name), i.sort_order, i.id""")
    meals: dict = {}
    for r in cur.fetchall():
        meals.setdefault((r["id"], r["name"]), []).append(r)
    out = []
    for (uid, name), items in meals.items():
        items = [i for i in items if i["item"]]
        kcal = sum(float(i["kcal"] or 0) * float(i["grams"]) / 100 for i in items)
        prot = sum(float(i["protein"] or 0) * float(i["grams"]) / 100 for i in items)
        open_ = um == f"U{uid}"
        link = url("/web/food/usual", um="" if open_ else f"U{uid}")
        head = (f'<p class="row"><a href="{esc(link)}#U{uid}" id="U{uid}"><b>{esc(name)}</b></a>'
                f'<span class="small">{len(items)} items · {_n(kcal)} kcal · P {_n(prot)}</span></p>')
        if open_:
            opts = _food_options(cur)
            rows = "".join(f'<tr><td><select name="food_{k}" aria-label="Food">{_food_options(cur, i["food_id"])}</select></td>'
                           f'<td><input type="number" name="g_{k}" min="0" step="any" value="{_plain(i["grams"])}" aria-label="Amount"> {esc(i["basis"])}</td>'
                           f'<td><label class="small"><input type="checkbox" name="rm_{k}" value="1"> remove</label></td></tr>'
                           for k, i in enumerate(items))
            head += (f'<form class="fbox" method="post" action="/web/food/usualmeal">{hidden("csrf", csrf)}{hidden("id", f"U{uid}")}'
                     f'{hidden("rows", str(len(items)))}{hidden("back", back)}'
                     f'<p class="row"><label class="small">Name <input name="name" value="{esc(name)}" maxlength="60" required></label></p>'
                     f'<div class="tablewrap"><table class="ft plain"><tbody>{rows}'
                     f'<tr><td><select name="add_food" aria-label="Add a food"><option value="">add a food …</option>{opts}</select></td>'
                     '<td><input type="number" name="add_g" min="0" step="any" placeholder="g" aria-label="Amount of the added food"></td><td></td></tr>'
                     '</tbody></table></div><p class="row"><button class="primary sm" type="submit" name="act" value="save">Save</button>'
                     '<button class="ghost sm bad" type="submit" name="act" value="delete">Delete</button>'
                     '<a class="ghost sm dim" href="/web/food/usual">Cancel</a>'
                     '<span class="small">You see the change first, then Yes. Past meals keep their weights.</span></p></form>')
        out.append(f'<div class="um">{head}</div>')
    return "".join(out) or '<p class="empty">No usual meals yet. Save one from a meal in the food log.</p>'


def render_catalogue(csrf: str, q) -> str:
    text, flt = str(q.get("q", ""))[:60], q.get("f", "all")
    flt = flt if flt in dict(FILTERS) else "all"
    srt = q.get("s", "eaten")
    srt = srt if srt in {k for k, _l, _o in SORTS} else "eaten"
    s_q = "" if srt == "eaten" else srt  # the default order is left out of the links
    sel = q.get("food", "")
    m = re.fullmatch(r"N(\d+)", sel)
    back = url("/web/food/catalogue", q=text, f="" if flt == "all" else flt, s=s_q, food=sel)
    with connect() as conn, conn.cursor() as cur:
        rows = _food_list(cur, text, flt, srt)
        body = []
        for r in rows:
            nid = f"N{r['id']}"
            open_ = sel == nid
            link = url("/web/food/catalogue", q=text, f="" if flt == "all" else flt, s=s_q, food="" if open_ else nid)
            flags = (' <span class="pill warn">review</span>' if r["needs_review"] else "") + (' <span class="pill">drink</span>' if r["is_drink"] else "")
            body.append(f'<tr class="click{" sel" if open_ else ""}" id="{nid}"><td><a href="{esc(link)}#{nid}"><span class="small">{nid}</span> {esc(r["name"])}</a>{flags}</td>'
                        + "".join(f"<td>{_n(r[k], 1) if r[k] is not None and float(r[k]) % 1 else _n(r[k])}</td>" for k in ("kcal", "protein", "fat", "carbs"))
                        + f'<td class="hide-sm">{r["n"]}</td><td class="hide-sm">{r["last"].strftime("%d.%m.") if r["last"] else "–"}</td>'
                        + f'<td class="hide-sm">{r["created_at"].astimezone(rules.BERLIN).strftime("%d.%m.%y")}</td></tr>')
            if open_ and m:
                body.append(f'<tr class="panel"><td colspan="8">{_food_detail(cur, csrf, int(m[1]), back)}</td></tr>')
    chips = "".join(f'<a class="chip{" on" if k == flt else ""}" href="{esc(url("/web/food/catalogue", q=text, f="" if k == "all" else k, s=s_q))}">{lbl}</a>'
                    for k, lbl in FILTERS)
    sort_opts = "".join(f'<option value="{k}"{" selected" if k == srt else ""}>{lbl}</option>' for k, lbl, _o in SORTS)
    search = (f'<form class="row" method="get" action="/web/food/catalogue"><input type="search" name="q" value="{esc(text)}" class="grow" '
              'placeholder="Search by name, word, N number or barcode" aria-label="Search foods">'
              f'{hidden("f", flt) if flt != "all" else ""}<button class="ghost" type="submit">Search</button>'
              f'<label class="small">Sort by <select name="s" class="autosubmit" aria-label="Sort by">{sort_opts}</select></label></form>')
    lookup = (f'<details class="lookup"><summary class="ghost sm">New food</summary><form class="fbox row" method="post" action="/web/food/lookup">'
              f'{hidden("csrf", csrf)}<input name="name" maxlength="120" required class="grow" placeholder="Food name or barcode" aria-label="Food name or barcode">'
              '<button class="primary sm" type="submit">Look up</button><span class="small">USDA and Open Food Facts, like the chat. '
              'The result waits on Questions for your Add.</span></form></details>')
    more = f'<p class="small">Showing the first {LIST_LIMIT}; search to find others.</p>' if len(rows) == LIST_LIMIT else ""
    table = (f'<div class="tablewrap"><table class="ft"><thead><tr><th>Food <span class="small">per 100 g or ml</span></th><th>kcal</th>'
             '<th><span class="long">Protein</span><span class="short">P</span></th><th><span class="long">Fat</span><span class="short">F</span></th>'
             '<th><span class="long">Carbs</span><span class="short">C</span></th><th class="hide-sm">Eaten</th><th class="hide-sm">Last</th>'
             '<th class="hide-sm">Added</th></tr></thead>'
             f'<tbody>{"".join(body) or "<tr><td colspan=8 class=empty>No food matches.</td></tr>"}</tbody></table></div>')
    label = next(lbl for k, lbl, _o in SORTS if k == srt)
    return (_tabs("cat") + f'<div class="card">{search}{lookup}<p class="chips">{chips}</p>{table}{more}'
            f'<p class="small hint">Sorted by: {label.lower()}. Click a food to see and change it.</p></div>')


def render_usual(csrf: str, q) -> str:
    um = str(q.get("um", ""))[:12]
    with connect() as conn, conn.cursor() as cur:
        body = _usual_meals(cur, csrf, um, url("/web/food/usual", um=um))
    return (_tabs("usual") + f'<div class="card">{body}'
            '<p class="small hint">Click a usual meal to change its foods and weights, rename it or delete it. '
            'New usual meals are saved from a meal in the food log.</p></div>')
