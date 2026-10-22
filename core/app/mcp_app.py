"""MCP adapter: thin. It exposes the core's operations as tools and holds no rules."""
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import service
from .config import EXTRA_ALLOWED_HOSTS, MCP_PATH

mcp = MCPServer(
    "logger-v2",
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
def resolve(pending_id: str, confirm: bool) -> dict[str, Any]:
    """Answer a needs_confirmation question: confirm true saves the entry, false cancels it."""
    return service.resolve(pending_id, confirm)


@mcp.tool()
def undo(receipt_id: str) -> dict[str, Any]:
    """Remove an entry by its receipt id (for example m123)."""
    return service.undo(receipt_id)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "logger-v2-core"})


def build_app():
    hosts = ["127.0.0.1:*", "localhost:*", *EXTRA_ALLOWED_HOSTS]
    return mcp.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=[]),
    )
