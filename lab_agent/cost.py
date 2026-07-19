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
