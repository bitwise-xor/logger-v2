"""Turn one free-text message into structured entries. The core resolves names, units and dates afterwards."""
import pathlib
from datetime import datetime

from .provider import Provider, Usage

PROMPT = (pathlib.Path(__file__).parent / "prompts" / "parser.md").read_text()
DEFAULT_MODEL = "claude-haiku-5-5"


def _null(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR, NUM, INT, BOOL = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
WHEN = _null(STR)  # the user's time words in a form app.when.parse_when reads ("22:00", "yesterday 22:00", "13.10. 09:00")

SCHEMA = _obj({
    "intent": {"type": "string", "enum": ["log", "question", "command"]},
    "command_text": _null(STR),
    "private": _null({"type": "string", "enum": ["private", "gratitude"]}),
    "private_unsure": BOOL,
    "food": {"type": "array", "items": _obj({
        "food": STR, "amount": _null(NUM), "unit": _null(STR), "grams_per_unit": _null(NUM),
        "cooked": BOOL, "drink": _null(BOOL), "when": WHEN, "note": _null(STR),
    })},
    "usual_meals": {"type": "array", "items": _obj({
        "name": STR, "skip": {"type": "array", "items": STR},
        "changes": {"type": "array", "items": _obj({"food": STR, "grams": NUM})},
        "when": WHEN,
    })},
    "health": {"type": "array", "items": _obj({
        "type": STR, "value": _null(NUM), "value2": _null(NUM),
        "period": _null({"type": "string", "enum": ["morning", "evening", "fasted"]}),
        "session": _null(INT), "when": WHEN,
    })},
    "thoughts": {"type": "array", "items": _obj({
        "summary": STR,
        "thought_type": {"type": "string", "enum": ["idea", "question", "task", "project", "note", "journal", "reading", "progress"]},
        "area": {"type": "string", "enum": ["health", "projects", "finance", "immigration", "learning", "personal"]},
        "tags": {"type": "array", "items": STR}, "project": _null(STR),
    })},
    "todos": {"type": "array", "items": _obj({
        "item": STR, "list": {"type": "string", "enum": ["shopping", "tasks"]}, "store": _null(STR),
    })},
})


def _fmt(dt: datetime) -> str:
    return dt.strftime("%a %d.%m.%Y %H:%M")


def build_input(text: str, now: datetime, previous: list[tuple[datetime, str]], meals: list[tuple[str, datetime, list[str]]]) -> str:
    """meals: today's meals and drinks so far as (label, time, food names), e.g. ("meal 1", 10:12, ["skyr", "whey"])."""
    lines = [f"Now: {_fmt(now)} (Europe/Berlin)"]
    if meals:
        lines.append("Logged so far today:")
        lines += [f"- {label} at {t:%H:%M}: {', '.join(names)}" for label, t, names in meals]
    else:
        lines.append("Logged so far today: nothing")
    if previous:
        lines.append("Previous messages:")
        lines += [f"- {_fmt(t)}: {m}" for t, m in previous]
    lines += ["", "Message:", '"""', text, '"""']
    return "\n".join(lines)


def parse(provider: Provider, text: str, now: datetime, previous: list[tuple[datetime, str]] = (),
          meals: list[tuple[str, datetime, list[str]]] = (), model: str = DEFAULT_MODEL) -> tuple[dict, Usage]:
    """previous: earlier messages of the conversation, oldest first."""
    return provider.json(PROMPT, build_input(text, now, list(previous), list(meals)), SCHEMA, model)
