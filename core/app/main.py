import logging

from . import maintenance, migrate
from .mcp_app import build_app

# One line per event on the container's log (docker logs): time, level, who, what. Never the user's words or values.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

migrate.run()
app = build_app()
maintenance.start()
