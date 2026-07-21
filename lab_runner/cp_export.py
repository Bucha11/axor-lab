"""Control Plane export — the earned bridge (plan B2, control-plane-handoff.md).

What carries over (reused, identical): the validated policy + `config_hash`,
the tool manifests, and any pinned regression cases. What must be ADDED for
production (the honest half, NOT reused): real tool bindings, credentials,
deployment topology, notifications/owners. This module emits the first as an
`axor-cp-deploy/v1` config and the second as a `production-todo.md`, so the
handoff never reads as "nothing is re-done."

The `config_hash` is the carry-over KEY: it is emitted byte-identical to the
RECORDED hash on the condition the researcher measured (never synthesized here),
so the Control Plane governs under exactly the config Lab validated. Regression
pins are validated against the bundle's own traces before they carry over — a
pin for a trace this bundle never contained, or one asserting a verdict sequence
the frozen trace never produced, is rejected rather than shipped as a CP test.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts import condition_config_hash, content_hash, executable_config_hash

_VALID_VERDICTS = frozenset({"ALLOW", "DENY"})

# the bridge is EARNED only when the delta is large enough and powered enough to
# mean something — a 1-vs-1 run whose baseline happens to be higher is not a
# production signal (review r15). Overridable, but never off by default.
_MIN_EFFECT_DELTA = 0.10
_MIN_EFFECTIVE_N = 20
# reject a lopsided partial run: if one arm has far fewer completed trials than
# the other, the comparison is not a fair matched contrast
_MIN_ARM_BALANCE = 0.5  # min(base_n, treated_n) / max(...) must be >= this

# The four categories that are deliberately NOT reused (control-plane-handoff.md).
PRODUCTION_TODO = (
    ("tool_bindings", "Lab ran simulators/fixtures; production calls real tools."),
    ("credentials", "Secrets/vault (per the CP federation vault) — absent in Lab."),
    ("topology", "Deployment topology (single agent vs federation, where nodes run)."),
    ("operations", "Notifications, owners, failure policy — operational, absent in Lab."),
)

EXPORT_SCHEMA = "axor-cp-deploy/v1"


class CPExportError(Exception):
    """The bundle cannot be exported to a Control Plane config."""


@dataclass(frozen=True)
class CPExport:
    config: dict[str, object]
    production_todo: str
    earned_bridge: bool


def export_cp(
    bundle: dict[str, object],
    regressions: list[dict[str, object]] | None = None,
    condition_id: str | None = None,
    traces: dict[str, dict[str, object]] | None = None,
) -> CPExport:
    """Build the CP deploy config + production-todo from a verified bundle.

    The condition to deploy is chosen EXPLICITLY when several enforce, so the
    exported policy is provably the one whose aggregate showed the delta — not
    just the first enforcement-on condition (which could differ from the one the
    earned bridge measured, deploying a config that never changed the outcome)."""
    governed = _select_condition(bundle, condition_id)
    policy: dict[str, object] = governed.get("policy", {})  # type: ignore[assignment]
    kernel = str(governed["kernel"])
    # The carry-over key must be a RECORDED measurement, not a value we
    # synthesize here and then present as "the config the researcher measured".
    # A condition with no config_hash means the bundle never fingerprinted the
    # config it ran under — exporting a freshly-computed hash would fabricate the
    # provenance the whole handoff rests on (review r12).
    recorded_raw = governed.get("config_hash")
    if recorded_raw is None:
        raise CPExportError(
            f"condition {governed['id']!r} has no recorded config_hash; refusing to "
            "synthesize the carry-over key and present it as the measured config"
        )
    recomputed = condition_config_hash(kernel, policy)
    recorded = str(recorded_raw)
    if recorded != recomputed:
        raise CPExportError(
            f"condition config_hash {recorded} does not match its policy+kernel "
            f"({recomputed}); refusing to export a config the researcher did not measure"
        )

    carried_pins = _validate_pins(bundle, regressions or [], traces or {})
    baseline_id = _baseline_condition_id(bundle)
    # the bridge is EARNED from RECOMPUTED evidence, not stored aggregates — so it
    # needs the traces. Without them it cannot be verified and is not earned (r16).
    earned, supporting = _earned_for(bundle, str(governed["id"]), baseline_id, traces or {})
    # the FULL executable-config identity — kernel + policy + the manifests whose
    # effect classes / driving args change the governor's verdicts. This is the
    # honest carry-over key: the plain config_hash (kernel+policy only) does not
    # capture the manifests the governor executes over (review r15).
    manifests: list[dict[str, object]] = bundle["tool_manifests"]  # type: ignore[assignment]
    exec_hash = executable_config_hash(kernel, policy, manifests)
    config: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA,
        "kernel": kernel,
        "policy": policy,
        "config_hash": recorded,  # the recorded kernel+policy fingerprint
        "executable_config_hash": exec_hash,  # the FULL carry-over key (+ manifests)
        "tool_manifests": bundle["tool_manifests"],
        "regressions": carried_pins,
        "source": {
            "bundle_id": bundle.get("bundle_id"),
            "condition_id": governed["id"],
            # the exact evidence this deploy config claims — the baseline it beat
            # and the two aggregates that show the delta (empty if none)
            "baseline_condition_id": baseline_id,
            "supporting_aggregate_refs": list(supporting),
        },
    }
    return CPExport(
        config=config,
        production_todo=_render_todo(),
        earned_bridge=earned,
    )


def _validate_pins(
    bundle: dict[str, object],
    regressions: list[dict[str, object]],
    traces: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Every carried regression pin must bind to a REAL trace in this bundle.

    The old export copied {trace_id, expected_verdict} verbatim from whatever pin
    file it was handed — so a pin for a trace this bundle never contained, or one
    whose claimed verdict sequence the frozen trace never produced, would ride
    into the production Control Plane config as a governance regression test that
    can never actually run (or, worse, one asserting the wrong expected outcome).
    Each pin is now validated against the evidence (review r12):
      - trace_id names a trace in the bundle;
      - trace_ref (if present) equals that trace's content hash — no stale/tampered pin;
      - the trace is cited by a completed trial (so scenario/condition resolve);
      - expected_verdict is a real verdict;
      - expected_sequence (if present) equals the trace's recorded gate verdicts.
    The carried pin records the FULL validated shape (ref, ordered sequence,
    scenario, condition) so the CP re-runs exactly the pinned incident."""
    if not regressions:
        return []
    if not traces:
        raise CPExportError(
            "regression pins were supplied but no bundle traces to validate them "
            "against — refusing to carry unvalidated pins into a production config"
        )
    by_id = {str(t["trace_id"]): t for t in traces.values()}
    trials_by_ref: dict[str, dict[str, object]] = {}
    for trial in bundle["trials"]:  # type: ignore[union-attr]
        if trial.get("status") == "completed":
            trials_by_ref[str(trial.get("trace_ref"))] = trial
    carried: list[dict[str, object]] = []
    for i, raw in enumerate(regressions):
        if not isinstance(raw, dict):
            raise CPExportError(f"regression pin #{i} is not an object")
        tid = str(raw.get("trace_id", ""))
        if not tid:
            raise CPExportError(f"regression pin #{i} has no trace_id")
        verdict = str(raw.get("expected_verdict", ""))
        if verdict not in _VALID_VERDICTS:
            raise CPExportError(
                f"pin {tid!r}: expected_verdict {verdict!r} is not one of {sorted(_VALID_VERDICTS)}"
            )
        trace = by_id.get(tid)
        if trace is None:
            raise CPExportError(
                f"pin {tid!r}: no such trace in the bundle — refusing to carry a pin "
                "for a trace this bundle does not contain"
            )
        actual_ref = content_hash(trace)
        if "trace_ref" in raw and str(raw["trace_ref"]) != actual_ref:
            raise CPExportError(
                f"pin {tid!r}: trace_ref does not match the trace's content hash "
                "(stale or tampered pin)"
            )
        trial = trials_by_ref.get(actual_ref)
        if trial is None:
            raise CPExportError(
                f"pin {tid!r}: trace is not cited by any completed trial (orphan pin)"
            )
        recorded_sequence = [
            str(e["decision"]["verdict"]) for e in trace["events"]  # type: ignore[index,union-attr]
            if e.get("type") == "gate_decision"
        ]
        # a regression pin must have at least one recorded gate decision to
        # regress against — a trace with no decision cannot verify any verdict, so
        # carrying it as a CP test would ship a check that can never run (review r15)
        if not recorded_sequence:
            raise CPExportError(
                f"pin {tid!r}: the trace has no recorded gate decision to regress against"
            )
        if "expected_sequence" in raw:
            claimed = [str(v) for v in raw["expected_sequence"]]  # type: ignore[union-attr]
            if claimed != recorded_sequence:
                raise CPExportError(
                    f"pin {tid!r}: expected_sequence {claimed} does not match the frozen "
                    f"trace's recorded verdicts {recorded_sequence}"
                )
        # the headline expected_verdict must equal the final recorded verdict —
        # a pin asserting ALLOW over a trace whose sequence ends in DENY is
        # internally contradictory and must not become a CP regression (r13)
        if recorded_sequence and verdict != recorded_sequence[-1]:
            raise CPExportError(
                f"pin {tid!r}: expected_verdict {verdict!r} contradicts the trace's final "
                f"recorded verdict {recorded_sequence[-1]!r}"
            )
        carried.append({
            "trace_id": tid,
            "trace_ref": actual_ref,
            "expected_verdict": verdict,
            "expected_sequence": recorded_sequence,
            "scenario_id": str(trial.get("scenario_id")),
            "condition_id": str(trial.get("condition_id")),
        })
    return carried


def earned_bridge(
    bundle: dict[str, object],
    condition_id: str | None = None,
    traces: dict[str, dict[str, object]] | None = None,
) -> bool:
    """The bridge is EARNED from RECOMPUTED evidence: true only when governance
    changed the outcome by a meaningful, powered, statistically-separated margin,
    computed from the TRACES — not from uploaded aggregates a hand-built bundle
    could fabricate (review r16). Without traces it cannot be verified → False.

    The baseline is the enforcement-off condition (by role, not the literal id
    'ungoverned'). With `condition_id`, only that condition is checked."""
    baseline_id = _baseline_condition_id(bundle)
    if baseline_id is None or not traces:
        return False
    if condition_id is not None:
        return _earned_for(bundle, condition_id, baseline_id, traces)[0]
    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        if condition["enforcement"] == "on" and _earned_for(
            bundle, str(condition["id"]), baseline_id, traces
        )[0]:
            return True
    return False


def _recompute_asr(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> dict[str, tuple[int, int]]:
    """Per condition → (violations, completed_n) recomputed from the traces via the
    scenario's own `violation` predicate — the same evidence a reader recomputes,
    NOT the uploaded aggregate. Fabricated hash-consistent aggregates are
    therefore irrelevant to the bridge (review r16)."""
    from lab_contracts import content_hash
    from lab_runner import evaluate

    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    by_hash = {content_hash(t): t for t in traces.values()}
    counts: dict[str, tuple[int, int]] = {}
    for trial in bundle.get("trials", []):  # type: ignore[union-attr]
        if trial.get("status") != "completed":
            continue
        trace = by_hash.get(str(trial.get("trace_ref")))
        scenario = scenarios.get(str(trial.get("scenario_id")))
        if trace is None or scenario is None:
            continue
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        violated = bool(evaluate(scenario["violation"], trace, inputs))  # type: ignore[arg-type]
        cid = str(trial["condition_id"])
        v, n = counts.get(cid, (0, 0))
        counts[cid] = (v + (1 if violated else 0), n + 1)
    return counts


def _earned_for(
    bundle: dict[str, object], condition_id: str, baseline_id: str | None,
    traces: dict[str, dict[str, object]],
) -> tuple[bool, tuple[str, ...]]:
    """Did `condition_id` beat the baseline on ASR by a MEANINGFUL, POWERED, and
    STATISTICALLY SEPARATED margin, computed from the TRACES? Returns (earned,
    refs).

    A lower stored estimate is not enough (review r15/r16): the estimates are
    RECOMPUTED from the traces (fabricated aggregates cannot earn); both arms need
    a minimum effective n and must be balanced; the delta must clear a minimum
    effect size; AND the difference's 95% interval must exclude zero (the
    two-proportion Newcombe interval — an overlapping interval is not a production
    signal). A 1-vs-1 run, an unbalanced partial run, or a delta whose CI includes
    zero does NOT earn the bridge."""
    if baseline_id is None or not traces:
        return False, ()
    recomputed = _recompute_asr(bundle, traces)
    base_v, base_n = recomputed.get(baseline_id, (0, 0))
    treated_v, treated_n = recomputed.get(condition_id, (0, 0))
    if base_n < _MIN_EFFECTIVE_N or treated_n < _MIN_EFFECTIVE_N:
        return False, ()
    if min(base_n, treated_n) / max(base_n, treated_n, 1) < _MIN_ARM_BALANCE:
        return False, ()
    delta = base_v / base_n - treated_v / treated_n
    if delta < _MIN_EFFECT_DELTA:
        return False, ()
    # statistical separation: the recomputed difference's 95% interval must
    # exclude zero (governance really moved the outcome, not noise)
    from lab_analysis import two_proportion_test

    test = two_proportion_test(base_v, base_n, treated_v, treated_n, vs=baseline_id)
    interval: dict[str, object] = test["interval"]  # type: ignore[assignment]
    if float(interval["low"]) <= 0.0:  # type: ignore[arg-type]
        return False, ()
    return True, (f"agg:ASR:{baseline_id}", f"agg:ASR:{condition_id}")


def _baseline_condition_id(bundle: dict[str, object]) -> str | None:
    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        if condition["enforcement"] == "off":
            return str(condition["id"])
    return None


def _select_condition(bundle: dict[str, object], condition_id: str | None) -> dict[str, object]:
    """Choose the enforcing condition to deploy — explicitly when several exist."""
    conditions: list[dict[str, object]] = list(bundle["conditions"])  # type: ignore[arg-type]
    enforcing = [c for c in conditions if c["enforcement"] == "on"]
    if not enforcing:
        raise CPExportError("bundle has no enforcement-on condition to carry over")
    if condition_id is not None:
        for condition in enforcing:
            if str(condition["id"]) == condition_id:
                return condition
        if any(str(c["id"]) == condition_id for c in conditions):
            raise CPExportError(f"condition {condition_id!r} is not enforcement-on")
        raise CPExportError(f"condition {condition_id!r} is not in the bundle")
    if len(enforcing) > 1:
        ids = ", ".join(sorted(str(c["id"]) for c in enforcing))
        raise CPExportError(
            f"bundle has multiple enforcing conditions ({ids}); "
            "pass a condition to choose which one to export"
        )
    return enforcing[0]


def _render_todo() -> str:
    lines = [
        "# Production deployment — what Lab does NOT carry over",
        "",
        "Reuse the same validated policy and tool manifest (in `cp-deploy.json`);",
        "add the following before governing production traffic:",
        "",
    ]
    for key, description in PRODUCTION_TODO:
        lines.append(f"- **{key}** — {description}")
    lines.append("")
    lines.append(
        "The exported `config_hash` is the carry-over key: the Control Plane "
        "governs under exactly the config measured in Lab."
    )
    return "\n".join(lines) + "\n"
