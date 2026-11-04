"""Habits to do and habits to avoid (04.11.2026): a habit to avoid (a soft drink, say) is still ticked when it happens, but it does not
count as "done" (Today's "n of N done", the Habits page, the Week page, the "habits still open" nudge); its calendar shows
the days with it in amber and counts the days without. Kept in ui_settings under the key "habit_aim" ({"avoid": [names]});
until the owner sets one, the habits of the group "behaviour" are the ones to avoid. Set on the Habits page (Edit)."""
from psycopg.types.json import Jsonb

KEY = "habit_aim"


def avoid(cur) -> set:
    cur.execute("SELECT value FROM ui_settings WHERE key = %s", (KEY,))
    r = cur.fetchone()
    if r and isinstance(r["value"], dict):
        return set(r["value"].get("avoid") or [])
    cur.execute("SELECT name FROM log_types WHERE kind = 'event' AND category = 'behaviour'")
    return {x["name"] for x in cur.fetchall()}


def set_aim(cur, name: str, to_avoid: bool) -> bool:
    """True when it changed."""
    now = avoid(cur)
    new = (now | {name}) if to_avoid else (now - {name})
    if new == now:
        return False
    cur.execute("""INSERT INTO ui_settings (key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (KEY, Jsonb({"avoid": sorted(new)})))
    return True
