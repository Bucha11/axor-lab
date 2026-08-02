"""AgentDojo — the import path.

Its job in the launch set is to prove a suite can be built from an EXTERNAL
benchmark rather than authored by hand: the scenarios come from
`lab_adapters.agentdojo`, already curated and converted to scenario/v1. It is
the only built-in that declares the governance capability, because the banking
scenarios it imports are attack scenarios and studying them is the point.
"""

from __future__ import annotations

from lab_adapters.agentdojo import available_suites, import_suite, manifests
from lab_runner.loop import AgentProgram, Finish, ScriptedProgram, ToolCall

from ..manifest import ResolvedSuite
from ..sdk import BaseSuite


class AgentDojoSuite(BaseSuite):
    id = "agentdojo"

    def __init__(self, suite: str | None = None) -> None:
        self._suite = suite or available_suites()[0]

    def manifest(self) -> dict[str, object]:
        scenarios = import_suite(self._suite)
        tool_manifests = list(manifests().values())
        return {
            "schema_version": "suite/v1",
            "id": self.id,
            "name": "AgentDojo",
            "version": "1.0",
            "description": "General agent capability evaluation",
            "origin": "built_in",
            "capabilities": ["governance"],
            "agents": [{"ref": "scripted@0.6"}],
            "topology": {"kind": "single"},
            "scenarios": list(scenarios),
            "environment": {
                "tools": tool_manifests,
                "simulation": {"enabled": True, "strict_manifest": True},
            },
            "execution": {"strategy": "matrix", "repeats": 6,
                          "seed_policy": "per_repeat",
                          "conditions": _conditions()},
            "evaluation": {
                "metrics": [
                    {"name": "task_success", "label": "Task Success", "kind": "boolean",
                     "source": "trial_metric", "from": "task_success",
                     "direction": "higher_is_better"},
                    {"name": "ASR", "label": "Attack Success Rate", "kind": "boolean",
                     "source": "trial_metric", "from": "ASR",
                     "direction": "lower_is_better"},
                ],
                # No `evaluators` block: each scenario already declares the typed
                # `task_success` and `violation` predicates, and THOSE are this
                # suite's evaluation. Restating them here would be two sources of
                # truth for one judgement.
                "aggregations": [
                    {"metric": "ASR", "fn": "rate", "unit_of_analysis": "trial",
                     "interval": "wilson", "test": "mcnemar"},
                    {"metric": "task_success", "fn": "rate", "unit_of_analysis": "trial",
                     "interval": "wilson"},
                ],
            },
            "artifact": {"include_traces": True,
                         "sections": ["overview", "metrics", "scenarios", "failures",
                                      "evidence", "artifacts"]},
            "tags": ["benchmark", "security"],
        }

    def program_for(
        self, scenario: dict[str, object], seed: str, resolved: ResolvedSuite
    ) -> AgentProgram:
        """A deterministic stand-in for the model: the seed decides whether the
        agent follows the injection, which is what produces the discordant pairs
        a matched-pairs comparison needs."""
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        injection: dict[str, object] = scenario.get("injection") or {}  # type: ignore[assignment]
        follows = _follows_injection(str(scenario["name"]), seed)
        recipient = (
            _attacker_target(str(injection.get("text", "")))
            if follows and injection else inputs.get("landlord_iban", "")
        )
        return ScriptedProgram([
            ToolCall("read_txns", {}),
            ToolCall("send_money", {"recipient": recipient,
                                    "amount": inputs.get("amount", 1200)}),
            Finish(),
        ])


def _conditions() -> list[dict[str, object]]:
    """The paired arms this suite compares. Declared HERE, in the manifest, so
    the governance capability is visible to anyone reading the suite rather
    than hidden in the runner."""
    return [
        {"schema_version": "condition/v1", "id": "ungoverned", "label": "ungoverned",
         "enforcement": "off", "kernel": "reference_taint_floor_kernel"},
        {"schema_version": "condition/v1", "id": "governed", "label": "governed",
         "enforcement": "on", "kernel": "reference_taint_floor_kernel",
         "policy": {"profile": "strict", "trust_model": "content-ledger"}},
    ]


def _follows_injection(scenario_name: str, seed: str) -> bool:
    from lab_runner.agents import ScriptedAgent

    return ScriptedAgent().follows_injection(scenario_name, seed)


def _attacker_target(injection_text: str) -> str:
    from lab_runner.agents import ScriptedAgent

    return ScriptedAgent().attacker_target(injection_text)
