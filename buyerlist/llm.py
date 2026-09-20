"""Anthropic client wrapper with per-stage cost and cache telemetry.

The telemetry is not decoration: a central claim of this tool is that a buyer
list costs cents and seconds rather than hours of associate time, and that claim
should be measured on screen rather than asserted.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel

# Model assignment. Each stage uses the cheapest tier that does its job well:
# mechanical extraction from explicit criteria pages does not need Opus.
MODEL_INDEX_EXTRACT = "claude-haiku-4-5"   # 250 fund sites, structured pages
MODEL_COMPANY_EXTRACT = "claude-sonnet-5"  # messier SMB sites
MODEL_REASONING = "claude-opus-5"          # size estimation, ranking, critique

# USD per million tokens: (input, output)
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Haiku 4.5 predates the effort parameter and adaptive thinking; sending either
# is an error rather than a no-op, so gate on the model family.
_SUPPORTS_EFFORT = {"claude-opus-5", "claude-sonnet-5"}

# Cache writes bill above the input rate, and the premium depends on the TTL
# that was actually requested: 1.25x for the 5-minute cache, 2x for the 1-hour
# cache. Reads are ~0.1x for both. The fund index is the biggest cached prefix
# in this project and it asks for "1h", so hardcoding 1.25 understated the
# headline first-run cost exactly where the central cost claim lives.
CACHE_WRITE_MULTIPLIER: dict[str, float] = {"5m": 1.25, "1h": 2.0}


@dataclass
class StageUsage:
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    seconds: float = 0.0
    calls: int = 0
    cache_ttl: str = "5m"  # the TTL this stage requests; "5m" is the API default

    @property
    def cost_usd(self) -> float:
        if self.model not in PRICES:
            # A module whose whole point is that cost is measured rather than
            # asserted must never quietly price an unknown model at zero.
            raise KeyError(
                f"[{self.stage}] no price entry for model {self.model!r}; "
                f"add it to buyerlist.llm.PRICES (known: {sorted(PRICES)})"
            )
        pin, pout = PRICES[self.model]
        write_mult = CACHE_WRITE_MULTIPLIER[self.cache_ttl]
        return (
            self.input_tokens * pin
            + self.output_tokens * pout
            + self.cache_read_tokens * pin * 0.10  # cache reads bill at ~0.1x
            + self.cache_write_tokens * pin * write_mult
        ) / 1_000_000


@dataclass
class Telemetry:
    stages: list[StageUsage] = field(default_factory=list)

    def record(
        self, stage: str, model: str, usage, seconds: float, cache_ttl: str = "5m"
    ) -> None:
        # The TTL is part of the key: writes at different TTLs bill at different
        # multipliers, so they cannot share a row without mispricing one of them.
        for s in self.stages:
            if s.stage == stage and s.model == model and s.cache_ttl == cache_ttl:
                target = s
                break
        else:
            target = StageUsage(stage=stage, model=model, cache_ttl=cache_ttl)
            self.stages.append(target)

        target.input_tokens += getattr(usage, "input_tokens", 0) or 0
        target.output_tokens += getattr(usage, "output_tokens", 0) or 0
        target.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        target.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        target.seconds += seconds
        target.calls += 1

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.stages)

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.stages)

    def summary_rows(self) -> list[tuple[str, str, str, str, str, str]]:
        rows = []
        for s in self.stages:
            cache_note = f"{s.cache_read_tokens:,} read" if s.cache_read_tokens else "—"
            rows.append(
                (
                    s.stage,
                    s.model.replace("claude-", ""),
                    f"{s.input_tokens:,}",
                    f"{s.output_tokens:,}",
                    cache_note,
                    f"${s.cost_usd:.4f}",
                )
            )
        return rows


class LLM:
    def __init__(self, telemetry: Telemetry | None = None):
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        # Catch the copied-but-unedited template explicitly; otherwise the first
        # API call fails with an opaque 401 well into a long run.
        if key.startswith("sk-ant-your-key") or key == "":
            if not token:
                raise RuntimeError(
                    "No Anthropic credentials found.\n"
                    "  cp .env.example .env   then put your key in ANTHROPIC_API_KEY\n"
                    "  (or export ANTHROPIC_API_KEY, or run `ant auth login`)"
                )
        self.client = anthropic.Anthropic(max_retries=3)
        self.telemetry = telemetry or Telemetry()

    def parse(
        self,
        *,
        stage: str,
        model: str,
        schema: type[BaseModel],
        user_content: str,
        system: str | list[dict] | None = None,
        max_tokens: int = 8000,
        effort: str = "medium",
        cache_ttl: str = "5m",
    ):
        """One structured-output call. Returns a validated pydantic instance.

        Uses messages.parse so schema violations are retried at the tool-call
        layer rather than surfacing here as JSON parse errors.

        `cache_ttl` records the TTL the caller put in its `cache_control` block
        so telemetry prices the write at the right multiplier. It does not set
        the TTL — the caller still owns the `cache_control` it sends.
        """
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user_content}],
            "output_format": schema,
        }
        if system is not None:
            kwargs["system"] = system
        if model in _SUPPORTS_EFFORT:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effort}

        t0 = time.time()
        resp = self.client.messages.parse(**kwargs)
        elapsed = time.time() - t0

        self.telemetry.record(stage, model, resp.usage, elapsed, cache_ttl=cache_ttl)

        if resp.stop_reason == "refusal":
            raise RuntimeError(f"[{stage}] request was declined by safety classifiers")
        # max_tokens caps thinking + visible output together, and thinking is on
        # by default on opus-5. Truncation otherwise surfaces as a silent schema
        # retry or the generic "no parseable output", so name the real cause
        # before the generic check gets a chance to.
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(
                f"[{stage}] hit the max_tokens budget ({max_tokens:,}) before "
                f"finishing: thinking and visible output share this budget at "
                f"effort={effort!r}. Raise max_tokens for this stage (or lower "
                f"effort) and retry."
            )
        if resp.parsed_output is None:
            raise RuntimeError(f"[{stage}] model returned no parseable output")
        return resp.parsed_output
