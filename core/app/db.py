"""The database connections. Since 02.11.2026 (core item 9) they come from a small pool: opening a connection costs about
45 ms on the VM and a page needs several, so a few stay open and are lent out. `with connect() as conn` works as before:
the block's work is committed at its end (rolled back after an error), and the connection goes back to the pool instead
of being closed. A connection that broke (the database restarted) is checked before it is lent and replaced."""
import atexit
import threading

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import DB_HOST, DB_NAME, DB_PORT, env

POOL_MAX = 8  # connections per role; the threads of one process share them (Postgres allows 100, about 6 are in use)
_pools: dict[bool, ConnectionPool] = {}
_lock = threading.Lock()


def _pool(owner: bool) -> ConnectionPool:
    p = _pools.get(owner)
    if p is None:
        with _lock:
            p = _pools.get(owner)
            if p is None:
                user, pw = ("app_owner", env("APP_OWNER_PASSWORD")) if owner else ("core_rw", env("CORE_RW_PASSWORD"))
                p = ConnectionPool(kwargs={"host": DB_HOST, "port": DB_PORT, "dbname": DB_NAME, "user": user, "password": pw,
                                           "row_factory": dict_row, "connect_timeout": 5},
                                   min_size=1, max_size=POOL_MAX, max_idle=300, timeout=10,
                                   check=ConnectionPool.check_connection, name=user, open=True)
                _pools[owner] = p
    return p


def connect(owner: bool = False):
    """core_rw for the running service; app_owner only for migrations (and the tests). Use it as `with connect() as conn`."""
    return _pool(owner).connection()


@atexit.register
def _close() -> None:
    """A short process (the cron jobs run `python -m app.held` and the like) closes its connections on the way out."""
    for p in list(_pools.values()):
        p.close(timeout=2)
