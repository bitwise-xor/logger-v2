"""MCP adapter: thin. It exposes the core's operations as tools and holds no rules."""
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import logbook, service
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

from . import auth, web
from .config import EXTRA_ALLOWED_HOSTS, MCP_PATH, PILOT_TOOLS, PUBLIC_URL

provider = auth.OwnerProvider(PUBLIC_URL)

mcp = MCPServer(
    "logger-v2",
    auth_server_provider=provider,
    auth=AuthSettings(
        issuer_url=PUBLIC_URL, resource_server_url=PUBLIC_URL + MCP_PATH, validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[auth.SCOPE], default_scopes=[auth.SCOPE]),
        required_scopes=[auth.SCOPE],
    ),
    instructions=("Personal logger. Pass each message the user wants logged to `log`, word for word; the logger reads it. "
                  "Show its reply briefly. When it asks a question, ask the user and pass the reply to `answer`."),
)



def _pilot(f):
    """Register a pilot tool only when PILOT_TOOLS=1 (config.py)."""
    return mcp.tool()(f) if PILOT_TOOLS else f

@mcp.tool()
def log(text: str, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    """Log one user message (food, drinks, health values, habits, thoughts, private or gratitude lines) in the user's
    exact words, to-dos and shopping (they go to Todoist); also look-ups and changes ("what did I eat yesterday", "the apple
    was 150 g", "got the milk", "undo").
    Not for other questions to you. idempotency_key: a new unique string per message, the same on a retry."""
    return logbook.log(text, idempotency_key)


@mcp.tool()
def add_food(name: str, kcal: float, protein: float, fat: float, carbs: float, source: str, sat_fat: Optional[float] = None,
             fibre: Optional[float] = None, sugar_total: Optional[float] = None, sugar_added: Optional[float] = None,
             sodium_mg: Optional[float] = None, caffeine_mg: Optional[float] = None, basis: str = "g",
             source_note: Optional[str] = None, state: Optional[str] = None, drink: bool = False,
             question_id: Optional[str] = None, aliases: Optional[list[str]] = None) -> dict[str, Any]:
    """Add a food missing from the catalogue, values per 100 g (basis ml: per 100 ml). source: label (read from the
    user's photo), database (name it in source_note) or estimate. Name in English, brand in brackets. A label's
    "sugar" is sugar_total; sodium_mg = salt g x 400; leave out what is not printed. question_id: the open food
    question, so the waiting item is saved with it. aliases: other words the user uses for it."""
    values = dict(kcal=kcal, protein=protein, fat=fat, carbs=carbs, source=source, sat_fat=sat_fat, fibre=fibre,
                  sugar_total=sugar_total, sugar_added=sugar_added, sodium_mg=sodium_mg, caffeine_mg=caffeine_mg,
                  basis=basis, source_note=source_note, state=state, drink=drink)
    return logbook.add_food(name, values, question_id, aliases)


@mcp.tool()
def food_words(food_id: int, words: list[str], means: bool = False) -> dict[str, Any]:
    """Teach the logger words for a food it knows (N id): each word then finds that food. means=true when the user says
    a word now means this product ("waffle now means N250")."""
    return logbook.food_words(food_id, words, means)


@mcp.tool()
def food_lookup(barcode: Optional[str] = None, name: Optional[str] = None, question_id: Optional[str] = None) -> dict[str, Any]:
    """Look up a food the catalogue does not know: by barcode (the digits under the bars, from the user's photo or words)
    in Open Food Facts, or by name in USDA and Open Food Facts. question_id: the open food question it answers.
    Show the user the values it returns; the food is added only after their yes (answer)."""
    return logbook.food_lookup(barcode, name, question_id)


@mcp.tool()
def lab_report(text: str, corrections: Optional[str] = None, source: str = "image") -> dict[str, Any]:
    """A lab or imaging report from the user's photo or PDF: text = everything on all pages written out exactly as printed
    (values, units, ranges, names, dates; never converted or translated). source: image or document. Returns a read-back
    table: show it whole; the user's yes goes to answer; for a correction call again with the same text and corrections."""
    return logbook.lab_report(text, corrections, source)


@mcp.tool()
def answer(question_id: str, reply: str) -> dict[str, Any]:
    """Answer a question from `log` with the user's reply: a food name or N number, an amount, yes, or skip."""
    return logbook.answer(question_id, reply)


@_pilot
def record_weight(kg: float, at: Optional[str] = None, text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    """Log a body weight in kg. at = ISO local date/time of the weighing (omit for now). text = the user's original words.
    idempotency_key = a new unique string per user message; reuse the same one when retrying the same message."""
    return service.record_weight(kg, at, text, idempotency_key)


@_pilot
def recent_weights(days: int = 14) -> dict[str, Any]:
    """List the weights logged in the last N days (newest first)."""
    return service.recent_weights(days)


@_pilot
def record_bp(systolic: float, diastolic: float, period: Optional[str] = None, at: Optional[str] = None, text: Optional[str] = None,
              idempotency_key: Optional[str] = None) -> dict[str, Any]:
    """Log a blood pressure reading in mmHg (systolic over diastolic). period = morning or evening if the user said so.
    at = ISO local date/time of the reading (omit for now). text = the user's original words. idempotency_key as for record_weight."""
    return service.record_bp(systolic, diastolic, period, at, text, idempotency_key)


@_pilot
def recent_blood_pressure(days: int = 14) -> dict[str, Any]:
    """List the blood pressure readings logged in the last N days (newest first)."""
    return service.recent_bp(days)


@_pilot
def record_wakeup(at: Optional[str] = None, text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    """Log getting up. at = ISO local date/time of waking (omit for now). text = the user's original words. idempotency_key as for record_weight."""
    return service.record_wakeup(at, text, idempotency_key)


@_pilot
def recent_wakeups(days: int = 14) -> dict[str, Any]:
    """List the wake-ups logged in the last N days (newest first)."""
    return service.recent_wakeups(days)


@_pilot
def amend(receipt_id: str, kg: Optional[float] = None, systolic: Optional[float] = None, diastolic: Optional[float] = None,
          period: Optional[str] = None, at: Optional[str] = None) -> dict[str, Any]:
    """Correct an entry already saved, by its receipt id (m123 for weight or blood pressure, e45 for a wake-up). Give only what changes:
    kg (weight); systolic, diastolic, period (blood pressure); at (any entry, ISO local time). Prefer this over undo plus a new entry."""
    return service.amend(receipt_id, kg, systolic, diastolic, period, at)


@_pilot
def resolve(pending_id: str, confirm: bool) -> dict[str, Any]:
    """Answer a needs_confirmation question: confirm true saves the entry, false cancels it."""
    return service.resolve(pending_id, confirm)


@_pilot
def undo(receipt_id: str) -> dict[str, Any]:
    """Remove an entry by its receipt id (for example m123 or e45)."""
    return service.undo(receipt_id)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "logger-v2-core"})


auth.add_login_routes(mcp, provider)
web.add_routes(mcp)


def build_app():
    hosts = ["127.0.0.1:*", "localhost:*", *EXTRA_ALLOWED_HOSTS]
    return mcp.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=[]),
    )
