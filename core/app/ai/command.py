"""Turn one command ("what did I eat yesterday", "the apple was 150 g") into the logger's find and change calls."""
import pathlib
from datetime import datetime

from .parser import _fmt, _null, _obj, STR
from .provider import Provider, Usage

PROMPT = (pathlib.Path(__file__).parent / "prompts" / "command.md").read_text()
DEFAULT_MODEL = "claude-haiku-5-5"

# args is JSON text: v1's find and change take many optional arguments, and the structured-output schema allows at
# most 16 nullable fields, so the core checks the keys itself (commands.FIND_ARGS, CHANGE_ARGS).
SCHEMA = _obj({
    "steps": {"type": "array", "items": _obj({"action": {"type": "string", "enum": ["find", "change", "undo", "todo"]}, "args": STR})},
    "unclear": _null(STR),
})


def build_input(text: str, now: datetime, previous: list[tuple[datetime, str]], entries: str) -> str:
    lines = [f"Now: {_fmt(now)} (Europe/Berlin)"]
    if previous:
        lines.append("Previous messages:")
        lines += [f"- {_fmt(t)}: {m}" for t, m in previous]
    lines += ["Recent entries:", entries or "none", "", "Request:", '"""', text, '"""']
    return "\n".join(lines)


def read(provider: Provider, text: str, now: datetime, previous: list[tuple[datetime, str]] = (), entries: str = "",
         model: str = DEFAULT_MODEL) -> tuple[dict, Usage]:
    return provider.json(PROMPT, build_input(text, now, list(previous), entries), SCHEMA, model)