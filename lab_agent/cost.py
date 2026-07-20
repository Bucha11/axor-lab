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
# a USD budget with NO explicit output ceiling still has to reserve the output
# the next call will bill — otherwise the pre-spend USD projection counts input
# only and lets a call through that the (unbounded) output then blows past the
# ceiling. Reserve a realistic single-response output per call (review r14).
_DEFAULT_OUTPUT_RESERVE_PER_CALL = 512


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
    against ACTUAL usage BEFORE the first trial and BEFORE every provider call
    inside a trial's agent loop, so the run stops the moment a ceiling is reached
    rather than after a whole trial's worth of calls has already overshot it
    (review r11, r12). Unset limits (None) don't bind."""

    max_usd: float | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        # a zero or negative ceiling is not a budget — it is either a no-op the
        # caller mistook for "spend nothing" or an outright error. Reject it so a
        # `--max-usd 0` fails loudly instead of stopping the run before any trial
        # ran while looking like a successful empty run (review r12).
        for name in ("max_usd", "max_input_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be > 0 when set, got {value!r}")

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

    def is_overshot(self, usage: dict[str, int], model: str) -> bool:
        """True when actual usage is STRICTLY past a ceiling — i.e. the last
        completed call pushed us over, not merely up to, the limit. With per-call
        pre-checks this should be rare (we stop before the overshooting call),
        but input tokens and a final partial output are not fully controllable,
        so we report the honest label rather than pretend we stopped exactly."""
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        if self.max_input_tokens is not None and in_tok > self.max_input_tokens:
            return True
        if self.max_output_tokens is not None and out_tok > self.max_output_tokens:
            return True
        if self.max_usd is not None and actual_usd(in_tok, out_tok, model) > self.max_usd:
            return True
        return False

    def remaining_output_tokens(self, usage: dict[str, int]) -> int | None:
        """Output tokens left under the ceiling, to cap the NEXT provider call's
        max_tokens so a single call cannot blow far past the limit. None when no
        output ceiling is set."""
        if self.max_output_tokens is None:
            return None
        return max(0, self.max_output_tokens - int(usage.get("output_tokens", 0)))

    def pre_spend_exceeded(
        self, usage: dict[str, int], projected_input_tokens: int, model: str,
        projected_output_tokens: int = _DEFAULT_OUTPUT_RESERVE_PER_CALL,
    ) -> str | None:
        """Reject a call BEFORE it is made when its projected cost would breach an
        input-token or USD ceiling (review r13).

        The output ceiling is already enforced hard by capping the call's
        max_tokens, but input tokens and USD were only ever checked AFTER a call
        returned — so a single 200k-token prompt against a 100-token remaining
        input budget still went out, and the overshoot was noticed only once
        billed. This reserves the projected input (a best-effort estimate — input
        size is not perfectly predictable) PLUS the output we would allow, and
        refuses the call if that reservation exceeds the ceiling.

        The USD reservation always includes an OUTPUT allowance: when an output
        ceiling is set we reserve what remains under it; when it is NOT set, a
        USD-only budget still reserves `projected_output_tokens` for the call —
        otherwise the projection counts input only and a USD budget would never
        account for the (unbounded) output it is about to pay for (review r14)."""
        in_after = int(usage.get("input_tokens", 0)) + max(0, projected_input_tokens)
        if self.max_input_tokens is not None and in_after > self.max_input_tokens:
            return (
                f"next call's projected input (~{projected_input_tokens:,} tokens) would take "
                f"total input to {in_after:,}, over the ceiling {self.max_input_tokens:,}"
            )
        if self.max_usd is not None:
            capped = self.remaining_output_tokens(usage)
            out_reserve = capped if capped is not None else max(0, projected_output_tokens)
            out_after = int(usage.get("output_tokens", 0)) + out_reserve
            projected = actual_usd(in_after, out_after, model)
            if projected > self.max_usd:
                return (
                    f"next call's projected spend ${projected:.2f} would exceed the ceiling "
                    f"${self.max_usd:.2f}"
                )
        return None
