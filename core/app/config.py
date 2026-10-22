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
