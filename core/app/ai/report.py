"""Turn the text of a lab or imaging report into v1's report format (the larger model; reports come once or twice a year)."""
import pathlib

from .parser import STR, _obj
from .provider import Provider, Usage

PROMPT = (pathlib.Path(__file__).parent / "prompts" / "report.md").read_text()
MODEL = "claude-sonnet-5-5"
E = lambda *values: {"type": "string", "enum": ["", *values]}  # noqa: E731 - "" = not on the report

# Every optional field is a string ("" = none): the structured-output schema allows at most 16 nullable fields.
SCHEMA = _obj({
    "type": {"type": "string", "enum": ["lab", "imaging"]}, "title": STR, "provider": STR, "city": STR, "country": STR,
    "collected": STR, "reported": STR, "accession": STR, "ordered_by": STR, "signed_by": STR, "fasting": STR, "status": STR,
    "notes": STR, "tz": STR,
    "values": {"type": "array", "items": _obj({
        "test": STR, "value": STR, "unit": STR, "ref": STR, "printed_name": STR, "panel": STR, "ref_low": STR, "ref_high": STR,
        "site": STR, "side": E("left", "right", "both"), "specimen": STR, "method": STR, "calculated": E("yes", "no"),
        "abnormal": E("yes", "no"), "note": STR,
    })},
})


def read(provider: Provider, text: str, known_tests: list[str], corrections: str = "") -> tuple[dict, Usage]:
    user = "\n".join(["Known tests: " + "; ".join(known_tests), "", "Report text:", '"""', text, '"""']
                     + (["", "Corrections from the person:", '"""', corrections, '"""'] if corrections else []))
    return provider.json(PROMPT, user, SCHEMA, MODEL, max_tokens=16000)