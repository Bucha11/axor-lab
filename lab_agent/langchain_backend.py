"""LangChainBackend — the axor-langchain model call as a `ModelBackend`.

A framework adapter's only job at the model boundary is to turn (messages, tools)
into the next `ModelAction` (adapters.md §2); the surrounding wrapped runtime does
the provenance mint + gate-before-dispatch. This backend does exactly that for a
LangChain chat model, so `axor-langchain` reuses the same kernel/ledger/gate path as
every other adapter — the framework only supplies the decision.

Duck-typed so `langchain` is an OPTIONAL dependency: any object exposing
`bind_tools(tools).invoke(messages) -> AIMessage`-like (with `.tool_calls` and
`.content`) works — a real `langchain_*.ChatModel`, or a fake in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .backends import FINAL, TOOL_CALL, ModelAction


@dataclass
class LangChainBackend:
    """Wrap a LangChain chat model as a `ModelBackend`.

    `model` is any LangChain chat model (`ChatAnthropic`, `ChatOpenAI`, …) or a
    LangGraph node exposing the same `bind_tools(...).invoke(...)` shape."""

    model: object
    # a live model samples each condition independently → NOT matched pairs, so the
    # analysis must not apply McNemar (statistics.md); the backend declares this the
    # same way AnthropicBackend does.
    is_deterministic: bool = False
    _tokens_in: int = 0
    _tokens_out: int = 0
    _usage: dict[str, int] = field(default_factory=dict)

    def next_action(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]],
        max_output_tokens: int | None = None,
    ) -> ModelAction:
        bound = self.model.bind_tools(tools) if hasattr(self.model, "bind_tools") else self.model
        lc_messages = [(_role(m.get("role", "user")), str(m.get("content", ""))) for m in messages]
        response = bound.invoke(lc_messages)
        self._accumulate_usage(response)
        for tc in _tool_calls(response):
            name, args = _call_name_args(tc)
            if name:
                return ModelAction(kind=TOOL_CALL, tool=name, args=args)
        return ModelAction(kind=FINAL, text=str(getattr(response, "content", "") or ""))

    def usage(self) -> dict[str, int]:
        return {"input_tokens": self._tokens_in, "output_tokens": self._tokens_out}

    def _accumulate_usage(self, response: object) -> None:
        meta = getattr(response, "usage_metadata", None)
        if isinstance(meta, dict):
            self._tokens_in += int(meta.get("input_tokens", 0) or 0)
            self._tokens_out += int(meta.get("output_tokens", 0) or 0)


def _role(role: object) -> str:
    r = str(role)
    return {"assistant": "ai", "model": "ai", "tool": "tool", "system": "system"}.get(r, "human")


def _tool_calls(response: object) -> list[object]:
    tcs = getattr(response, "tool_calls", None)
    return list(tcs) if isinstance(tcs, list) else []


def _call_name_args(tc: object) -> tuple[str, dict[str, object]]:
    # LangChain standard tool_call is a dict {name, args, id}; be tolerant of an
    # object form (.name / .args) too
    if isinstance(tc, dict):
        return str(tc.get("name", "")), dict(tc.get("args", {}) or {})
    name = getattr(tc, "name", "")
    args = getattr(tc, "args", {}) or {}
    return str(name), dict(args)
