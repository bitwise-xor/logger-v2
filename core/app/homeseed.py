"""`docker exec logger-v2-core python -m app.homeseed`: make the starter zones and chores once (home.seed, 03.11.2026) when there is
no zone yet; prints what it did."""
import json

from .home import seed

if __name__ == "__main__":
    print(json.dumps(seed()))
