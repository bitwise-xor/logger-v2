"""Body measurements typed as "<measure> <number>" pairs, read without the AI (03.11.2026, body measurements T1, spec section
5.2): "waist 123.5", "waist 123,5 hips 128 cm", "measured: waist 49 in". A line made only of such pairs, at least one of them a
body measurement, becomes the parser's answer at once (logbook.log's no-AI path, like the web app's buttons). An unknown word
among them becomes a question and is never guessed; a line with anything else in it (a weight, a habit, a time) goes to the AI
as before."""
import re
from typing import Optional

from .body import LABEL, NAMES
from .v1_food import _resolve_type, _types

INCH_CM = 2.54
_LEAD = re.compile(r"\s*(?:measured|measurements?|body(?:\s+measurements?)?|tape)\s*:?\s*", re.I)
_PAIR = re.compile(r"([a-zäöüß]+(?:[ _-][a-zäöüß]+)?)\s*:?\s*(\d{1,3}(?:[.,]\d+)?)\s*(cm\b|inch(?:es)?\b|in\b|\")?", re.I)
_GAP = re.compile(r"(?:\s|[,;]|\band\b)*", re.I)


def pairs(text: str) -> Optional[list[tuple[str, float, bool]]]:
    """[(word, number, in inches)] when the whole line is pairs, else None."""
    lead = _LEAD.match(text)
    pos, out = (lead.end() if lead else 0), []
    while True:
        pos = _GAP.match(text, pos).end()
        if pos >= len(text):
            return out or None
        m = _PAIR.match(text, pos)
        if not m:
            return None
        unit = (m[3] or "").lower()
        out.append((m[1].lower(), float(m[2].replace(",", ".")), unit not in ("", "cm")))
        pos = m.end()


def read(cur, text: str) -> Optional[dict]:
    """The parser's answer for a line of body measurements, or None to let the AI read it."""
    found = pairs(text or "")
    if not found:
        return None
    _, idx = _types(cur)
    health, notes, known = [], [], 0
    for word, v, inches in found:
        t, _ = _resolve_type(idx, word)
        if t and t["name"] not in NAMES:
            return None  # a weight or a habit among them: the AI reads the whole line, as before
        if inches:
            value = round(v * INCH_CM, 1)
            notes.append(f"{word} {v:g} in = {value:.1f} cm")
        else:
            value = round(v, 1)
        known += bool(t)
        health.append({"type": t["name"] if t else word, "value": value, "value2": None, "period": None, "session": None,
                       "when": None, "tape": True})
    if not known:
        return None
    return {"intent": "log", "command_text": None, "private": None, "private_unsure": False, "food": [], "usual_meals": [],
            "health": health, "thoughts": [], "todos": [], "notes": notes}


def unknown_text(word: str, value: float) -> str:
    """The question for a word that is not a measure (settled in record.settle)."""
    return (f"'{word}' is not a body measurement I know ({', '.join(LABEL[k].lower() for k in NAMES)}). "
            f"Log '{word} {value:g}' again with one of those words.")
