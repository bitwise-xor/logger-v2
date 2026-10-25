"""What each logging chat costs before the user types anything: tool definitions plus the project prompt.
Counted with Anthropic's token-counting endpoint (free), for v1's live connector and for v2.

    .venv/bin/python tools/measure_tokens.py [--v1-server ~/stack/mcp/server.py] [--v1-prompt <file>]
"""
import argparse
import asyncio
import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import anthropic  # noqa: E402

# The short project prompt a v2 logging chat needs; the rules live in the server.
V2_PROMPT = (ROOT / "docs" / "chat-prompt.md").read_text()


def env_value(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():  # literal, no shell parsing
        if line.startswith(key + "="):
            return line[len(key) + 1:].strip()
    sys.exit(f"{key} is not set")


def tools_of(mcp) -> list[dict]:
    return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in asyncio.run(mcp.list_tools())]


def v1_tools(path: str) -> list[dict]:
    os.environ.setdefault("LOG_PASSPHRASE", "x")  # the connector checks it per call, not at import
    spec = importlib.util.spec_from_file_location("v1_server", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tools_of(mod.mcp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-server", default=str(pathlib.Path.home() / "stack" / "mcp" / "server.py"))
    ap.add_argument("--v1-prompt", default=str(pathlib.Path.home() / "migration" / "logger-app" / "docs" / "chat-instructions.md"))
    a = ap.parse_args()

    os.environ.update(MCP_PATH="/mcp", PUBLIC_URL="https://log.example.test", ALLOWED_HOSTS="log.example.test")
    from app.mcp_app import mcp as v2  # noqa: E402
    v2_all = tools_of(v2)
    v2_new = [t for t in v2_all if t["name"] in ("log", "answer")]
    v1 = v1_tools(a.v1_server)
    v1_prompt = pathlib.Path(a.v1_prompt).read_text()

    client = anthropic.Anthropic(api_key=env_value("ANTHROPIC_API_KEY"))
    msgs = [{"role": "user", "content": "hi"}]

    def count(model, system=None, tools=None):
        kw = {"model": model, "messages": msgs}
        if system:
            kw["system"] = system
        if tools:
            kw["tools"] = tools
        return client.messages.count_tokens(**kw).input_tokens

    print(f"v1 tools: {[t['name'] for t in v1]}; v2 new tools: {[t['name'] for t in v2_new]}; pilot tools: {len(v2_all) - len(v2_new)}")
    print(f"v1 prompt: {len(v1_prompt.split())} words; v2 prompt: {len(V2_PROMPT.split())} words\n")
    print("| Per chat, before the first message | " + " | ".join(m for m in ("claude-sonnet-5-5", "claude-opus-5-5")) + " |")
    print("| --- | --- | --- |")
    rows = [
        ("v1 tool definitions (find, log, change)", dict(tools=v1)),
        ("v1 project instructions", dict(system=v1_prompt)),
        ("**v1 total**", dict(system=v1_prompt, tools=v1)),
        ("v2 tool definitions (log, answer)", dict(tools=v2_new)),
        ("v2 project prompt", dict(system=V2_PROMPT)),
        ("**v2 total**", dict(system=V2_PROMPT, tools=v2_new)),
        ("pilot tools v2 can retire (9)", dict(tools=[t for t in v2_all if t not in v2_new])),
    ]
    base = {m: count(m) for m in ("claude-sonnet-5-5", "claude-opus-5-5")}
    for label, kw in rows:
        vals = [count(m, **kw) - base[m] for m in ("claude-sonnet-5-5", "claude-opus-5-5")]
        print(f"| {label} | " + " | ".join(f"{v:,}" for v in vals) + " |")


if __name__ == "__main__":
    main()
