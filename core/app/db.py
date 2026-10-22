import psycopg
from psycopg.rows import dict_row

from .config import DB_HOST, DB_NAME, DB_PORT, env


def connect(owner: bool = False) -> psycopg.Connection:
    """core_rw for the running service; app_owner only for migrations."""
    user, pw = ("app_owner", env("APP_OWNER_PASSWORD")) if owner else ("core_rw", env("CORE_RW_PASSWORD"))
    return psycopg.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=user, password=pw,
                           row_factory=dict_row, connect_timeout=5)
