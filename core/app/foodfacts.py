"""Open Food Facts (world.openfoodfacts.org, open data, no key): a product by barcode, and a search by name.
Values per 100 g come from the nutriments; a missing kcal, protein, fat or carbs makes a product unusable."""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

UA = {"User-Agent": "logger-v2/0.1 (personal food logger)"}
TIMEOUT = 8


class FoodFactsError(Exception):
    pass


def _get(url: str) -> dict:
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            if attempt == 2 or e.code not in (429, 500, 502, 503, 504):
                raise FoodFactsError(f"Open Food Facts answered {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise FoodFactsError(f"Open Food Facts is not reachable ({type(e).__name__})") from e
    return {}


def product(code: str) -> Optional[dict]:
    fields = "code,product_name,product_name_en,brands,quantity,nutriments"
    b = _get(f"https://world.openfoodfacts.org/api/v2/product/{urllib.parse.quote(code)}.json?fields={fields}")
    return b.get("product") if b.get("status") == 1 else None


def search(q: str, n: int = 8) -> list[dict]:
    b = _get("https://search.openfoodfacts.org/search?" + urllib.parse.urlencode(
        {"q": q, "page_size": n, "fields": "code,product_name,product_name_en,brands,nutriments,countries_tags"}))
    return b.get("hits") or []


def values(n: dict) -> Optional[dict]:
    """Per 100 g in the catalogue's units, or None when a main value is missing."""
    def num(k):
        v = n.get(k)
        try:
            return round(float(v), 3) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    kcal = num("energy-kcal_100g")
    if kcal is None and num("energy_100g") is not None:
        kcal = round(num("energy_100g") / 4.184, 1)
    out = dict(kcal=kcal, protein=num("proteins_100g"), fat=num("fat_100g"), carbs=num("carbohydrates_100g"),
               sat_fat=num("saturated-fat_100g"), fibre=num("fiber_100g"), sugar_total=num("sugars_100g"))
    if num("sodium_100g") is not None:
        out["sodium_mg"] = round(num("sodium_100g") * 1000, 1)
    elif num("salt_100g") is not None:
        out["sodium_mg"] = round(num("salt_100g") * 400, 1)
    if any(out[k] is None for k in ("kcal", "protein", "fat", "carbs")):
        return None
    return {k: v for k, v in out.items() if v is not None}