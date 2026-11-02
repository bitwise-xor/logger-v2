"""`docker exec logger-v2-core python -m app.gymseed`: make the starter split once (gym.seed, 02.11.2026) when there is no split
yet; prints what it did."""
import json

from .gym import seed

if __name__ == "__main__":
    print(json.dumps(seed()))
