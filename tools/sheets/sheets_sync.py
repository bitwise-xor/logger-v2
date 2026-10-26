#!/usr/bin/env python3
"""Writes v2's food and health data into the Google Sheet "Food Log (view)" (values only).

Reads the views v_food_log, v_food_day, v_measurements, v_sleep and v_events through the read-only database role
sheets_reader (via `docker exec` into the database container) and rewrites these data tabs:
  Log            one row per entry, last 90 days, newest day first (column S = sort key for the summary)
  Daily totals   per day: meal totals, drinks, day total, target, % of target, left (all days)
  Day rows       hidden; per day of the last 90 days the rows Daily summary shows: entries by meal with subtotals,
                 drinks, day total, target, % of target, left (column Q = kind of row, for the colours)
  Health         every calendar day of the last 90 days: weight (+ 7-day average), blood pressure, glucose, sleep
  Habits         every calendar day of the last 90 days: a tick per habit done, counts for the last 7 and 30 days
  Stats          hidden; a few numbers computed here (streaks, data checks)
Layout, formulas, colours and charts live in the sheet (built by sheets_layout.py) and are never touched.
Every run also writes the time into the "Updated" cell of Daily summary and Weekly summary (STAMP).

Environment (sheets/sync.env, mode 600): SHEET_ID, SA_KEY, SYNC_STATE, DB_CONTAINER, DB_USER, DB_NAME,
SHEETS_READER_PASSWORD. Usage: sheets_sync.py [--force] [--dry-run] [--check]
"""
import argparse, datetime as dt, fcntl, hashlib, json, os, re, subprocess, sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
EPOCH = dt.date(1899, 12, 30)
KEEP_DAYS = 90

NUT = ["kcal", "protein", "fat", "sat_fat", "carbs", "fibre", "sugar_total", "sugar_added", "sodium_mg", "caffeine_mg"]
TARGET_FOR = {"kcal": "target_kcal", "protein": "target_protein", "fat": "target_fat", "sat_fat": "target_sat_fat",
              "carbs": "target_carbs", "fibre": "target_fibre", "sugar_added": "target_sugar_added", "sodium_mg": "target_sodium_mg"}

LOG_HEAD = ["Date", "Time", "Meal", "Food", "Amount", "Unit", "kcal", "Protein (g)", "Fat (g)", "Sat fat (g)", "Carbs (g)",
            "Fibre (g)", "Sugar total (g)", "Sugar added (g)", "Sodium (mg)", "Caffeine (mg)", "Note", "ID", "Sort"]
DAILY_HEAD = ["Date", "Row", "Items", "kcal", "Protein (g)", "Fat (g)", "Sat fat (g)", "Carbs (g)", "Fibre (g)",
              "Sugar total (g)", "Sugar added (g)", "Sodium (mg)", "Caffeine (mg)"]
DAYROWS_HEAD = ["Date", "Time", "Meal", "Food", "Amount", "Unit", "kcal", "Protein (g)", "Fat (g)", "Sat fat (g)", "Carbs (g)",
                "Fibre (g)", "Sugar total (g)", "Sugar added (g)", "Sodium (mg)", "Caffeine (mg)", "Kind"]
HEALTH_GROUPS = ["", "", "Weight (kg)", "", "Blood pressure morning", "", "Blood pressure evening", "", "Glucose", "Sleep", "", ""]
HEALTH_HEAD = ["Date", "Day", "Weight", "7-day avg", "Sys", "Dia", "Sys", "Dia", "Fasting (mg/dL)", "Bed", "Wake", "Hours"]
STATS_ROWS = ["Protein target streak (days)", "Days within 10% of kcal target, last 30", "Days logged, last 30",
              "Entries last 90 days", "Entries estimated, last 90 days", "Entries needing review, last 90 days"]
# Stats!B1 = the time of this run; B2.. = STATS_ROWS in order
TABS = ["Log", "Daily totals", "Day rows", "Health", "Habits", "Stats"]
STAMP = {"Daily summary": "O1", "Weekly summary": "J1"}


# ----------------------------------------------------------------------------- helpers

def today():
    return dt.datetime.now(TZ).date()


def d(s):
    return dt.date.fromisoformat(s) if isinstance(s, str) else s


def serial(day):
    return (d(day) - EPOCH).days


def t(s):
    return None if s is None else dt.time.fromisoformat(s)


def tfrac(x):
    if x is None:
        return ""
    if isinstance(x, str):
        x = dt.time.fromisoformat(x)
    return round((x.hour * 3600 + x.minute * 60 + x.second) / 86400, 6)


def ts_frac(iso):
    """Clock time of a timestamptz in Berlin as a day fraction."""
    if iso is None:
        return ""
    x = dt.datetime.fromisoformat(iso).astimezone(TZ)
    return round((x.hour * 3600 + x.minute * 60 + x.second) / 86400, 6)


def num(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return int(v) if v == int(v) else round(v, 4)
    return v


def txt(s):
    """Text for USER_ENTERED: keep text that looks like a formula, number or date as text."""
    if s is None or s == "":
        return ""
    s = str(s)
    if s[0] in "=+-@'" or re.fullmatch(r"[\d.,:/\- ]+", s):
        return "'" + s
    return s


def sort_key(time_str):
    """Order inside a day: entries before 05:00 belong to the end of the day; unknown times last."""
    if time_str is None:
        return 2
    x = dt.time.fromisoformat(time_str)
    return round((x.hour * 3600 + x.minute * 60 + x.second) / 86400 + (1 if x.hour < 5 else 0), 6)


# ----------------------------------------------------------------------------- database

def query(sql):
    pw = os.environ.get("SHEETS_READER_PASSWORD")
    if not pw:
        sys.exit("SHEETS_READER_PASSWORD is not set")
    cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD=" + pw, os.environ.get("DB_CONTAINER", "logger-v2-db"),
           "psql", "-h", "127.0.0.1", "-U", os.environ.get("DB_USER", "sheets_reader"), "-d", os.environ.get("DB_NAME", "appdata"),
           "-At", "-v", "ON_ERROR_STOP=1", "-c", f"select row_to_json(x)::text from ({sql}) x"]
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    if r.returncode:
        raise RuntimeError("database: " + r.stderr.strip()[:300])
    return [json.loads(line) for line in r.stdout.split("\n") if line]


def fetch():
    since = (today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    return {
        "entries": query(f"select * from v_food_log where day >= '{since}'"),
        "days": query("select * from v_food_day"),
        "meas": query(f"select day, type, period, value1, value2, event_at from v_measurements where day >= '{since}'"),
        "sleep": query(f"select wake_day, bed_at, wake_at, hours from v_sleep where wake_day >= '{since}'"),
        "events": query(f"select day, type, event_at from v_events where day >= '{since}'"),
    }


# ----------------------------------------------------------------------------- tab builders

def build_log(entries):
    rows = [list(LOG_HEAD)]
    order = sorted(entries, key=lambda r: (-serial(r["day"]), r["time"] is None, -(sort_key(r["time"]) if r["time"] else 0),
                                           -r["meal"], -r["id"]))
    for r in order:
        rows.append([serial(r["day"]), tfrac(r["time"]), "Drink" if r["is_drink"] else f"Meal {r['meal']}", txt(r["food"]),
                     num(r["amount"]), r.get("unit") or "", *[num(r[k]) for k in NUT], txt(r["note"]),
                     f"f{r['id']}", sort_key(r["time"])])
    return rows


def build_daily(days):
    rows = [list(DAILY_HEAD)]
    by_day = {}
    for r in days:
        by_day.setdefault(r["day"], {})["total" if r["is_total"] else r["meal"]] = r
    for day in sorted(by_day, reverse=True):
        info = by_day[day]
        ds = serial(day)

        def line(label, r):
            return [ds, label, r["items"], *[num(r[k]) for k in NUT]]

        for m in sorted(k for k in info if k not in ("total", 0)):
            rows.append(line(f"Meal {m} total", info[m]))
        if 0 in info:
            rows.append(line("Drinks total", info[0]))
        tot = info["total"]
        d_idx = len(rows) + 1
        rows.append(line("Day total", tot))
        t_idx = d_idx + 1
        tr = [ds, "Target", ""] + [""] * len(NUT)
        for i, k in enumerate(NUT):
            if k in TARGET_FOR and tot.get(TARGET_FOR[k]) is not None:
                tr[3 + i] = num(tot[TARGET_FOR[k]])
        rows.append(tr)
        pr, lr = [ds, "% of target", ""] + [""] * len(NUT), [ds, "Left (+) / over (-)", ""] + [""] * len(NUT)
        for i, k in enumerate(NUT):
            if k not in TARGET_FOR:
                continue
            L = chr(ord("A") + 3 + i)
            pr[3 + i] = f'=IF(OR({L}{t_idx}="",{L}{d_idx}=""),"",{L}{d_idx}/{L}{t_idx}*100)'
            lr[3 + i] = f'=IF({L}{t_idx}="","",{L}{t_idx}-{L}{d_idx})'
        rows.append(pr)
        rows.append(lr)
    return rows


def build_day_rows(entries, days):
    """What Daily summary shows for one day, for every day of the Log: entries by meal with a subtotal after each
    meal and after the drinks, then Day total, Target, % of target (text like "44%") and Left. Column Q = kind."""
    info = {}
    for r in days:
        info.setdefault(r["day"], {})["total" if r["is_total"] else r["meal"]] = r
    by_day = {}
    for e in entries:
        by_day.setdefault(e["day"], []).append(e)
    rows = [list(DAYROWS_HEAD)]
    for day in sorted(by_day, reverse=True):
        ds, es, inf = serial(day), by_day[day], info.get(day, {})

        def entry(e):
            return [ds, tfrac(e["time"]), "Drink" if e["is_drink"] else f"Meal {e['meal']}", txt(e["food"]), num(e["amount"]),
                    e.get("unit") or "", *[num(e[k]) for k in NUT], "entry"]

        def total(label, r, n, kind):
            return [ds, "", "", label, n, "item" if n == 1 else "items", *[num(r.get(k)) for k in NUT], kind]

        ordered = lambda xs: sorted(xs, key=lambda e: (sort_key(e["time"]), e["id"]))  # noqa: E731
        for m in sorted({e["meal"] for e in es if not e["is_drink"]}):
            grp = ordered([e for e in es if not e["is_drink"] and e["meal"] == m])
            rows += [entry(e) for e in grp]
            if m in inf:
                rows.append(total(f"Meal {m} total", inf[m], len(grp), "sub"))
        drinks = ordered([e for e in es if e["is_drink"]])
        if drinks:
            rows += [entry(e) for e in drinks]
            if 0 in inf:
                rows.append(total("Drinks total", inf[0], len(drinks), "sub"))
        tot = inf.get("total")
        if not tot:
            continue
        rows.append(total("Day total", tot, len(es), "day"))
        tr, pr, lr = ([ds, "", "", label, "", ""] + [""] * len(NUT) + [kind] for label, kind in
                      (("Target", "target"), ("% of target", "pct"), ("Left (+) / over (-)", "left")))
        for i, k in enumerate(NUT):
            t_ = tot.get(TARGET_FOR.get(k, ""))
            if t_ is None or not float(t_):
                continue
            v = float(tot[k] or 0)
            places = 0 if k in ("kcal", "sodium_mg") else 1
            tr[6 + i] = num(t_)
            pr[6 + i] = f"'{round(v / float(t_) * 100)}%"
            lr[6 + i] = round(float(t_) - v, places) if places else int(round(float(t_) - v))
        rows += [tr, pr, lr]
    return rows


def _calendar(first):
    """Every day from today back to `first`, newest first."""
    t0, out = today(), []
    d_ = t0
    while d_ >= first:
        out.append(d_)
        d_ -= dt.timedelta(days=1)
    return out


def build_health(data):
    days = {}
    for m in data["meas"]:
        r = days.setdefault(d(m["day"]), {})
        if m["type"] == "weight":
            r.setdefault("weight", []).append(float(m["value1"]))
        elif m["type"] == "bp":
            r[f"bp_{m['period']}"] = (m["value1"], m["value2"])
        elif m["type"] == "blood_sugar":
            r["glucose"] = m["value1"]
    for s in data["sleep"]:
        r = days.setdefault(d(s["wake_day"]), {})
        r["bed"], r["wake"], r["hours"] = ts_frac(s["bed_at"]), ts_frac(s["wake_at"]), s["hours"]
    weight = {k: sum(v["weight"]) / len(v["weight"]) for k, v in days.items() if v.get("weight")}
    rows = [list(HEALTH_GROUPS), list(HEALTH_HEAD)]
    for day in _calendar(min(days) if days else today()):
        r = days.get(day, {})
        week = [weight[x] for x in (day - dt.timedelta(days=i) for i in range(7)) if x in weight]
        bm, be = r.get("bp_morning", ("", "")), r.get("bp_evening", ("", ""))
        rows.append([serial(day), day.strftime("%a"), round(weight[day], 1) if day in weight else "",
                     round(sum(week) / len(week), 1) if week else "", num(bm[0]), num(bm[1]), num(be[0]), num(be[1]),
                     num(r.get("glucose")), r.get("bed", ""), r.get("wake", ""), num(r.get("hours"))])
    return rows


def habit_name(t):
    words = [w.upper() if len(w) == 1 else w for w in t.replace("_", " ").split()]
    return " ".join([words[0][:1].upper() + words[0][1:]] + words[1:]) if words else t


def build_habits(data):
    count = {}
    for e in data["events"]:
        if e["type"] not in ("wake", "bed"):
            key = (d(e["day"]), e["type"])
            count[key] = count.get(key, 0) + 1
    total = {}
    for (_, typ), n in count.items():
        total[typ] = total.get(typ, 0) + n
    habits = sorted(total, key=lambda k: (-total[k], k))
    t0 = today()
    last = lambda n, h: sum(1 for i in range(n) if count.get((t0 - dt.timedelta(days=i), h)))  # noqa: E731
    rows = [["Date", "Day"] + [habit_name(h) for h in habits],
            ["Last 7 days", ""] + [f"'{last(7, h)}/7" for h in habits],
            ["Last 30 days", ""] + [f"'{last(30, h)}/30" for h in habits]]
    first = min((k[0] for k in count), default=t0)
    for day in _calendar(first):
        rows.append([serial(day), day.strftime("%a")] +
                    [("✓" if count.get((day, h), 0) == 1 else f"✓ {count[(day, h)]}") if count.get((day, h)) else "" for h in habits])
    return rows


def build_stats(data, now):
    days = {}
    for r in data["days"]:
        if r["is_total"]:
            days[d(r["day"])] = r
    t0 = today()
    streak = 0
    day = t0 - dt.timedelta(days=1)
    while day in days and days[day]["target_protein"] is not None and days[day]["protein"] is not None \
            and float(days[day]["protein"]) >= float(days[day]["target_protein"]):
        streak += 1
        day -= dt.timedelta(days=1)
    last30 = [t0 - dt.timedelta(days=i) for i in range(1, 31)]
    logged = [x for x in last30 if x in days]
    within = [x for x in logged if days[x]["target_kcal"] and abs(float(days[x]["kcal"]) / float(days[x]["target_kcal"]) - 1) <= 0.1]
    ent = data["entries"]
    vals = [streak, len(within), len(logged), len(ent), sum(1 for e in ent if e["estimated"]),
            sum(1 for e in ent if e["needs_review"])]
    rows = [["Updated", now.strftime("%d.%m.%Y %H:%M")]]
    rows += [[k, v] for k, v in zip(STATS_ROWS, vals)]
    return rows


def build_all(data, now):
    return {"Log": build_log(data["entries"]), "Daily totals": build_daily(data["days"]),
            "Day rows": build_day_rows(data["entries"], data["days"]), "Health": build_health(data),
            "Habits": build_habits(data), "Stats": build_stats(data, now)}


# ----------------------------------------------------------------------------- Google Sheets (values only)

API = "https://sheets.googleapis.com/v4/spreadsheets"


def make_session(key_file):
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_file(key_file, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return AuthorizedSession(creds)


def _check(resp, what):
    if resp.status_code >= 300:
        raise RuntimeError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()


def tab_info(sess, sid):
    meta = _check(sess.get(f"{API}/{sid}", params={"fields": "sheets.properties(sheetId,title,gridProperties)"}), "read tabs")
    return {s["properties"]["title"]: s["properties"] for s in meta.get("sheets", [])}


def push_stamp(sess, sid, stamp, info):
    data = [{"range": f"'{t}'!{cell}", "majorDimension": "ROWS", "values": [[stamp]]} for t, cell in STAMP.items() if t in info]
    if data:
        _check(sess.post(f"{API}/{sid}/values:batchUpdate", json={"valueInputOption": "RAW", "data": data}), "write stamp")


def push(sess, sid, tabs, info):
    """Write all data tabs in one atomic call, grow tabs that are too small, clear what is left below and to the right."""
    missing = [t for t in tabs if t not in info]
    if missing:
        raise RuntimeError("tabs missing in the sheet (run sheets_layout.py): " + ", ".join(missing))
    grow = []
    for t_, rows in tabs.items():
        gp = info[t_]["gridProperties"]
        need_r, need_c = len(rows) + 50, max(max(len(r) for r in rows), 26) + 2
        if gp.get("rowCount", 0) < need_r or gp.get("columnCount", 0) < need_c:
            grow.append({"updateSheetProperties": {"properties": {"sheetId": info[t_]["sheetId"], "gridProperties": {
                "rowCount": max(gp.get("rowCount", 0), need_r), "columnCount": max(gp.get("columnCount", 0), need_c)}},
                "fields": "gridProperties.rowCount,gridProperties.columnCount"}})
    if grow:
        _check(sess.post(f"{API}/{sid}:batchUpdate", json={"requests": grow}), "grow tabs")
    clear = []
    for t_, rows in tabs.items():
        ncol = max(len(r) for r in rows)
        clear.append(f"'{t_}'!A{len(rows) + 1}:ZZ")
        clear.append(f"'{t_}'!{_col(ncol + 1)}1:ZZ")
    _check(sess.post(f"{API}/{sid}/values:batchClear", json={"ranges": clear}), "clear tails")
    data = [{"range": f"'{t_}'!A1", "majorDimension": "ROWS", "values": rows} for t_, rows in tabs.items()]
    _check(sess.post(f"{API}/{sid}/values:batchUpdate", json={"valueInputOption": "USER_ENTERED", "data": data}), "write values")


def _col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ----------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="write even if nothing changed")
    ap.add_argument("--dry-run", action="store_true", help="build the rows and print sizes, do not call Google")
    ap.add_argument("--check", action="store_true", help="check the database role and the sheet tabs, write nothing")
    a = ap.parse_args(argv)
    state = os.path.expanduser(os.environ.get("SYNC_STATE", "/opt/stacks/logger-v2/sheets"))
    os.makedirs(state, exist_ok=True)
    lock = open(os.path.join(state, "lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another run is active")
        return 0

    def status(result, msg):
        with open(os.path.join(state, "status.txt"), "w") as f:
            f.write(f"result={result}\ntime={dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\nmessage={msg}\n")

    try:
        now = dt.datetime.now(TZ)
        data = fetch()
        tabs = build_all(data, now)
        sizes = {k: len(v) - 1 for k, v in tabs.items()}
        # the stats tab holds the run time; leave it out of the change check
        digest = hashlib.sha256(json.dumps({k: v for k, v in tabs.items() if k != "Stats"} | {"stats": tabs["Stats"][1:]},
                                           sort_keys=True, default=str).encode()).hexdigest()
        if a.dry_run:
            print("rows:", sizes, "hash", digest[:12])
            return 0
        sid, key = os.environ.get("SHEET_ID"), os.environ.get("SA_KEY")
        if not sid or not key:
            sys.exit("SHEET_ID and SA_KEY must be set")
        sess = make_session(os.path.expanduser(key))
        info = tab_info(sess, sid)
        if a.check:
            miss = [t_ for t_ in tabs if t_ not in info]
            print("database ok:", sizes)
            print("sheet tabs:", ", ".join(info))
            print("ALL GOOD" if not miss else "MISSING TABS: " + ", ".join(miss))
            return 1 if miss else 0
        hf = os.path.join(state, "last_hash.txt")
        last = open(hf).read().strip() if os.path.exists(hf) else ""
        stamp = "Updated " + now.strftime("%d.%m.%Y %H:%M")
        if digest == last and not a.force:
            push_stamp(sess, sid, stamp, info)
            status("OK", "no change; stamp written")
            return 0
        push(sess, sid, tabs, info)
        push_stamp(sess, sid, stamp, info)
        open(hf, "w").write(digest)
        status("OK", f"wrote {sizes}")
        print("wrote", sizes)
        return 0
    except Exception as e:  # noqa: BLE001
        status("FAIL", str(e)[:300])
        print("FAILED:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
