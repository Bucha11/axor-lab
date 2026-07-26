"""Gate a SEQUENCE of tool calls, not a single read → sink pair.

`run_trial` executes exactly one read tool and exactly one sink, which is the
shape of the curated banking slice and of nothing else. Measured over
`agentdojo==0.1.35`, ground-truth call sequences run to 5 calls in banking and
workspace, 9 in slack and 18 in travel — so travel was one task out of twenty
representable, and "travel produces zero denials", the sharpest structural check
the reference offers, could not even be attempted.

This module runs a whole sequence through one governor, in order, with
provenance accumulating between calls: an untrusted read registers its result,
and every later sink is judged against everything registered before it. That
ordering is the substance — a value is untrusted because of what was read
*earlier in this trial*, so a per-call gate with no memory would answer a
different question.

The governor is the real `axor_core.governor.ToolCallGovernor`. Nothing here
reimplements a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .axor_backend import HAS_AXOR_CORE, governor_config
from .errors import UnknownKernelError


@dataclass(frozen=True)
class CallVerdict:
    """One gated call, with enough to explain the verdict without re-running."""

    index: int
    tool: str
    verdict: str          # ALLOW | DENY
    gate: str | None      # the gate that denied, when one did
    reason: str
    is_sink: bool
    is_untrusted_source: bool


@dataclass(frozen=True)
class SequenceResult:
    denials: tuple[CallVerdict, ...]
    calls: tuple[CallVerdict, ...]

    @property
    def denied(self) -> bool:
        return bool(self.denials)

    def denials_by_gate(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for call in self.denials:
            counts[call.gate or "unknown"] = counts.get(call.gate or "unknown", 0) + 1
        return counts


def _manifests_for(taxonomy: Any, tools: list[str]) -> dict[str, dict[str, object]]:
    """Synthesize Lab tool manifests from the declared taxonomy.

    The taxonomy is the operator's statement about the tools; a manifest is how
    Lab's config compiler consumes it. Building them here keeps ONE source of
    truth — editing the taxonomy changes the governed run, with no second list
    to keep in step.
    """
    manifests: dict[str, dict[str, object]] = {}
    for tool in tools:
        effect: dict[str, object] = {"default_class": "BENIGN", "driving_args": []}
        if tool in taxonomy.egress_sinks:
            effect = {
                "default_class": "EXPORT",
                "driving_args": list(taxonomy.egress_sinks[tool]),
            }
        elif tool in taxonomy.consequence:
            effect = {"default_class": taxonomy.consequence[tool], "driving_args": []}
        manifest: dict[str, object] = {
            "schema_version": "tool-manifest/v1",
            "id": tool,
            "args_schema": {"type": "object", "properties": {}},
            "result_schema": {"type": "object"},
            "effect": effect,
        }
        if tool in taxonomy.untrusted_sources:
            # the whole result of an untrusted source is attacker-reachable: the
            # injection vectors land inside these payloads and which field
            # carries one differs per vector, so naming a single path would
            # under-declare the surface. The key is `untrusted_fields` — the
            # name the config compiler reads (lab_contracts/canonical.py).
            manifest["untrusted_fields"] = ["result"]
        manifests[tool] = manifest
    return manifests


def gate_sequence(
    calls: list[dict[str, Any]],
    taxonomy: Any,
    tools: list[str],
    policy: dict[str, object] | None = None,
    inputs: dict[str, object] | None = None,
    injected: list[str] | None = None,
    confidentiality: bool = False,
) -> SequenceResult:
    """Run `calls` through one governor in order and return every verdict.

    `calls` are `{tool, args, result}` records as frozen by
    `scripts/extract_agentdojo.py` — the benchmark's own ground truth, with the
    results the real tools produced. The results are what makes a content-ledger
    verdict reproducible: taint is derived by matching a sink argument against
    what an untrusted read actually returned.

    `injected` is the attack payload as it sits in the environment. Every
    untrusted read registers it alongside its own result, because that is where
    it physically is — inside the data the read returns. Omitting it is not a
    smaller experiment, it is a different one: with no attack text in any read,
    the attacker's destination is untainted and every suite reports ASR 100%.

    A denied call still advances the sequence. The reference counts denials per
    pass, so stopping at the first one would under-report every multi-sink task;
    and a real deployment does not end the session because one call was refused.
    """
    if not HAS_AXOR_CORE:
        raise UnknownKernelError(
            "axor-core is not installed; the AgentDojo calibration runs the real governor"
        )
    from axor_core.governor import ToolCallGovernor

    manifests = _manifests_for(taxonomy, tools)
    config = governor_config(manifests, policy, inputs)
    # The confidentiality axis, declared separately from integrity. It is passed
    # here rather than compiled into the canonical config because
    # `compiled_governor_config` does not yet carry a sensitive-source field —
    # see contracts/agentdojo-calibration.md §10. Until it does, this
    # declaration is NOT covered by `executable_config_hash`, which is a real
    # gap and is named as one rather than left to be discovered.
    # OFF by default, because the reference's per-suite numbers are an
    # INTEGRITY-only measurement: it states that the confidentiality axis "was
    # never engaged" in those runs and carries no row for it. Arming the floor
    # by default would compare our two-axis run against their one-axis one and
    # call the difference a mismatch.
    sensitive = getattr(taxonomy, "sensitive_sources", frozenset()) if confidentiality else ()
    if sensitive:
        config["sensitive_sources"] = set(sensitive)
    governor = ToolCallGovernor(**config)

    verdicts: list[CallVerdict] = []
    for index, call in enumerate(calls):
        tool = str(call["tool"])
        args = dict(call.get("args") or {})
        is_sink = tool in taxonomy.egress_sinks
        is_source = tool in taxonomy.untrusted_sources
        decision = governor.evaluate(tool, args)
        if decision.allowed:
            if is_source:
                # what this read returned is now attacker-reachable content; a
                # later sink argument matching it is untrusted-derived
                governor.register_output(decision, call.get("result"))
                for payload in injected or ():
                    governor.register_output(decision, payload)
            verdicts.append(CallVerdict(
                index=index, tool=tool, verdict="ALLOW", gate=None,
                reason="axor-core governor: allowed",
                is_sink=is_sink, is_untrusted_source=is_source,
            ))
        else:
            verdicts.append(CallVerdict(
                index=index, tool=tool, verdict="DENY",
                gate=str(decision.category), reason=str(decision.reason),
                is_sink=is_sink, is_untrusted_source=is_source,
            ))
    return SequenceResult(
        calls=tuple(verdicts),
        denials=tuple(v for v in verdicts if v.verdict == "DENY"),
    )
