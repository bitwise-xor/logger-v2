"""USDA FoodData Central SR Legacy, a local copy: loading it at startup and searching it by name."""
import gzip
import pathlib
import re

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
FOODS, PORTIONS = DATA / "usda_sr_legacy_foods.csv.gz", DATA / "usda_sr_legacy_portions.csv.gz"


def load_if_empty(conn) -> int:
    """Fill usda_foods and usda_portions from core/data once (as app_owner, after the migrations). -> foods loaded."""
    if conn.execute("SELECT count(*) AS n FROM usda_foods").fetchone()["n"] or not FOODS.exists():
        return 0
    with conn.transaction():
        for table, path in (("usda_foods", FOODS), ("usda_portions", PORTIONS)):
            with gzip.open(path, "rt", encoding="utf-8") as f, conn.cursor().copy(
                    f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')") as cp:
                while chunk := f.read(1 << 16):
                    cp.write(chunk)
    return conn.execute("SELECT count(*) AS n FROM usda_foods").fetchone()["n"]


def search(cur, words: str, limit: int = 10) -> list[dict]:
    """Best matches for the words: every word first, then any word; shorter (plainer) descriptions first."""
    for op in ("&", "|"):
        cur.execute(f"""SELECT f.*, ts_rank(to_tsvector('english', description), q) AS rank
                        FROM usda_foods f, to_tsquery('english', %s) q
                        WHERE to_tsvector('english', description) @@ q
                        ORDER BY rank DESC, length(description) LIMIT %s""",
                    (f" {op} ".join(w for w in _words(words)) or "x", limit))
        rows = cur.fetchall()
        if rows:
            return rows
    return []


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 1]


def portions(cur, fdc_id: int) -> list[dict]:
    cur.execute("SELECT amount, unit, grams FROM usda_portions WHERE fdc_id = %s", (fdc_id,))
    return cur.fetchall()