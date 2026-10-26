"""Turn USDA's FoodData Central SR Legacy download (2018, public domain) into the two compact files the core loads at
startup: core/data/usda_sr_legacy_foods.csv.gz (values per 100 g) and core/data/usda_sr_legacy_portions.csv.gz
(portion weights). The nutrient ids are the ones v1's imports/usda/build.py used.

    python3 tools/import/usda_build.py data/usda/FoodData_Central_sr_legacy_food_csv_2018-04.zip
"""
import csv
import gzip
import io
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
NUTRIENTS = {1008: "kcal", 1003: "protein", 1004: "fat", 1258: "sat_fat", 1005: "carbs", 1079: "fibre", 2000: "sugar_total",
             1093: "sodium_mg", 1057: "caffeine_mg"}
COLS = ["fdc_id", "description", "category", *NUTRIENTS.values()]


def rows(z: zipfile.ZipFile, name: str):
    path = next(n for n in z.namelist() if n.endswith("/" + name))
    yield from csv.DictReader(io.TextIOWrapper(z.open(path), encoding="utf-8"))


def main(src: str):
    z = zipfile.ZipFile(src)
    cats = {r["id"]: r["description"] for r in rows(z, "food_category.csv")}
    foods = {r["fdc_id"]: {"fdc_id": r["fdc_id"], "description": r["description"], "category": cats.get(r["food_category_id"], "")}
             for r in rows(z, "food.csv")}
    for r in rows(z, "food_nutrient.csv"):
        key = NUTRIENTS.get(int(r["nutrient_id"]))
        if key and r["fdc_id"] in foods and r["amount"] != "":
            foods[r["fdc_id"]][key] = r["amount"]
    keep = [f for f in foods.values() if all(f.get(k) not in (None, "") for k in ("kcal", "protein", "fat", "carbs"))]
    out = ROOT / "core" / "data"
    out.mkdir(exist_ok=True)
    with gzip.open(out / "usda_sr_legacy_foods.csv.gz", "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        for food in sorted(keep, key=lambda x: int(x["fdc_id"])):
            w.writerow({c: food.get(c, "") for c in COLS})
    ids = {f["fdc_id"] for f in keep}
    n_portions = 0
    with gzip.open(out / "usda_sr_legacy_portions.csv.gz", "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fdc_id", "amount", "unit", "grams"])
        for r in rows(z, "food_portion.csv"):
            unit = (r["modifier"] or r["portion_description"] or "").strip()
            if r["fdc_id"] in ids and unit and r["gram_weight"] and float(r["gram_weight"]) > 0 and float(r["amount"] or 0) > 0:
                w.writerow([r["fdc_id"], r["amount"], unit, r["gram_weight"]])
                n_portions += 1
    print(f"{len(foods)} foods read, {len(keep)} kept (kcal, protein, fat and carbs known), {n_portions} portion weights")
    for f in out.glob("usda_sr_legacy_*.csv.gz"):
        print(f"{f.name}: {f.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main(sys.argv[1])