import os


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise RuntimeError(f"missing setting {name}")
    return v


DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "appdata")

# The MCP endpoint lives at an unguessable path while it has no login (pilot, test data only).
MCP_PATH = os.environ.get("MCP_PATH", "/mcp")
EXTRA_ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "log.19hospitalroad.fyi").split(",") if h.strip()]

PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://log.19hospitalroad.fyi").rstrip("/")
# Where the AI apps send the browser back after sign-in (claude.ai now, claude.com possibly later).
ALLOWED_REDIRECTS = [u.strip() for u in os.environ.get(
    "ALLOWED_REDIRECTS", "https://claude.ai/api/mcp/auth_callback,https://claude.com/api/mcp/auth_callback").split(",") if u.strip()]
TODAY_HOST = os.environ.get("TODAY_HOST", "today.19hospitalroad.fyi").lower()
