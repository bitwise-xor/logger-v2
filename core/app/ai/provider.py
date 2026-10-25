"""AI providers behind one small interface, so the parser can move to another API or a local model."""
import json
import time
from dataclasses import dataclass
from typing import Protocol

# USD per million tokens: input, output, cache read, cache write. Estimates for the cost log, not a bill.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}


class ProviderError(Exception):
    pass


@dataclass
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    seconds: float = 0.0

    @property
    def usd(self) -> float:
        p = PRICES.get(self.model)
        if not p:
            return 0.0
        return (self.input_tokens * p[0] + self.output_tokens * p[1]
                + self.cache_read_tokens * p[2] + self.cache_write_tokens * p[3]) / 1e6


class Provider(Protocol):
    def json(self, system: str, user: str, schema: dict, model: str, max_tokens: int = 4000) -> tuple[dict, Usage]:
        """Return an object that matches the JSON schema, and what the call used."""
        ...


class AnthropicProvider:
    def __init__(self, api_key: str, effort: str = "low", timeout: float = 60.0):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=timeout)
        self.effort = effort

    def json(self, system: str, user: str, schema: dict, model: str, max_tokens: int = 4000) -> tuple[dict, Usage]:
        t0 = time.monotonic()
        r = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if r.stop_reason == "refusal":
            raise ProviderError("the model declined the request")
        if r.stop_reason == "max_tokens":
            raise ProviderError("the answer was cut off (max_tokens)")
        text = next((b.text for b in r.content if b.type == "text"), None)
        if text is None:
            raise ProviderError("no text in the answer")
        u = r.usage
        usage = Usage(model, u.input_tokens, u.output_tokens,
                      getattr(u, "cache_read_input_tokens", 0) or 0,
                      getattr(u, "cache_creation_input_tokens", 0) or 0,
                      time.monotonic() - t0)
        return json.loads(text), usage
