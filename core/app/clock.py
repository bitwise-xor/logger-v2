"""Times for new entries: v1's date reader plus the v2 rules on top of it."""
import re
from datetime import datetime, timedelta
from typing import Optional

from .when import parse_when


def read_when(text: Optional[str], now: datetime) -> tuple[datetime, bool, Optional[str]]:
    """-> (time, date_only, problem). Empty means now. One v2 rule on top of v1: "yesterday" with a time before
    05:00, said after 05:00, means the night that just ended ("slept yesterday at 4:00, woke at 12:20")."""
    if not text or not text.strip():
        return now, False, None
    w = parse_when(text, now)
    if not w:
        return now, False, f"time '{text}' not readable"
    ts, date_only = w
    if (not date_only and re.search(r"\byesterday\b|\bgestern\b", text.lower()) and ts.hour < 5 and now.hour >= 5
            and ts.date() == now.date() - timedelta(days=1)):
        ts += timedelta(days=1)
    return ts, date_only, None
