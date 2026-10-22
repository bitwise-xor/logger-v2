from fastapi import FastAPI

# No interactive docs or schema on a public hostname.
app = FastAPI(title="logger-v2", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "logger-v2-core"}
