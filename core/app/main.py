from . import migrate
from .mcp_app import build_app

migrate.run()
app = build_app()
