"""What the owner chose to show on the Today page (Settings > Today page), kept in ui_settings under the key "today":
{"hidden": ["habit:meds", "health:sugar", "usual:3", "review"], "order": {"habit": ["shoonya", "meds"], "health": [...], "usual": ["3", "5"]}}.
Hiding is only about the Today page: a hidden habit stays on the Habits page, in the Sheet and in every record."""
from psycopg.types.json import Jsonb

from .db import connect

KEY = "today"
HEALTH_ROWS = ("weight", "bp_morning", "bp_evening", "sugar", "wake")  # the Health card's rows (todaypage.HEALTH_ROWS keys)


def load(cur) -> dict:
    """-> {"hidden": set of "kind:id", "order": {kind: [ids]}} (empty when nothing was chosen)."""
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (KEY,))
    row = cur.fetchone()
    value = row["value"] if row else {}
    order = value.get("order") if isinstance(value.get("order"), dict) else {}
    return {"hidden": {str(x) for x in value.get("hidden", [])}, "order": {k: [str(i) for i in v] for k, v in order.items() if isinstance(v, list)}}


def ordered(items: list, key, order: list) -> list:
    """Items in the chosen order; those not in it (new ones) keep their own order after them."""
    pos = {k: i for i, k in enumerate(order)}
    return sorted(items, key=lambda x: pos.get(str(key(x)), len(pos)))  # sorted is stable


def shown(kind: str, ident, prefs: dict) -> bool:
    return f"{kind}:{ident}" not in prefs["hidden"]


def save(hidden: list[str], order: dict) -> None:
    with connect() as c:
        c.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                     ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                  (KEY, Jsonb({"hidden": sorted(hidden), "order": order})))


def reset() -> None:
    with connect() as c:
        c.execute("DELETE FROM ui_settings WHERE key = %s", (KEY,))
