"""Apply core/migrations/*.sql once each, in file-name order, as app_owner."""
import pathlib

from . import usda
from .db import connect

DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def run() -> list[str]:
    done: list[str] = []
    if not list(DIR.glob("*.sql")):
        raise RuntimeError(f"no migration files found in {DIR}")
    with connect(owner=True) as conn:
        conn.autocommit = True
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        have = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for f in sorted(DIR.glob("*.sql")):
            if f.name in have:
                continue
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
            done.append(f.name)
        conn.autocommit = False
        if usda.load_if_empty(conn):  # the local USDA copy (migration 009), once
            done.append("usda data")
        conn.commit()
    return done
