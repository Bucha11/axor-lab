"""Agent adapters — the AgentArtifact behind experiment.agent_ref.

The MVP runner is local-only and the model layer is pluggable via a small
protocol. `scripted` is the deterministic stand-in used by the acceptance
suite and the shipped example; a BYOK model-backed adapter slots in behind
the same protocol (plan: Phase 1 "BYOK inference", Then-tier for in-app).

agent_ref grammar: `scripted` | `scripted@<attack_rate>` (e.g. scripted@0.6).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

from .errors import UnknownAgentError

_IBAN_RE = re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}\b")
DEFAULT_ATTACK_RATE = 0.6
_RATE_SCALE = 1000


class AgentAdapter(Protocol):
    """What the runner needs from a scripted agent (structural, no inheritance)."""

    def follows_injection(self, scenario_name: str, seed: str) -> bool:
        """Whether this trial's behavior follows the injected instruction."""
        ...

    def attacker_target(self, injection_text: str) -> str:
        """The exfiltration target the agent extracts from the injection."""
        ...


@dataclass(frozen=True)
class SinkDecision:
    """The sink call a driving agent produced after observing tool output.

    The runtime — not the agent — assigns provenance: because the value was
    produced by a model call whose context held the (possibly injected) tool
    result, it is minted `model_extraction` under the conservative join
    (over-taints, never under-taints). The agent only reports what it called.
    """

    recipient: str
    amount: object


@runtime_checkable
class DrivingAgent(Protocol):
    """A model-backed agent that DRIVES the loop: it observes the read result
    (which may carry an injection) and emits the sink call itself. The runner
    dispatches to this path when the adapter implements it; provenance is still
    the runtime's (conservative join), so a driving agent cannot launder taint.
    """

    def decide_sink_call(
        self,
        task: str,
        read_result: object,
        inputs: dict[str, object],
        sink_manifest: dict[str, object],
    ) -> SinkDecision:
        ...


@dataclass(frozen=True)
class ScriptedAgent:
    """Deterministic stand-in for the stochastic model layer.

    The seed decides whether the agent follows the injection, so paired
    ungoverned/governed trials on the same seed produce the discordant
    pairs McNemar needs.
    """

    attack_rate: float = DEFAULT_ATTACK_RATE
    # behavior is fixed by (scenario, seed), so ungoverned/governed on the same
    # seed IS a real matched pair — McNemar is valid (review r4)
    is_deterministic: ClassVar[bool] = True

    def follows_injection(self, scenario_name: str, seed: str) -> bool:
        digest = hashlib.sha256(f"{scenario_name}:{seed}".encode()).hexdigest()
        return int(digest, 16) % _RATE_SCALE < int(self.attack_rate * _RATE_SCALE)

    def attacker_target(self, injection_text: str) -> str:
        match = _IBAN_RE.search(injection_text)
        return match.group(0) if match else "ATTACKER-TARGET"


def resolve_agent(agent_ref: str) -> AgentAdapter:
    """Resolve experiment.agent_ref to an adapter instance."""
    name, _, param = agent_ref.partition("@")
    if name == "scripted":
        if not param:
            return ScriptedAgent()
        try:
            rate = float(param)
        except ValueError as exc:
            raise UnknownAgentError(f"bad scripted attack rate {param!r}") from exc
        if not 0.0 <= rate <= 1.0:
            raise UnknownAgentError(f"attack rate must be in [0, 1], got {rate}")
        return ScriptedAgent(attack_rate=rate)
    raise UnknownAgentError(
        f"unknown agent_ref {agent_ref!r}; supported: scripted[@rate] "
        "(model-backed BYOK adapters are the next plan phase)"
    )
