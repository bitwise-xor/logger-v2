"""Pure rules (no database): time parsing and the plausibility checks. Ported from the v1 connector's behaviour."""
import math
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class BadInput(ValueError):
    pass


def parse_at(at: str | None, now: datetime) -> tuple[datetime, bool]:
    """The AI app turns the user's words into a local date and time and sends ISO text. Returns (moment, time_known).
    None -> now, time not known. Date only -> noon, time not known. Without a zone the time is Europe/Berlin."""
    now = now.astimezone(BERLIN)
    if at is None or not at.strip():
        return now, False
    s = at.strip()
    try:
        if _DATE.match(s):
            d = date.fromisoformat(s)
            return datetime.combine(d, time(12, 0), tzinfo=BERLIN), False
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise BadInput(f"'{at}' is not a date and time. Send ISO text like 2026-10-22T08:30 (Europe/Berlin).")
    return (dt.replace(tzinfo=BERLIN) if dt.tzinfo is None else dt.astimezone(BERLIN)), True


def fmt(dt: datetime, known: bool = True) -> str:
    dt = dt.astimezone(BERLIN)
    return dt.strftime("%d.%m. %H:%M") if known else dt.strftime("%d.%m.") + " (no time)"


def check_number(x: float, name: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0:
        raise BadInput(f"{name} must be a number above 0")
    return float(x)


def is_future(dt: datetime, now: datetime) -> bool:
    return dt > now + timedelta(minutes=5)
