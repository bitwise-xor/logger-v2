"""MCP adapter: thin. It exposes the core's operations as tools and holds no rules."""
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import service
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

from . import auth, today
from .config import EXTRA_ALLOWED_HOSTS, MCP_PATH, PUBLIC_URL

provider = auth.OwnerProvider(PUBLIC_URL)

mcp = MCPServer(
    "logger-v2",
    auth_server_provider=provider,
    auth=AuthSettings(
        issuer_url=PUBLIC_URL, resource_server_url=PUBLIC_URL + MCP_PATH, validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[auth.SCOPE], default_scopes=[auth.SCOPE]),
        required_scopes=[auth.SCOPE],
    ),
    instructions=("Personal logger (pilot). Send only real log messages to these tools. Never invent values; when a reply says "
                  "needs_confirmation, ask the user and then call resolve. Send ISO local times (Europe/Berlin) in `at`."),
)


@mcp.tool()
def record_weight(kg: float, at: Optional[str] = None, text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    """Log a body weight in kg. at = ISO local date/time of the weighing (omit for now). text = the user's original words.
    idempotency_key = a new unique string per user message; reuse the same one when retrying the same message."""
    return service.record_weight(kg, at, text, idempotency_key)


@mcp.tool()
def recent_weights(days: int = 14) -> dict[str, Any]:
    """List the weights logged in the last N days (newest first)."""
    return service.recent_weights(days)


@mcp.tool()
def record_bp(systolic: float, diastolic: float, period: Optional[str] = None, at: Optional[str] = None, text: Optional[str] = None,
              idempotency_key: Optional[str] = None) -> dict[str, Any]:
    """Log a blood pressure reading in mmHg (systolic over diastolic). period = morning or evening if the user said so.
    at = ISO local date/time of the reading (omit for now). text = the user's original words. idempotency_key as for record_weight."""
    return service.record_bp(systolic, diastolic, period, at, text, idempotency_key)


@mcp.tool()
def recent_blood_pressure(days: int = 14) -> dict[str, Any]:
    """List the blood pressure readings logged in the last N days (newest first)."""
    return service.recent_bp(days)


@mcp.tool()
def record_wakeup(at: Optional[str] = None, text: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, Any]:
    """Log getting up. at = ISO local date/time of waking (omit for now). text = the user's original words. idempotency_key as for record_weight."""
    return service.record_wakeup(at, text, idempotency_key)


@mcp.tool()
def recent_wakeups(days: int = 14) -> dict[str, Any]:
    """List the wake-ups logged in the last N days (newest first)."""
    return service.recent_wakeups(days)


@mcp.tool()
def amend(receipt_id: str, kg: Optional[float] = None, systolic: Optional[float] = None, diastolic: Optional[float] = None,
          period: Optional[str] = None, at: Optional[str] = None) -> dict[str, Any]:
    """Correct an entry already saved, by its receipt id (m123 for weight or blood pressure, e45 for a wake-up). Give only what changes:
    kg (weight); systolic, diastolic, period (blood pressure); at (any entry, ISO local time). Prefer this over undo plus a new entry."""
    return service.amend(receipt_id, kg, systolic, diastolic, period, at)


@mcp.tool()
def resolve(pending_id: str, confirm: bool) -> dict[str, Any]:
    """Answer a needs_confirmation question: confirm true saves the entry, false cancels it."""
    return service.resolve(pending_id, confirm)


@mcp.tool()
def undo(receipt_id: str) -> dict[str, Any]:
    """Remove an entry by its receipt id (for example m123 or e45)."""
    return service.undo(receipt_id)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "logger-v2-core"})


auth.add_login_routes(mcp, provider)
today.add_route(mcp)


def build_app():
    hosts = ["127.0.0.1:*", "localhost:*", *EXTRA_ALLOWED_HOSTS]
    return mcp.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=[]),
    )
