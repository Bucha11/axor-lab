"""Model backends behind one protocol.

A backend turns a conversation (messages + available tools) into the next
model action: either a tool call or a final answer. Two implementations:

- `CassetteBackend` — replays a recorded transcript. Deterministic, offline,
  the CI-safe way to cover the wrapped runtime without a key or network.
- `AnthropicBackend` — the real BYOK path (Claude Messages API tool-use loop).
  Its dependency and key are optional; importing lab_agent never requires them
  (optional-dependency pattern), and the class raises `BackendUnavailable`
  only when actually used without them.

Provenance note: a backend only chooses *what* the model does. It never
assigns provenance labels — those are the wrapped runtime's, so a hostile or
buggy backend cannot launder taint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from .errors import BackendUnavailable, CassetteExhausted

TOOL_CALL = "tool_call"
FINAL = "final"


@dataclass(frozen=True)
class ModelAction:
    """The next thing the model wants to do."""

    kind: str  # TOOL_CALL | FINAL
    tool: str | None = None
    args: dict[str, object] | None = None
    text: str | None = None


class ModelBackend(Protocol):
    """Turns (messages, tools) into the next `ModelAction` (structural)."""

    def next_action(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]],
        max_output_tokens: int | None = None,
    ) -> ModelAction:
        ...

    def usage(self) -> dict[str, int]:
        """Cumulative token usage so far (for the cost estimate/report)."""
        ...


@dataclass
class CassetteBackend:
    """Replays a fixed sequence of `ModelAction`s (a recorded transcript).

    A cassette is authored as a list of {tool, args} / {text} dicts; each call
    to `next_action` returns the next one. This is how the acceptance suite
    exercises the wrapped runtime deterministically, no network involved.
    """

    turns: tuple[ModelAction, ...]
    _cursor: int = 0
    _tokens: int = 0
    # a recorded transcript replays identically, so a per-call fresh cassette
    # yields the same model behavior in both conditions — a real matched pair
    is_deterministic: bool = True

    @classmethod
    def from_records(cls, records: list[dict[str, object]]) -> "CassetteBackend":
        actions: list[ModelAction] = []
        for record in records:
            if "tool" in record:
                actions.append(
                    ModelAction(kind=TOOL_CALL, tool=str(record["tool"]),
                                args=dict(record.get("args", {})))  # type: ignore[arg-type]
                )
            else:
                actions.append(ModelAction(kind=FINAL, text=str(record.get("text", ""))))
        return cls(turns=tuple(actions))

    def next_action(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]],
        max_output_tokens: int | None = None,
    ) -> ModelAction:
        # max_output_tokens is honored by the live backend; a cassette replays a
        # fixed transcript, so it is accepted (protocol parity) and ignored
        if self._cursor >= len(self.turns):
            raise CassetteExhausted(f"cassette has only {len(self.turns)} turn(s)")
        action = self.turns[self._cursor]
        self._cursor += 1
        # a crude but honest token proxy so the cost path is exercised offline
        self._tokens += sum(len(str(m)) for m in messages) // 4
        return action

    def usage(self) -> dict[str, int]:
        return {"input_tokens": self._tokens, "output_tokens": self._cursor * 8}


@dataclass
class AnthropicBackend:
    """The real BYOK backend (Claude Messages API tool-use loop).

    Optional-dependency + optional-key: constructing it is cheap; the SDK and
    key are only required at `next_action`. Kept import-light so lab_agent has
    no hard dependency (a wheel installs with zero deps).
    """

    model: str = "claude-opus-4-8"
    api_key_env: str = "ANTHROPIC_API_KEY"
    _tokens_in: int = 0
    _tokens_out: int = 0
    # live model sampling: each condition is an INDEPENDENT draw (no shared seed),
    # so ungoverned/governed are not matched pairs — McNemar does not apply
    is_deterministic: bool = False

    def next_action(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]],
        max_output_tokens: int | None = None,
    ) -> ModelAction:
        client = self._client()
        # cap this call's output at the budget's remaining output tokens, so a
        # single call cannot blow far past an output/USD ceiling (review r12).
        # A remaining budget of 0 would be caught by the pre-call guard before we
        # get here, so clamp to at least 1 for a well-formed request.
        cap = 1024 if max_output_tokens is None else max(1, min(1024, max_output_tokens))
        response = client.messages.create(
            model=self.model,
            max_tokens=cap,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self._tokens_in += int(getattr(usage, "input_tokens", 0))
            self._tokens_out += int(getattr(usage, "output_tokens", 0))
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return ModelAction(
                    kind=TOOL_CALL, tool=str(block.name), args=dict(block.input)  # type: ignore[arg-type]
                )
        text = "".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
        )
        return ModelAction(kind=FINAL, text=text)

    def usage(self) -> dict[str, int]:
        return {"input_tokens": self._tokens_in, "output_tokens": self._tokens_out}

    def _client(self) -> object:
        try:
            import anthropic  # noqa: PLC0415 (optional dependency)
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK
            raise BackendUnavailable(
                "the anthropic SDK is not installed; `pip install anthropic` for the BYOK path"
            ) from exc
        key = os.environ.get(self.api_key_env)
        if not key:
            raise BackendUnavailable(f"{self.api_key_env} is not set (BYOK: bring your own key)")
        return anthropic.Anthropic(api_key=key)
