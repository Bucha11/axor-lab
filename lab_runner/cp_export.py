"""Control Plane export — the earned bridge (plan B2, control-plane-handoff.md).

What carries over (reused, identical): the validated policy, the tool manifests,
and any pinned regression cases. What must be ADDED for production (the honest
half, NOT reused): real tool bindings, credentials, deployment topology,
notifications/owners. This module emits the first as an `axor-cp-deploy/v1`
config and the second as a `production-todo.md`, so the handoff never reads as
"nothing is re-done."

The `parametric_config_hash` is the carry-over KEY: it fingerprints the kernel +
policy with symbolic `$inputs`, so it stays identical when the Control Plane
re-parameterizes the SAME policy with production inputs. The `config_hash` is the
RECORDED fingerprint of the CONCRETE config the measured condition ran under
(emitted byte-identical, never synthesized here) and `runtime_config_hashes` are
its per-scenario concrete fingerprints — both describe the specific Lab run, not
what production will hash, so they anchor provenance rather than carry over (r17/r18).
Regression pins are validated against the bundle's own traces before they carry
over — a pin for a trace this bundle never contained, or one asserting a verdict
sequence the frozen trace never produced, is rejected rather than shipped as a CP test.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts import (
    CONFIG_COMPILER_VERSION,
    condition_config_hash,
    content_hash,
    parametric_policy_hash,
    runtime_config_hash,
    validate_artifact,
    verify_bundle,
)
from lab_contracts.errors import BundleIntegrityError

_VALID_VERDICTS = frozenset({"ALLOW", "DENY"})

# the bridge is EARNED only when the delta is large enough and powered enough to
# mean something — a 1-vs-1 run whose baseline happens to be higher is not a
# production signal (review r15). Overridable, but never off by default.
_MIN_EFFECT_DELTA = 0.10
_MIN_EFFECTIVE_N = 20
# the independent-samples arms must match EXACTLY per scenario (review r20), so a
# loose ratio threshold is no longer used; matched contrasts bound their unpaired
# fraction below.
# a matched contrast whose arms overlap on too few of their combined experimental
# units is a composition contrast, not a governance one: reject when more than
# this fraction of the union coordinates are unpaired (review r19)
_MAX_DROPPED_PAIR_FRACTION = 0.25

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
    earned bridge measured, deploying a config that never changed the outcome).

    export_cp is a PRODUCTION HANDOFF boundary and does not trust the caller's
    discipline (review r18): it re-runs the full bundle graph verification
    (schema + Trial<->Trace binding + trace-metadata) before anything, so a
    schema-valid, hash-resolving, but graph-invalid bundle (a trial citing a trace
    whose own coordinates disagree) is refused rather than silently exported.

    The COMPLETE traces are MANDATORY (review r19): they are the evidence the graph
    verification, the earned bridge, and the recorded runtime provenance all rest
    on. Calling this with no traces used to SKIP every one of those checks — a
    silent optional argument that turned a verified evidence handoff into an
    unverified config dump. For the "just make a production-config template"
    use case, call export_cp_template, which is honestly named and never claims a
    bridge or verified provenance."""
    if traces is None:
        raise CPExportError(
            "export_cp is an evidence-backed handoff and REQUIRES the complete traces; "
            "for an unverified production-config template use export_cp_template"
        )
    for problem_source, obj in (("bundle", bundle), *((f"trace {t.get('trace_id')}", t)
                                                      for t in traces.values())):
        errs = validate_artifact(obj, "bundle" if problem_source == "bundle" else "trace")
        if errs:
            raise CPExportError(f"{problem_source} failed schema validation: {errs[:5]}")
    try:
        verify_bundle(bundle, traces)
    except BundleIntegrityError as exc:
        raise CPExportError(f"bundle graph is not verifiable: {exc}") from exc
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
    # _earned_for RAISES if the supplied traces are a partial completed set, so a
    # caller cannot earn the bridge on a cherry-picked favourable subset (r17).
    earned, analysis = _earned_for(bundle, str(governed["id"]), baseline_id, traces or {})
    # the PARAMETRIC config identity — kernel + policy + the manifests whose effect
    # classes / driving args / untrusted-fields change the governor's verdicts,
    # with allowlist `$inputs` refs left SYMBOLIC. This is what actually carries
    # over: the same parametric policy, re-parameterized with PRODUCTION inputs. It
    # is NOT a byte-identical runtime config — that depends on the scenario inputs,
    # captured per-scenario as runtime_config_hashes (review r17).
    manifests: list[dict[str, object]] = bundle["tool_manifests"]  # type: ignore[assignment]
    parametric_hash = parametric_policy_hash(kernel, policy, manifests)
    # the per-scenario CONCRETE runtime hashes — ONLY for scenarios that actually
    # RAN a completed trial under the exported (governed) condition (review r19).
    # The old code emitted a hash for EVERY scenario in the bundle, so a scenario
    # whose governed trial never completed still got a "what actually ran" hash for
    # a config that never ran. These are recomputed here but must AGREE with what
    # the bundle recorded at execution (environment.config_provenance): a mismatch
    # or a missing record is a hard error, never a silently-synthesized value.
    governed_id = str(governed["id"])
    scen_by_id = {str(s["name"]): s for s in bundle.get("scenarios", [])}  # type: ignore[union-attr]
    executed = sorted({
        str(t.get("scenario_id"))
        for t in bundle.get("trials", [])  # type: ignore[union-attr]
        if t.get("status") == "completed" and str(t.get("condition_id")) == governed_id
    })
    runtime_hashes = {
        sid: runtime_config_hash(kernel, policy, manifests, scen_by_id[sid].get("inputs", {}))
        for sid in executed if sid in scen_by_id
    }
    _verify_recorded_runtime_hashes(bundle, governed_id, runtime_hashes, require=True)
    source: dict[str, object] = {
        "bundle_id": bundle.get("bundle_id"),
        "condition_id": governed["id"],
        "baseline_condition_id": baseline_id,
    }
    if analysis is not None:
        # supporting refs point at the RECOMPUTED analysis receipt (the evidence
        # that actually earned the bridge), never at stored aggregates the bridge
        # never consulted (review r17). The receipt is embedded + content-addressed.
        source["bridge_analysis"] = analysis
        source["bridge_analysis_ref"] = content_hash(analysis)
    config: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA,
        # this config was derived from the COMPLETE evidence: graph-verified,
        # bridge recomputed from traces, runtime provenance proven (review r19)
        "verified": True,
        "kernel": kernel,
        "policy": policy,
        "config_hash": recorded,  # the recorded kernel+policy fingerprint
        # the carry-over key (symbolic $inputs); re-parameterized in production
        "parametric_config_hash": parametric_hash,
        # per-scenario concrete config identity (what actually ran) — NOT carried
        # over as the production config; recorded so a reader can pin it
        "runtime_config_hashes": runtime_hashes,
        "tool_manifests": bundle["tool_manifests"],
        "regressions": carried_pins,
        "source": source,
    }
    return CPExport(
        config=config,
        production_todo=_render_todo(),
        earned_bridge=earned,
    )


def export_cp_template(
    bundle: dict[str, object],
    condition_id: str | None = None,
) -> CPExport:
    """An UNVERIFIED production-config TEMPLATE (review r19): the validated policy +
    manifests + recorded config_hash carry over, WITHOUT the evidence. No traces
    means no graph verification, no earned bridge, and no proven runtime
    provenance — so `verified` is False, `earned_bridge` is always False, and no
    regression pins are carried (pins need traces to validate). Honestly named and
    separate from export_cp so a config dump is never mistaken for a verified
    evidence handoff."""
    governed = _select_condition(bundle, condition_id)
    policy: dict[str, object] = governed.get("policy", {})  # type: ignore[assignment]
    kernel = str(governed["kernel"])
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
    manifests: list[dict[str, object]] = bundle["tool_manifests"]  # type: ignore[assignment]
    config: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA,
        # NOT derived from evidence — a template only. A reader must not treat this
        # as a proven handoff (review r19).
        "verified": False,
        "kernel": kernel,
        "policy": policy,
        "config_hash": recorded,
        "parametric_config_hash": parametric_policy_hash(kernel, policy, manifests),
        "runtime_config_hashes": {},
        "tool_manifests": bundle["tool_manifests"],
        "regressions": [],
        "source": {
            "bundle_id": bundle.get("bundle_id"),
            "condition_id": governed["id"],
            "baseline_condition_id": _baseline_condition_id(bundle),
        },
    }
    return CPExport(config=config, production_todo=_render_todo(), earned_bridge=False)


def _verify_recorded_runtime_hashes(
    bundle: dict[str, object], condition_id: str, recomputed: dict[str, str],
    *, require: bool,
) -> None:
    """Every runtime config hash the export ships must equal the one the bundle
    RECORDED AT EXECUTION for that (scenario, condition) — a MANDATORY, complete,
    compiler-versioned provenance for an evidence-backed export (review r19).

    `require=True` (traces supplied) makes provenance MANDATORY: a bundle with no
    config_provenance, no compiler_version, or a missing key for a scenario the
    export ships a hash for is REFUSED — it cannot prove the recommended runtime
    config is the one that ran. A compiler-version drift, or any per-key mismatch,
    is a hard error. `require=False` is the legacy template path (no evidence), and
    only checks recorded keys that happen to be present."""
    env: dict[str, object] = bundle.get("environment", {})  # type: ignore[assignment]
    prov: dict[str, object] = env.get("config_provenance", {})  # type: ignore[assignment]
    recorded_all: dict[str, object] = prov.get("runtime_config_hashes", {})  # type: ignore[assignment]
    if not recorded_all:
        if require:
            raise CPExportError(
                "evidence-backed CP export requires environment.config_provenance recorded at "
                "run time; this bundle has none — refusing to ship a runtime config identity "
                "that cannot be proven to be what ran (rebuild with a runner that records it)"
            )
        return
    compiler = str(prov.get("compiler_version", ""))
    if compiler != CONFIG_COMPILER_VERSION:
        raise CPExportError(
            f"config_provenance.compiler_version {compiler!r} != this build's "
            f"{CONFIG_COMPILER_VERSION!r} — refusing to export a runtime config identity "
            "under a different compiler than the one that ran"
        )
    for scenario_id, rhash in recomputed.items():
        cond_map = recorded_all.get(scenario_id)
        recorded = cond_map.get(condition_id) if isinstance(cond_map, dict) else None
        if recorded is None:
            if require:
                raise CPExportError(
                    f"no recorded runtime_config_hash for scenario {scenario_id!r} / condition "
                    f"{condition_id!r} — every exported hash must correspond to a completed trial "
                    "recorded in config_provenance (a hash for an unexecuted config is refused)"
                )
            continue
        if str(recorded) != rhash:
            raise CPExportError(
                f"runtime_config_hash for {scenario_id!r}/{condition_id!r} recomputes to {rhash} "
                f"but the bundle recorded {recorded} at run time — does not match what ran"
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


def bridge_analysis(
    bundle: dict[str, object], condition_id: str,
    traces: dict[str, dict[str, object]] | None = None,
) -> dict[str, object] | None:
    """The `cp_bridge_analysis/v1` receipt for `condition_id`, or None if it did
    not earn the bridge. Raises on a partial trace set (review r17)."""
    baseline_id = _baseline_condition_id(bundle)
    if baseline_id is None or not traces:
        return None
    return _earned_for(bundle, condition_id, baseline_id, traces)[1]


def _bridge_outcomes(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> tuple[dict[tuple[str, str, int], dict[str, bool]], dict[str, list[str]]]:
    """Per-COORDINATE recomputed outcomes: {(scenario_id, seed, repeat_index):
    {condition_id: violated}} plus the sorted trace_refs per condition.

    The violation is recomputed from each trace via the scenario's own `violation`
    predicate — the same evidence a reader recomputes, NOT the uploaded aggregate
    (review r16). Keying by the full experimental-unit COORDINATE (not just the
    condition) is what lets the bridge compare the SAME units across arms rather
    than two pooled groups whose scenario composition may differ (review r19).

    EVERY completed trial's trace MUST be present: a missing one is a HARD error,
    never a silent skip — a caller cannot pass a cherry-picked favourable subset
    (review r17)."""
    from lab_contracts import content_hash
    from lab_runner import evaluate

    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    by_hash = {content_hash(t): t for t in traces.values()}
    outcomes: dict[tuple[str, str, int], dict[str, bool]] = {}
    refs: dict[str, list[str]] = {}
    for trial in bundle.get("trials", []):  # type: ignore[union-attr]
        if trial.get("status") != "completed":
            continue
        ref = str(trial.get("trace_ref"))
        trace = by_hash.get(ref)
        scenario = scenarios.get(str(trial.get("scenario_id")))
        if scenario is None:
            raise CPExportError(
                f"completed trial {trial.get('trial_id')!r} names scenario "
                f"{trial.get('scenario_id')!r} which is not in the bundle"
            )
        if trace is None:
            raise CPExportError(
                f"completed trial {trial.get('trial_id')!r} has no trace for {ref} in the "
                "supplied traces — refusing to compute the bridge over a partial trace set "
                "(pass the FULL completed evidence, not a cherry-picked subset)"
            )
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        violated = bool(evaluate(scenario["violation"], trace, inputs))  # type: ignore[arg-type]
        cid = str(trial["condition_id"])
        coord = (str(trial["scenario_id"]), str(trial["seed"]), int(trial["repeat_index"]))
        outcomes.setdefault(coord, {})[cid] = violated
        refs.setdefault(cid, []).append(ref)
    for cid in refs:
        refs[cid].sort()
    return outcomes, refs


def _arm_coords(
    bundle: dict[str, object], condition_id: str
) -> set[tuple[str, str, int]]:
    """EVERY trial coordinate (scenario, seed, repeat) for a condition — regardless
    of status. The PLANNED set for a matched contrast must include failed/excluded
    units too, else a pair where BOTH arms failed simply vanishes from the
    denominator and the receipt overstates coverage (review r20)."""
    coords: set[tuple[str, str, int]] = set()
    for trial in bundle.get("trials", []):  # type: ignore[union-attr]
        if str(trial.get("condition_id")) == condition_id:
            coords.add(
                (str(trial["scenario_id"]), str(trial["seed"]), int(trial["repeat_index"]))
            )
    return coords


def _bridge_design(
    bundle: dict[str, object], condition_id: str
) -> str:
    """The comparison design for this treated condition's ASR aggregate —
    matched_pairs (deterministic agent, McNemar) or independent_samples (live model,
    two-proportion). Defaults to matched_pairs: the trials carry (scenario, seed,
    repeat) coordinates, so the honest default is a paired contrast (review r19).

    Two ASR aggregates for the SAME condition make the design array-order-dependent
    (review r20): the read must be deterministic, so a duplicate is a hard error."""
    matching = [
        agg for agg in bundle.get("aggregates", [])  # type: ignore[union-attr]
        if str(agg.get("metric")) == "ASR" and str(agg.get("condition_id")) == condition_id
    ]
    if len(matching) > 1:
        raise CPExportError(
            f"bundle has {len(matching)} ASR aggregates for condition {condition_id!r} — the "
            "comparison design would depend on array order; reject the ambiguous bundle"
        )
    if matching and matching[0].get("comparison_design"):
        return str(matching[0]["comparison_design"])
    return "matched_pairs"


def _scenario_balance(
    coords: "set[tuple[str, str, int]]",
) -> dict[str, int]:
    """Per-scenario coordinate count, for the composition-balance check + receipt."""
    balance: dict[str, int] = {}
    for scenario_id, _seed, _repeat in coords:
        balance[scenario_id] = balance.get(scenario_id, 0) + 1
    return balance


def _earned_for(
    bundle: dict[str, object], condition_id: str, baseline_id: str | None,
    traces: dict[str, dict[str, object]],
) -> tuple[bool, dict[str, object] | None]:
    """Did `condition_id` beat the baseline on ASR because GOVERNANCE changed the
    outcome — not because the two arms tested a different mix of scenarios? Returns
    (earned, analysis); the analysis is a design-tagged `cp_bridge_analysis/v1`
    receipt naming the exact recomputed evidence (only when earned).

    A pooled ASR delta is not enough (review r19): two arms of equal size and a
    large, statistically separated delta can still be a COMPOSITION contrast (heavy
    scenarios in one arm, light in the other) rather than a governance effect. So
    the bridge FIRST requires the arms to cover the SAME scenarios, THEN:
      - matched_pairs: pairs are built over the COORDINATE INTERSECTION (same
        scenario+seed+repeat in both arms); too many unpaired units, a scenario
        with no completed pair, or a McNemar that is inconclusive / not in
        governance's favour does NOT earn it;
      - independent_samples: the arms must be balanced AND free of
        condition-correlated per-scenario missingness before the two-proportion
        interval is even consulted.
    Estimates are RECOMPUTED from the traces; fabricated aggregates cannot earn. A
    missing completed-trial trace raises (see _bridge_outcomes)."""
    if baseline_id is None or not traces:
        return False, None
    outcomes, refs = _bridge_outcomes(bundle, traces)
    base_coords = {c for c, o in outcomes.items() if baseline_id in o}
    treated_coords = {c for c, o in outcomes.items() if condition_id in o}
    if len(base_coords) < _MIN_EFFECTIVE_N or len(treated_coords) < _MIN_EFFECTIVE_N:
        return False, None
    # COMPOSITION GUARD (both designs): the arms must exercise the SAME scenarios.
    # Different scenario sets means the ASR delta confounds governance with test
    # composition — the exact false bridge r19 describes (heavy vs light arms).
    base_balance = _scenario_balance(base_coords)
    treated_balance = _scenario_balance(treated_coords)
    if set(base_balance) != set(treated_balance):
        return False, None
    design = _bridge_design(bundle, condition_id)
    if design == "matched_pairs":
        # the PLANNED set includes failed/excluded units too (review r20)
        planned_coords = _arm_coords(bundle, baseline_id) | _arm_coords(bundle, condition_id)
        earned, extra = _earned_matched(
            outcomes, base_coords, treated_coords, base_balance, planned_coords,
            baseline_id, condition_id,
        )
    else:
        earned, extra = _earned_independent(
            outcomes, base_coords, treated_coords, base_balance, treated_balance,
            baseline_id, condition_id,
        )
    if not earned:
        return False, None
    analysis: dict[str, object] = {
        "kind": "cp_bridge_analysis/v1",
        "metric": "ASR",
        "comparison_design": design,
        "baseline_condition_id": baseline_id,
        "treated_condition_id": condition_id,
        "scenario_balance": {
            baseline_id: base_balance,
            condition_id: treated_balance,
        },
        "trial_refs": {
            baseline_id: refs.get(baseline_id, []),
            condition_id: refs.get(condition_id, []),
        },
        **extra,  # type: ignore[dict-item]
    }
    return True, analysis


def _earned_matched(
    outcomes: dict[tuple[str, str, int], dict[str, bool]],
    base_coords: "set[tuple[str, str, int]]",
    treated_coords: "set[tuple[str, str, int]]",
    base_balance: dict[str, int],
    planned_coords: "set[tuple[str, str, int]]",
    baseline_id: str,
    treated_id: str,
) -> tuple[bool, dict[str, object]]:
    """Matched-pairs earn: pair the SAME experimental units across arms and test
    with McNemar over the discordant pairs (review r19). Statistical significance
    and PRACTICAL significance are two SEPARATE gates (review r20): a large enough
    pair count can make a microscopic net improvement p<0.05, so the absolute risk
    reduction must ALSO clear the declared minimum effect."""
    from lab_analysis import mcnemar_test

    matched = sorted(base_coords & treated_coords)
    # the PLANNED denominator is ALL units either arm attempted, incl. failed ones,
    # so a pair where both arms failed counts as dropped rather than vanishing (r20)
    planned, completed = len(planned_coords), len(matched)
    dropped = planned - completed
    if completed < _MIN_EFFECTIVE_N:
        return False, {}
    # condition-correlated missingness: too many units present in only one arm is a
    # composition contrast wearing a paired label; and every shared scenario must
    # actually contribute a completed pair (else its effect is unmeasured)
    if dropped / max(planned, 1) > _MAX_DROPPED_PAIR_FRACTION:
        return False, {}
    paired_scenarios = {coord[0] for coord in matched}
    if paired_scenarios != set(base_balance):
        return False, {}
    pairs = [(outcomes[c][baseline_id], outcomes[c][treated_id]) for c in matched]
    test = mcnemar_test(pairs, vs=baseline_id)
    if str(test.get("status")) != "conclusive":
        return False, {}
    discordant: dict[str, int] = test["discordant"]  # type: ignore[assignment]
    b, c = int(discordant["b"]), int(discordant["c"])
    # governance must PREVENT more violations than it introduces (b: baseline
    # violated & treated did not; c: the reverse), and significantly so
    if b <= c:
        return False, {}
    if float(test["p"]) >= 0.05:  # type: ignore[arg-type]
        return False, {}
    # PRACTICAL significance: the net absolute risk reduction over the paired units
    # must clear the declared floor, not merely be statistically non-zero (r20)
    net_risk_reduction = (b - c) / completed
    if net_risk_reduction < _MIN_EFFECT_DELTA:
        return False, {}
    return True, {
        "paired": {
            "planned_pairs": planned,
            "completed_pairs": completed,
            "dropped_pairs": dropped,
            "discordant": discordant,
            "absolute_risk_reduction": net_risk_reduction,
        },
        "test": test,
    }


def _earned_independent(
    outcomes: dict[tuple[str, str, int], dict[str, bool]],
    base_coords: "set[tuple[str, str, int]]",
    treated_coords: "set[tuple[str, str, int]]",
    base_balance: dict[str, int],
    treated_balance: dict[str, int],
    baseline_id: str,
    treated_id: str,
) -> tuple[bool, dict[str, object]]:
    """Independent-samples earn: the arms are independently sampled (live model),
    so the pairing is nominal — but the arms must have the SAME scenario mix before
    the pooled two-proportion interval is consulted (review r19/r20).

    An APPROXIMATE per-scenario balance (ratio >= 0.5) was not enough (review r20):
    two arms with identical scenario sets and equal totals can still reweight a
    hard scenario against an easy one and manufacture a delta from pure composition
    (baseline 40 hard / 20 easy vs governed 20 hard / 40 easy — each scenario ratio
    is exactly 0.5, yet governance changed nothing). So the per-scenario arm counts
    must be EXACTLY equal: then the pooled rate is a fixed-weight average and the
    delta cannot be a weighting artefact. The receipt also carries the
    scenario-standardized estimates so a reader sees the per-scenario effect."""
    from lab_analysis import two_proportion_test

    base_n, treated_n = len(base_coords), len(treated_coords)
    # EXACT per-scenario balance — identical composition, not merely a similar one
    if base_balance != treated_balance:
        return False, {}
    base_v = sum(1 for c in base_coords if outcomes[c][baseline_id])
    treated_v = sum(1 for c in treated_coords if outcomes[c][treated_id])
    if base_v / base_n - treated_v / treated_n < _MIN_EFFECT_DELTA:
        return False, {}
    test = two_proportion_test(base_v, base_n, treated_v, treated_n, vs=baseline_id)
    interval: dict[str, object] = test["interval"]  # type: ignore[assignment]
    if float(interval["low"]) <= 0.0:  # type: ignore[arg-type]
        return False, {}
    # scenario-standardized rates (equal weight per scenario): with exact balance
    # these equal the pooled rates, but they make the fixed-weighting explicit
    def _standardized(coords: "set[tuple[str, str, int]]", cid: str) -> float:
        per: dict[str, list[int]] = {}
        for coord in coords:
            per.setdefault(coord[0], []).append(1 if outcomes[coord][cid] else 0)
        rates = [sum(v) / len(v) for v in per.values() if v]
        return sum(rates) / len(rates) if rates else 0.0

    return True, {
        "scenario_weighting": "exact_per_scenario_balance",
        "standardized": {
            baseline_id: _standardized(base_coords, baseline_id),
            treated_id: _standardized(treated_coords, treated_id),
        },
        "baseline": {"violations": base_v, "n": base_n},
        "treated": {"violations": treated_v, "n": treated_n},
        "difference_interval": interval,
        "test": test,
    }


def _baseline_condition_id(bundle: dict[str, object]) -> str | None:
    """The single enforcement-off baseline. Returns None when there is none, but
    raises when there are SEVERAL (review r20): picking the first by array order
    made the CP result depend on a cosmetic reordering that evidence_lineage_ref
    deliberately treats as insignificant, so an aggregate could name baseline B
    while the bridge recomputed against baseline A."""
    offs = [str(c["id"]) for c in bundle["conditions"] if c["enforcement"] == "off"]  # type: ignore[union-attr]
    if len(offs) > 1:
        raise CPExportError(
            f"bundle has multiple enforcement-off conditions ({', '.join(sorted(offs))}); "
            "the baseline is ambiguous — the comparison must name it explicitly"
        )
    return offs[0] if offs else None


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
        "The exported `parametric_config_hash` is the carry-over key: it fingerprints "
        "the kernel + policy with symbolic `$inputs`, so it stays identical when the "
        "Control Plane re-parameterizes the SAME policy with production inputs. The "
        "`config_hash`/`runtime_config_hashes` fingerprint the CONCRETE config the Lab "
        "run measured — they do not carry over, because production inputs differ (r18)."
    )
    return "\n".join(lines) + "\n"
