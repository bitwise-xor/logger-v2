"""AI providers behind one small interface, so the parser can move to another API or a local model."""
import base64
import contextvars
import json
import time
from dataclasses import dataclass
from typing import Protocol

# USD per million tokens: input, output, cache read, cache write (the 1-hour cache: twice the input price). Estimates for
# the cost log, not a bill.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 4.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 8.00),
}


class ProviderError(Exception):
    pass


# A live read of a kept message (core item 5, 02.11.2026): someone waits for the reply, and the words are safe in
# private.incoming whatever happens, so each AI call gets 20 s and one retry (about 40 s at worst instead of about
# 3 minutes); then the message shows "not read yet" and cron reads it again with the full wait. Measured on the live
# app: a read takes about 2 s, a whole message at most 7.5 s. Set by logbook.read_held.
LIVE = contextvars.ContextVar("live_read", default=False)
LIVE_TIMEOUT = 20.0
LIVE_RETRIES = 1


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

    def image_json(self, system: str, user: str, images: list[tuple[str, bytes]], schema: dict, model: str,
                   max_tokens: int = 2000) -> tuple[dict, Usage]:
        """json() with photos (media type, bytes) before the text: a food label (31.10.2026)."""
        ...


class AnthropicProvider:
    def __init__(self, api_key: str, effort: str = "low", timeout: float = 60.0, max_retries: int = 2):
        # Three tries of at most 60 s each, so a message waits about three minutes at worst when the AI is down (it was five
        # tries and five minutes); a long lab report may need most of one try, so the time of one try stays. A live read
        # of a kept message is shorter (LIVE above).
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries, timeout=timeout)
        self.effort = effort

    def json(self, system: str, user: str, schema: dict, model: str, max_tokens: int = 4000) -> tuple[dict, Usage]:
        t0 = time.monotonic()
        client = self.client.with_options(timeout=LIVE_TIMEOUT, max_retries=LIVE_RETRIES) if LIVE.get() else self.client
        r = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # The instructions stay cached for an hour (02.11.2026): messages mostly come more than 5 minutes apart, so the
            # default 5-minute cache was used by about 1 read in 20; a cached read is about 0.7 s faster.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
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

    def image_json(self, system: str, user: str, images: list[tuple[str, bytes]], schema: dict, model: str,
                   max_tokens: int = 2000) -> tuple[dict, Usage]:
        content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(b).decode()}}
                   for mt, b in images]
        return self.json(system, content + [{"type": "text", "text": user}], schema, model, max_tokens)  # type: ignore[arg-type]
