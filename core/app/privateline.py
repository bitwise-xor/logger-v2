"""Private and gratitude lines, saved without the AI (decided 26.10.2026 for the Private page; 14.11.2026 for every door).
A message that starts with "gratitude:", "private:" or the short "g:" (= gratitude; there is deliberately no "p:") is
turned here into ready parser answers, so its words never reach the AI, the general inbox or the kept-words table.
A leaf module: it imports nothing from the app, so logbook, record and the web pages can all use it without a cycle."""
import re
from typing import Optional

# The one label pattern: at the start, after optional spaces, any case. The stored text always keeps the full form.
LABEL = re.compile(r"^\s*(gratitude|private|g)\s*:\s*", re.I)
KIND = {"gratitude": "gratitude", "g": "gratitude", "private": "private"}
MAX_LINES = 20


class Refused(ValueError):
    """A labelled text that cannot be saved as it is; the message says why (never the words). empty: nothing after the label."""

    def __init__(self, message: str, empty: bool = False):
        super().__init__(message)
        self.empty = empty


def blank(**kw) -> dict:
    """An empty parser answer with the given parts (the web app's buttons build theirs from this too)."""
    return {"intent": "log", "command_text": None, "private": None, "private_unsure": False, "food": [], "usual_meals": [],
            "health": [], "thoughts": [], "todos": [], **kw}


def label(text: Optional[str]) -> Optional[str]:
    """"gratitude" or "private" when the text starts with a label, else None."""
    m = LABEL.match(text or "")
    return KIND[m.group(1).lower()] if m else None


def words(text: Optional[str]) -> str:
    """The text without its label in front (only the first one)."""
    return LABEL.sub("", text or "", count=1)


def pred(kind: str, line: str) -> dict:
    """The ready parser answer for one entry: one thought, area personal; summary = the first line, squeezed, 200 characters."""
    summary = " ".join(line.split("\n")[0].split())[:200]
    return blank(private=kind, thoughts=[{"summary": summary, "thought_type": "journal" if kind == "gratitude" else "note",
                                          "area": "personal", "tags": ["gratitude"] if kind == "gratitude" else []}])


def entries(kind: str, body: str) -> list[tuple[str, dict]]:
    """The entries of one text of this kind (the words after the label) -> [(text to store, parser answer)].
    Gratitude: every non-empty line is its own entry, spaces squeezed, at most MAX_LINES. Private: the whole text is one
    entry, line breaks kept. A label repeated in front of a line is dropped, as the Private page always did. Raises Refused."""
    text = (body or "").replace("\r\n", "\n").strip()
    lines = [" ".join(x.split()) for x in text.split("\n")] if kind == "gratitude" else [text]
    lines = [words(x).strip() for x in lines if x.strip()]
    lines = [x for x in lines if x]
    if not lines:
        raise Refused("Type something after the label.", empty=True)
    if len(lines) > MAX_LINES:
        raise Refused(f"At most {MAX_LINES} lines at once.")
    return [(f"{kind}: {x}", pred(kind, x)) for x in lines]


def read(text: Optional[str]) -> Optional[tuple[str, list[tuple[str, dict]]]]:
    """A labelled text -> (kind, entries); None when the text has no label (it goes the normal way). Raises Refused."""
    kind = label(text)
    if kind is None:
        return None
    return kind, entries(kind, words(text))
