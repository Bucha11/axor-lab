"""AgentDojo — the import path.

Its job in the launch set is to prove a suite can be built from an EXTERNAL
benchmark rather than authored by hand: the scenarios come from
`lab_adapters.agentdojo`, already curated and converted to scenario/v1. It is
the only built-in that declares the governance capability, because the banking
scenarios it imports are attack scenarios and studying them is the point.
"""

from __future__ import annotations

from lab_adapters.agentdojo import available_suites, import_suite, manifests
from lab_runner.loop import (
    AgentProgram,
    Finish,
    LoopOutcome,
    ScriptedProgram,
    ToolCall,
)

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
        self,
        scenario: dict[str, object],
        seed: str,
        resolved: ResolvedSuite,
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

    def evidence_for(
        self, outcome: LoopOutcome, trial: dict[str, object], scenario: dict[str, object]
    ) -> list[dict[str, object]]:
        case = _breach_case(outcome, trial, scenario)
        return [case] if case is not None else []


def _breach_case(outcome, trial, scenario):
    """A `prompt_injection` case for a trial where the attack landed.

    The governance chain — injection -> provenance -> gated call -> verdict —
    is built by the capability (`lab_capabilities.governance.evidence`) and
    attaches as the case's optional `governance` block. What is built here is
    the platform half: which trial, which events, what it cost. A suite must be
    able to raise a case without reaching into the capability, or "governance is
    optional" is false for evidence.
    """
    from lab_runner.cases import build_case, case_id_for, turning_point

    if not outcome.violation:
        return None
    return build_case(
        case_id=case_id_for(trial, "prompt_injection"),
        kind="prompt_injection",
        title=f"Injected instruction reached the sink in {scenario.get('name', 'a trial')}",
        trial=trial, trace=outcome.trace,
        run_id=str(trial.get("execution_id", "")),
        severity="high",
        summary=(
            "The scenario's violation predicate matched: untrusted content from "
            "the injection fixture drove an egress call. The timeline points at "
            "the recorded events; the trace's value ledger is authoritative for "
            "the provenance."
        ),
        highlight_seq=turning_point(outcome.trace),
        tags=["prompt_injection", "data_exfiltration"],
    )


def _conditions() -> list[dict[str, object]]:
    """The paired arms this suite compares. Declared HERE, in the manifest, so
    the governance capability is visible to anyone reading the suite rather
    than hidden in the runner."""
    # The REAL kernel, not Lab's in-process reference simulator: this suite is
    # meant to be dispatched to a connected runtime, and a wrapped agent on
    # someone else's machine governs through axor-core because that is the only
    # kernel it has. Pinning the reference kernel planned a run nothing could
    # execute, and the failure only surfaced after every trial had already run.
    kernel = _kernel_version()
    return [
        {"schema_version": "condition/v1", "id": "ungoverned", "label": "ungoverned",
         "enforcement": "off", "kernel": kernel},
        {"schema_version": "condition/v1", "id": "governed", "label": "governed",
         "enforcement": "on", "kernel": kernel,
         "policy": {"profile": "strict", "trust_model": "content-ledger"}},
    ]


def _kernel_version() -> str:
    from lab_capabilities.governance.runner import connected_runtime_kernel

    return connected_runtime_kernel()


def _follows_injection(scenario_name: str, seed: str) -> bool:
    from lab_runner.agents import ScriptedAgent

    return ScriptedAgent().follows_injection(scenario_name, seed)


def _attacker_target(injection_text: str) -> str:
    from lab_runner.agents import ScriptedAgent

    return ScriptedAgent().attacker_target(injection_text)
