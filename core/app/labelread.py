"""A food label read from a photo (31.10.2026): Claude reads the nutrition table and returns the values per 100 g (or
100 ml); the web app shows them for the owner to check and correct, and only the owner's Add puts the food in the
catalogue (webfood.label_add, through the logger's own new-food checks). The photo is sent once and not kept."""
from typing import Optional

from . import logbook
from .ai.provider import ProviderError
from .db import connect

MODEL = "claude-sonnet-5-5"  # reading small print in a photo: the stronger model (a few tenths of a cent per photo)
TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_BYTES = 8 * 1024 * 1024
NUM = {"type": ["number", "null"]}
FIELDS = ("kcal", "protein", "fat", "sat_fat", "carbs", "sugar_total", "fibre", "salt_g")
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["readable", "name", "brand", "basis", *FIELDS, "note"],
          "properties": {"readable": {"type": "boolean"}, "name": {"type": ["string", "null"]}, "brand": {"type": ["string", "null"]},
                         "basis": {"type": "string", "enum": ["g", "ml"]}, **{k: NUM for k in FIELDS}, "note": {"type": ["string", "null"]}}}
SYSTEM = """You read nutrition labels from photos of food packages for a personal food log.
Return the values per 100 g, or per 100 ml for drinks (basis "ml"), as printed in the "per 100 g / 100 ml" column.
If the label only gives values per serving and states the serving size in g or ml, convert them to per 100 and say so in note.
Energy in kcal; if only kJ is printed, divide by 4.184. Salt in grams as printed (salt_g); if only sodium is printed, salt = sodium x 2.5.
"of which saturates" is sat_fat, "of which sugars" is sugar_total, fibre as printed. Use null for a value that is not on the label.
name: the product's name if it is visible (as printed, in its language), else null; brand likewise.
readable: false when no nutrition table can be read (blurred, cut off, not a label); then all values are null and note says why.
note: one short sentence for anything the owner should check (a conversion, a hard-to-read number), else null."""


def read(photo: bytes, media_type: str) -> dict:
    """{"ok": True, "values": {...per 100...}, "name", "brand", "basis", "note"} or {"ok": False, "reason"}."""
    if media_type not in TYPES:
        return {"ok": False, "reason": "Send a photo (JPEG or PNG)."}
    if not photo or len(photo) > MAX_BYTES:
        return {"ok": False, "reason": "The photo is empty or too large (at most 8 MB)."}
    try:
        out, usage = logbook.provider().image_json(SYSTEM, "Read the nutrition values on this label.", [(media_type, photo)], SCHEMA, MODEL)
    except ProviderError as e:
        return {"ok": False, "reason": f"Claude could not read it ({e}). Try again, or type the values on the Food page."}
    with connect() as conn, conn.cursor() as cur:  # the cost log, like the other AI calls (no photo, no values)
        logbook._audit(cur, "label_photo", "read" if out.get("readable") else "unreadable",
                       {"ai": [{"model": usage.model, "usd": round(usage.usd, 6)}]}, logbook.WEB)
    if not out.get("readable") or any(out.get(k) is None for k in ("kcal", "protein", "fat", "carbs")):
        return {"ok": False, "reason": (out.get("note") or "No nutrition table could be read.") + " Try a sharper photo of the table, straight on."}
    values = {k: _round(out.get(k)) for k in FIELDS}
    return {"ok": True, "values": values, "name": _text(out.get("name")), "brand": _text(out.get("brand")),
            "basis": "ml" if out.get("basis") == "ml" else "g", "note": _text(out.get("note"))}


def _round(v) -> Optional[float]:
    return None if v is None else round(float(v), 2)


def _text(v) -> Optional[str]:
    return " ".join(str(v).split())[:120] or None if v else None
