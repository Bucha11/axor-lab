"""Pre-run cost estimation (spec-lab.md §9: a pre-run estimate before any
live run; BYOK or local, so Lab never underwrites unbounded fan-out)."""

from __future__ import annotations

from dataclasses import dataclass

# Illustrative per-1M-token USD prices; the point is a shown estimate + a hard
# ceiling, not a billing-accurate quote. BYOK means the researcher pays their
# provider directly — Lab never resells tokens.
_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "scripted": (0.0, 0.0),
    "cassette": (0.0, 0.0),  # a recorded transcript — no live inference
}
_DEFAULT_TURN_TOKENS = (900, 120)  # (input, output) per trial, rough


@dataclass(frozen=True)
class CostEstimate:
    trials: int
    model: str
    est_input_tokens: int
    est_output_tokens: int
    est_usd: float

    def line(self) -> str:
        if self.est_usd == 0.0:
            return f"{self.trials} trials on {self.model}: $0.00 (no paid inference)"
        return (
            f"{self.trials} trials on {self.model}: ~{self.est_input_tokens:,} in / "
            f"{self.est_output_tokens:,} out tokens, ~${self.est_usd:.2f} (BYOK — you pay your provider)"
        )


def estimate_cost(trials: int, model: str) -> CostEstimate:
    prices = _PRICES_PER_MTOK.get(_price_key(model), _PRICES_PER_MTOK["claude-opus-4-8"])
    in_tok = trials * _DEFAULT_TURN_TOKENS[0]
    out_tok = trials * _DEFAULT_TURN_TOKENS[1]
    usd = (in_tok / 1_000_000) * prices[0] + (out_tok / 1_000_000) * prices[1]
    return CostEstimate(
        trials=trials, model=model, est_input_tokens=in_tok,
        est_output_tokens=out_tok, est_usd=round(usd, 2),
    )


def _price_key(model: str) -> str:
    if model.startswith("scripted"):
        return "scripted"
    if model.startswith("cassette"):
        return "cassette"
    for key in _PRICES_PER_MTOK:
        if key in model:
            return key
    return "claude-opus-4-8"


def actual_usd(input_tokens: int, output_tokens: int, model: str) -> float:
    """USD for ACTUAL measured tokens (same price table as the estimate)."""
    prices = _PRICES_PER_MTOK.get(_price_key(model), _PRICES_PER_MTOK["claude-opus-4-8"])
    usd = (input_tokens / 1_000_000) * prices[0] + (output_tokens / 1_000_000) * prices[1]
    return round(usd, 4)


@dataclass(frozen=True)
class CostBudget:
    """A HARD run-wide ceiling — the enforcement half the cost layer promised but
    never had (it only ever printed an estimate). Any set limit is checked
    against ACTUAL usage between trials, so the run stops BEFORE the next provider
    call once a ceiling is reached (review r11). Unset limits (None) don't bind."""

    max_usd: float | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def is_set(self) -> bool:
        return any(x is not None for x in (self.max_usd, self.max_input_tokens, self.max_output_tokens))

    def exceeded(self, usage: dict[str, int], model: str) -> str | None:
        """Return a human reason if actual `usage` has reached any ceiling, else
        None. Uses >= so we stop AT the ceiling, not one call past it."""
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        if self.max_input_tokens is not None and in_tok >= self.max_input_tokens:
            return f"input tokens {in_tok:,} reached ceiling {self.max_input_tokens:,}"
        if self.max_output_tokens is not None and out_tok >= self.max_output_tokens:
            return f"output tokens {out_tok:,} reached ceiling {self.max_output_tokens:,}"
        if self.max_usd is not None:
            spent = actual_usd(in_tok, out_tok, model)
            if spent >= self.max_usd:
                return f"spend ${spent:.2f} reached ceiling ${self.max_usd:.2f}"
        return None
