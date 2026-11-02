"""Watch data from Apple Health (02.11.2026, Gym step d; plan and the owner's answers in the doc, section "Step (d), Activity").

POST /watch/health on the public log host, sent by the iPhone app Health Auto Export (REST API automations, JSON, export
version 2) with the header "Authorization: Bearer <WATCH_TOKEN>". The owner types WATCH_TOKEN into /opt/stacks/logger-v2/.env
(compose passes it on); without it the address answers 503. Each run re-sends yesterday and today, so readings are upserted on
(metric, time, source) and workouts on Apple's id: totals are overwritten, never added up. Energy is kept in kcal and distance in
km whatever the app sends. Only counts go to the audit log, never values."""
import hmac
import json
import logging
import os
from datetime import datetime

from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import logbook, rules
from .db import connect

_LOG = logging.getLogger(__name__)
PATH = "/watch/health"
MAX_BYTES = 10_000_000
METRICS = ("step_count", "active_energy", "apple_exercise_time", "resting_heart_rate", "heart_rate_variability",
           "walking_running_distance", "flights_climbed", "sleep_analysis", "heart_rate")
SLEEP_KEYS = ("totalSleep", "asleep", "core", "deep", "rem", "awake", "inBed", "sleepStart", "sleepEnd", "inBedStart", "inBedEnd")


def token() -> str:
    t = os.environ.get("WATCH_TOKEN", "").strip()
    return t if len(t) >= 24 else ""


def when(s) -> datetime:
    """The app's times: "2026-11-02 14:30:00 +0100"; ISO times too. Without a zone the time is Europe/Berlin."""
    s = str(s or "").strip()
    for f in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            t = datetime.strptime(s, f)
            break
        except ValueError:
            t = None
    t = t or datetime.fromisoformat(s.replace("Z", "+00:00"))  # ValueError when it is no time at all
    return t if t.tzinfo else t.replace(tzinfo=rules.BERLIN)


def _f(v):
    if isinstance(v, dict):
        v = v.get("qty")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) < 1e9 else None


def _unit(v, default="") -> str:
    return str((v.get("units") if isinstance(v, dict) else None) or default).strip()


def kcal(qty, unit: str):
    return None if qty is None else qty / 4.184 if unit.lower() == "kj" else qty


def km(qty, unit: str):
    u = unit.lower()
    return None if qty is None else qty / 1000 if u == "m" else qty * 1.609344 if u in ("mi", "mile", "miles") else qty


def _metric_rows(m: dict) -> list[tuple]:
    """(metric, at, value, unit, source, extra) for each reading of one metric; a reading that cannot be read is left out."""
    name, unit = str(m.get("name", "")), str(m.get("units") or "")
    out = []
    for item in m.get("data") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")[:200]
        try:
            if name == "sleep_analysis" and any(k in item for k in ("totalSleep", "asleep", "deep", "core", "rem")):
                extra = {k: item[k] for k in SLEEP_KEYS if k in item}
                total = _f(item.get("totalSleep"))
                if total is None:
                    total = sum(_f(item.get(k)) or 0 for k in ("asleep", "core", "deep", "rem"))
                out.append((name, when(item.get("sleepStart") or item.get("inBedStart") or item.get("date")), total, "hr", source,
                            {"kind": "night", **extra}))
            elif name == "sleep_analysis":  # one segment of a night: its stage and length in hours
                start = when(item.get("startDate") or item.get("start") or item.get("date"))
                end = item.get("endDate") or item.get("end")
                out.append((name, start, _f(item.get("qty")), "hr", source,
                            {"kind": "segment", "stage": str(item.get("value") or ""), "end": when(end).isoformat() if end else None}))
            elif name == "heart_rate":
                hr = {k.lower(): _f(item.get(k)) for k in ("Min", "Avg", "Max")}
                out.append((name, when(item.get("date")), hr["avg"], unit or "count/min", source, hr))
            elif name == "active_energy":
                out.append((name, when(item.get("date")), kcal(_f(item.get("qty")), unit), "kcal", source, {}))
            elif name == "walking_running_distance":
                out.append((name, when(item.get("date")), km(_f(item.get("qty")), unit), "km", source, {}))
            else:
                out.append((name, when(item.get("date")), _f(item.get("qty")), unit, source, {}))
        except (ValueError, TypeError):
            continue
    return out


def _workout_row(w: dict) -> tuple | None:
    try:
        hr = w.get("heartRate") if isinstance(w.get("heartRate"), dict) else {}
        energy = w.get("activeEnergyBurned") or w.get("activeEnergy")
        dist = w.get("distance")
        return (str(w["id"])[:100], str(w.get("name") or "Workout")[:100], when(w["start"]), when(w["end"]),
                kcal(_f(energy), _unit(energy, "kcal")), km(_f(dist), _unit(dist, "km")),
                _f(hr.get("avg")) if hr else _f(w.get("avgHeartRate")), _f(hr.get("max")) if hr else _f(w.get("maxHeartRate")),
                str(w.get("source") or "")[:200])
    except (KeyError, ValueError, TypeError):
        return None


def save(data: dict) -> dict:
    """Upsert one request's readings and workouts. Metrics the Activity tab does not use are skipped."""
    rows, skipped = [], 0
    for m in data.get("metrics") or []:
        if isinstance(m, dict) and m.get("name") in METRICS:
            rows += _metric_rows(m)
        else:
            skipped += 1
    works = [r for r in (_workout_row(w) for w in data.get("workouts") or [] if isinstance(w, dict)) if r]
    with connect() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute("""INSERT INTO watch_metrics (metric, at, value, unit, source, extra) VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (metric, at, source) DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit,
                           extra = EXCLUDED.extra, received_at = now()""", (*r[:5], Jsonb(r[5])))
        for r in works:
            cur.execute("""INSERT INTO watch_workouts (apple_id, name, start_at, end_at, kcal, km, hr_avg, hr_max, source)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (apple_id) DO UPDATE SET name = EXCLUDED.name, start_at = EXCLUDED.start_at, end_at = EXCLUDED.end_at,
                           kcal = EXCLUDED.kcal, km = EXCLUDED.km, hr_avg = EXCLUDED.hr_avg, hr_max = EXCLUDED.hr_max,
                           source = EXCLUDED.source, received_at = now()""", r)
        out = {"metrics": len(rows), "workouts": len(works), "skipped": skipped}
        logbook._audit(cur, "watch", "saved", out, "health-auto-export")
    return out


def add_routes(mcp) -> None:
    @mcp.custom_route(PATH, methods=["POST"])
    async def receive(request: Request) -> JSONResponse:
        want = token()
        if not want:
            return JSONResponse({"error": "not set up"}, status_code=503)
        got = request.headers.get("authorization", "")
        if not hmac.compare_digest(got.encode(), f"Bearer {want}".encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if int(request.headers.get("content-length") or 0) > MAX_BYTES:
            return JSONResponse({"error": "too large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BYTES:
                return JSONResponse({"error": "too large"}, status_code=413)
        try:
            data = json.loads(body)["data"]
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "expected JSON with a data object"}, status_code=400)
        try:
            out = await run_in_threadpool(save, data)
        except Exception as e:  # noqa: BLE001 - the app retries on its next run
            _LOG.error("watch data not saved: %s", type(e).__name__)
            return JSONResponse({"error": "not saved"}, status_code=500)
        return JSONResponse({"ok": True, **out})
