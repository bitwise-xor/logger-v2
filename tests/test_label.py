"""A food from a label photo (31.10.2026): Claude reads the values (a fake here), the page shows them to check, and only
the owner's Add puts the food in the catalogue, through the logger's own new-food checks, with its scanned barcode."""
import pytest

from app import labelread, logbook
from app.ai.provider import ProviderError, Usage
from conftest import owner_sql
from test_log import fake  # noqa: F401 - fixture reused
from test_web import ORIGIN, act, web  # noqa: F401
from test_webtoday import no_targets  # noqa: F401 - no_targets: clean targets and usual meals

GRANOLA = {"readable": True, "name": "Crunchy Granola", "brand": "Testmarke", "basis": "g", "kcal": 452, "protein": 9.5, "fat": 17.8,
           "sat_fat": 3.1, "carbs": 61.2, "sugar_total": 19.0, "fibre": 6.4, "salt_g": 0.3, "note": None}


class Vision:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer or GRANOLA, fail, []

    def image_json(self, system, user, images, schema, model, max_tokens=2000):
        self.calls.append((model, images[0][0], len(images[0][1])))
        if self.fail:
            raise ProviderError("overloaded")
        return dict(self.answer), Usage(model, 1600, 120)


@pytest.fixture
def vision():
    v = Vision()
    logbook.PROVIDER = v
    yield v
    logbook.PROVIDER = None


def foods():
    return owner_sql("SELECT name, basis, kcal, protein, sodium_mg, barcode, source, source_note FROM foods ORDER BY id")


# ---- reading -----------------------------------------------------------------------------------------

def test_a_label_is_read_into_values_per_100_and_the_cost_is_logged(vision):
    out = labelread.read(b"\xff\xd8 a jpeg", "image/jpeg")
    assert out["ok"] and out["name"] == "Crunchy Granola" and out["brand"] == "Testmarke" and out["basis"] == "g"
    assert out["values"]["kcal"] == 452 and out["values"]["salt_g"] == 0.3
    assert vision.calls == [(labelread.MODEL, "image/jpeg", 9)] and foods() == []  # nothing saved by reading
    assert owner_sql("SELECT outcome FROM audit_log WHERE tool = 'label_photo'")[0]["outcome"] == "read"


def test_an_unreadable_photo_a_wrong_file_and_a_failed_call_say_why(vision):
    assert "JPEG or PNG" in labelread.read(b"x", "application/pdf")["reason"]
    vision.answer = {**{k: None for k in GRANOLA}, "readable": False, "basis": "g", "note": "The table is cut off."}
    assert "The table is cut off." in labelread.read(b"x", "image/png")["reason"]
    vision.fail = True
    assert "could not read it" in labelread.read(b"x", "image/png")["reason"]


def test_the_route_needs_the_csrf_header_and_returns_the_values(web, vision):
    c, csrf = web
    files = {"photo": ("label.jpg", b"\xff\xd8 a jpeg", "image/jpeg")}
    assert c.post("/web/food/label", files=files, headers=ORIGIN).status_code == 403
    j = c.post("/web/food/label", files=files, headers={**ORIGIN, "X-CSRF": csrf}).json()
    assert j["ok"] and j["values"]["protein"] == 9.5


# ---- adding ----------------------------------------------------------------------------------------

def test_the_checked_values_add_the_food_with_its_barcode(web, fake):
    page = act(web, "/web/food/labeladd", name=" Crunchy  Granola (Testmarke) ", basis="g", kcal="452", protein="9,5", fat="17.8", carbs="61.2",
               sat_fat="3.1", sugar_total="", fibre="6.4", salt_g="0.3", barcode="4006040123456").text
    assert "Added Crunchy Granola (Testmarke) to your catalogue." in page
    (f,) = foods()
    assert (f["name"], f["basis"], float(f["kcal"]), float(f["protein"]), float(f["sodium_mg"]), f["barcode"], f["source"]) == \
        ("Crunchy Granola (Testmarke)", "g", 452.0, 9.5, 120.0, "4006040123456", "label")
    assert "label photo read by Claude" in f["source_note"]
    assert web[0].get("/web/foods/barcode.json?code=4006040123456").json()["food"]["name"] == "Crunchy Granola (Testmarke)"


def test_an_amount_logs_it_straight_away(web, fake):
    page = act(web, "/web/food/labeladd", name="Test granola", basis="g", kcal="452", protein="9.5", fat="17.8", carbs="61.2",
               amount="45", time="08:30", key="lbl-1").text
    assert "Added Test granola to your catalogue." in page and 'action="/web/undo"' in page
    assert [float(r["amount"]) for r in owner_sql("SELECT amount FROM food_entries")] == [45.0] and fake.calls == []


def test_missing_or_impossible_values_add_nothing(web, fake):
    assert "Give the food a name" in act(web, "/web/food/labeladd", name="", kcal="1", protein="1", fat="1", carbs="1").text
    assert "Protein per 100 g is missing" in act(web, "/web/food/labeladd", name="Test x", kcal="100", protein="", fat="1", carbs="1").text
    page = act(web, "/web/food/labeladd", name="Test impossible", kcal="100", protein="90", fat="90", carbs="90").text  # 270 g in 100 g
    assert "Added" not in page and foods() == []


def test_today_has_the_photo_button_and_the_form_to_check(web):
    page = web[0].get("/web/").text
    assert "data-label-file" in page and 'capture="environment"' in page and 'action="/web/food/labeladd"' in page and "data-label-form hidden" in page
