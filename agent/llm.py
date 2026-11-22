"""Thin Anthropic client wrapper for the agent's cheap calls (relevance ranking,
preference summarization, status classification).

Mirrors apply.py:call_claude: retry/backoff, low effort (these are short
classification/ranking calls), text extracted past thinking blocks, and every
call appended to data/usage.jsonl so costs stay visible.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

import anthropic

from agent import config

# USD per million tokens (input, output) — for the usage log only.
_PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00),
           "claude-opus-5-5": (4.00, 20.00)}


def _log_usage(model: str, usage) -> None:
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    p_in, p_out = _PRICES.get(model, (0.0, 0.0))
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (config.DATA_DIR / "usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "source": "agent", "model": model, "input_tokens": inp,
                                "output_tokens": out,
                                "cost_usd": round(inp / 1e6 * p_in + out / 1e6 * p_out, 5)}) + "\n")
    except OSError:
        pass


def call(system: str, user: str, *, model: str, max_tokens: int = 2000,
         effort: str = "low", retries: int = 3) -> str:
    client = anthropic.Anthropic()
    for attempt in range(1, retries + 1):
        try:
            msg = client.messages.create(
                model=model, max_tokens=max(8000, max_tokens * 3),
                system=system, messages=[{"role": "user", "content": user}],
                extra_body={"output_config": {"effort": effort}})
            _log_usage(model, msg.usage)
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        except (anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APIConnectionError) as e:
            if attempt == retries:
                raise
            wait = 2 ** attempt
            print(f"   LLM error ({type(e).__name__}), retrying in {wait}s...")
            time.sleep(wait)
    return ""


def parse_json(raw: str) -> Any:
    """Parse a JSON object or array from a model response, tolerating code fences
    or stray prose around it."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    candidates = []
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = raw.find(opener), raw.rfind(closer)
        if 0 <= start < end:
            candidates.append((start, raw[start:end + 1]))
    for _, fragment in sorted(candidates):       # earliest-starting JSON value first
        try:
            return json.loads(fragment)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"Could not parse JSON from model output:\n{raw[:400]}")
