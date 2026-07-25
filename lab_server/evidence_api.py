"""The EvidenceCase for a run's trace — before it is published, or ever.

`EvidenceView` reads a PUBLISHED bundle, so investigating your own run meant
publishing it first or dropping to `axor-lab evidence`. That is backwards:
investigation is what you do BEFORE deciding something is worth publishing, and
most runs never should be.

This is the same construction the CLI and the published HTML page both use —
`build_evidence_case` over the trace's own scenario, the condition
`evidence_condition` resolves, and the kernel `resolve_kernel` returns for that
condition. Nothing about the case is weaker for being unpublished; only its
audience is.
"""
from __future__ import annotations

from typing import Any


class EvidenceRefused(ValueError):
    """The EvidenceCase cannot be built. Carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _scenario_for(bundle: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    scenario_id = str(trace["trial"]["scenario_id"])
    for scenario in bundle["scenarios"]:
        if scenario["name"] == scenario_id:
            return scenario
    raise EvidenceRefused(422, f"scenario {scenario_id!r} is not in the bundle")


def build_case(
    bundle: dict[str, Any],
    traces: dict[str, Any],
    trace_id: str,
    twin_id: str | None = None,
    policy: str | None = None,
) -> dict[str, Any]:
    """Build the EvidenceCase for `trace_id` within `bundle`."""
    from lab_runner import default_registry
    from lab_runner.axor_backend import resolve_kernel
    from lab_runner.errors import RunnerError
    from lab_runner.evidence import build_evidence_case, evidence_condition, validate_twin
    from lab_runner.kernel import UnknownKernelError

    trace = traces.get(trace_id)
    if trace is None:
        raise EvidenceRefused(404, f"trace {trace_id!r} is not in this run")

    twin = None
    if twin_id:
        twin = traces.get(twin_id)
        if twin is None:
            raise EvidenceRefused(404, f"twin trace {twin_id!r} is not in this run")
        # A twin has to be the SAME case under an enforcing policy. Any other
        # trace would render a counterfactual about a different situation.
        try:
            validate_twin(trace, twin, bundle)
        except ValueError as exc:
            raise EvidenceRefused(422, str(exc)) from exc

    scenario = _scenario_for(bundle, trace)
    try:
        condition = evidence_condition(bundle, trace, policy)
    except ValueError as exc:
        raise EvidenceRefused(422, str(exc)) from exc

    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}
    version = str(condition["kernel"])
    try:
        # The same resolver replay and regress use: the REAL axor-core governor
        # when the condition pins the installed build. This trace's scenario
        # inputs are threaded through so a real-kernel `$inputs` allowlist expands
        # to concrete values rather than the symbolic ref.
        kernel = resolve_kernel(
            version, manifests, condition.get("policy"),
            default_registry((version,)), scenario.get("inputs", {}),
        )
    except UnknownKernelError as exc:
        raise EvidenceRefused(
            409,
            f"cannot build the case: {exc}. This trace pins a kernel this server "
            "does not have",
        ) from exc

    try:
        return build_evidence_case(
            trace, scenario, condition, kernel, manifests, governed_twin=twin,
        )
    except (RunnerError, ValueError) as exc:
        raise EvidenceRefused(422, f"cannot build the case: {exc}") from exc


def pin_from_run(
    bundle: dict[str, Any],
    traces: dict[str, Any],
    trace_id: str,
    expected: str | None = None,
) -> dict[str, Any]:
    """Pin one of the run's own traces as a regression case.

    Web pinning used to run only off an imported incident, so closing the
    experiment → regression loop required a production incident to exist first,
    or the CLI. A trace you just produced is as pinnable as one that arrived from
    Control Plane.

    The pin records the WHOLE ordered verdict sequence, not just the final
    verdict: a multi-call trace whose real sequence is (ALLOW, ALLOW, DENY)
    compared against a singleton DENY would report a regression on an unchanged
    trace and kernel. `pin()` also refuses an expected verdict that contradicts
    what the trace actually recorded.
    """
    from lab_runner.regression import pin as make_pin

    trace = traces.get(trace_id)
    if trace is None:
        raise EvidenceRefused(404, f"trace {trace_id!r} is not in this run")
    if expected is None:
        # default to what the trace actually did — pinning "keep behaving as you
        # did here" is the common case and needs no restating
        verdicts = [
            str(e["decision"]["verdict"])
            for e in trace.get("events", [])
            if e.get("type") == "gate_decision"
        ]
        if not verdicts:
            raise EvidenceRefused(
                422, f"trace {trace_id!r} records no gate decision to pin"
            )
        expected = verdicts[-1]
    try:
        pinned = make_pin(trace, expected)
    except ValueError as exc:
        raise EvidenceRefused(422, str(exc)) from exc

    trial = trace.get("trial") or {}
    return {
        "pin": {
            "trace_id": pinned.trace_id,
            "trace_ref": pinned.trace_ref,
            "expected_verdict": pinned.expected_verdict,
            "expected_sequence": list(pinned.expected_sequence),
        },
        "scenario_id": str(trial.get("scenario_id", "")),
        "condition_id": str(trial.get("condition_id", "")),
        "kernel": sorted({str(c["kernel"]) for c in bundle.get("conditions", [])}),
    }


def cp_export(
    bundle: dict[str, Any],
    traces: dict[str, Any],
    regressions: list[dict[str, Any]] | None = None,
    condition_id: str | None = None,
) -> dict[str, Any]:
    """Build the Control Plane deploy config from this run — the Lab → CP handoff.

    `export_cp` is a production-handoff boundary and does not trust its caller: it
    re-verifies the whole bundle graph before producing anything, and it requires
    the deployed condition to be named explicitly when several enforce, so the
    exported policy is provably the one whose aggregate showed the delta rather
    than whichever happened to come first.

    What comes back is the config and the production to-do, not a written
    directory — writing the signed, manifest-bound export tree stays with
    `axor-lab export-cp`, where the author's key lives.
    """
    from lab_runner.cp_export import CPExportError, export_cp as build_export

    try:
        export = build_export(
            bundle, list(regressions or []), condition_id=condition_id, traces=traces,
        )
    except CPExportError as exc:
        raise EvidenceRefused(422, str(exc)) from exc
    return {
        "config": export.config,
        "production_todo": export.production_todo,
        # whether the deployed config's advantage is statistically EARNED, not
        # merely observed — the panel must not present the two the same way
        "earned_bridge": export.earned_bridge,
    }


def trace_index(traces: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's traces as a chooser list: which one do you want to look at.

    Carries the verdicts so the interesting traces — the denials — are findable
    without opening each one.
    """
    index = []
    for trace_id in sorted(traces):
        trace = traces[trace_id]
        trial = trace.get("trial") or {}
        verdicts = [
            str(e["decision"]["verdict"])
            for e in trace.get("events", [])
            if e.get("type") == "gate_decision"
        ]
        index.append({
            "trace_id": trace_id,
            "scenario_id": str(trial.get("scenario_id", "")),
            "condition_id": str(trial.get("condition_id", "")),
            "seed": str(trial.get("seed", "")),
            "repeat_index": trial.get("repeat_index"),
            "verdicts": verdicts,
            "denied": "DENY" in verdicts,
        })
    return index
