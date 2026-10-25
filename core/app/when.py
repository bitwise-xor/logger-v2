"""Reading times and dates from the user's words. Copied unchanged from logger-app connector/server.py (v1)."""
import re
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

_WEEKDAYS = {
    "monday": 0, "mon": 0, "montag": 0, "tuesday": 1, "tue": 1, "dienstag": 1,
    "wednesday": 2, "wed": 2, "mittwoch": 2, "thursday": 3, "thu": 3, "donnerstag": 3,
    "friday": 4, "fri": 4, "freitag": 4, "saturday": 5, "sat": 5, "samstag": 5, "sonnabend": 5,
    "sunday": 6, "sun": 6, "sonntag": 6,
}


_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})(?:[t ](\d{1,2}):(\d{2}))?")


_DMY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?!\d)")


_WD = re.compile(r"\b(?:(last|letzten?|vergangenen?)\s+)?(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")\b")


def parse_when(text: Optional[str], now: datetime) -> Optional[tuple[datetime, bool]]:
    """Resolve the user's wording to a Europe/Berlin datetime. Returns (datetime, date_only) or None."""
    if not text or not text.strip():
        return None
    s = text.strip().lower()
    now = now.astimezone(BERLIN)
    today = now.date()
    day: Optional[date] = None
    hh = mm = None

    def cut(m):
        return s[: m.start()] + " " + s[m.end():]

    try:
        m = _ISO.search(s)
        if m:
            day = date(int(m[1]), int(m[2]), int(m[3]))
            if m[4] is not None:
                hh, mm = int(m[4]), int(m[5])
            s = cut(m)
        if day is None:
            m = _DMY.search(s)
            if m:
                ys = m[3]
                y = now.year if ys is None else (int(ys) + 2000 if len(ys) == 2 else int(ys))
                day = date(y, int(m[2]), int(m[1]))
                if ys is None and day > today + timedelta(days=1):
                    day = date(y - 1, int(m[2]), int(m[1]))
                s = cut(m)
        if day is None:
            m = re.search(r"day before yesterday|vorgestern", s)
            if m:
                day, s = today - timedelta(days=2), cut(m)
        if day is None:
            m = re.search(r"\byesterday\b|\bgestern\b", s)
            if m:
                day, s = today - timedelta(days=1), cut(m)
        if day is None:
            m = re.search(r"\b(today|heute|tonight)\b", s)
            if m:
                day, s = today, cut(m)
        if day is None:
            m = re.search(r"\b(\d{1,3})\s+days?\s+ago\b|\bvor\s+(\d{1,3})\s+tag(?:en|e)?\b", s)
            if m:
                day, s = today - timedelta(days=int(m[1] or m[2])), cut(m)
        if day is None:
            m = _WD.search(s)
            if m:
                delta = (today.weekday() - _WEEKDAYS[m[2]]) % 7
                if delta == 0 and m[1]:
                    delta = 7
                day, s = today - timedelta(days=delta), cut(m)
    except ValueError:
        return None

    if hh is None:
        m = re.search(r"\b(\d{1,2}):(\d{2})(?:\s*(am|pm))?\b", s)
        if m:
            hh, mm, ap = int(m[1]), int(m[2]), m[3]
        else:
            m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", s)
            if m:
                hh, mm, ap = int(m[1]), 0, m[2]
            else:
                m = re.search(r"\b(\d{1,2})\s*uhr\b", s)
                hh, mm, ap = (int(m[1]), 0, None) if m else (None, None, None)
        if hh is not None:
            if ap == "pm" and hh < 12:
                hh += 12
            elif ap == "am" and hh == 12:
                hh = 0
    if day is None and hh is None:
        return None
    if hh is not None and not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None

    date_only = hh is None
    t = time(12, 0) if date_only else time(hh, mm)
    if day is None:  # bare time of day: today, or yesterday if that would lie in the future
        day = today
        if datetime.combine(day, t, tzinfo=BERLIN) > now + timedelta(hours=1):
            day = today - timedelta(days=1)
    return datetime.combine(day, t, tzinfo=BERLIN), date_only


def _day_of(ts: datetime, type_name: str) -> date:
    """Logical day: local time minus 5 h (before 05:00 counts as the previous day); wake-ups use their own calendar date."""
    local = ts.astimezone(BERLIN)
    return local.date() if type_name == "wake" else (local - timedelta(hours=5)).date()
