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

from lab_contracts import condition_config_hash, content_hash

_VALID_VERDICTS = frozenset({"ALLOW", "DENY"})

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
    earned, supporting = _earned_for(bundle, str(governed["id"]), baseline_id)
    config: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA,
        "kernel": kernel,
        "policy": policy,
        "config_hash": recorded,  # the carry-over key, the RECORDED measurement
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
        if "expected_sequence" in raw:
            claimed = [str(v) for v in raw["expected_sequence"]]  # type: ignore[union-attr]
            if claimed != recorded_sequence:
                raise CPExportError(
                    f"pin {tid!r}: expected_sequence {claimed} does not match the frozen "
                    f"trace's recorded verdicts {recorded_sequence}"
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


def earned_bridge(bundle: dict[str, object], condition_id: str | None = None) -> bool:
    """The bridge is EARNED, not nagged: true only when an aggregate shows
    governance changed the outcome on the researcher's own agent — an ASR that
    dropped from the (enforcement-off) baseline to a governed condition.

    The baseline is the condition whose enforcement is OFF, resolved by role —
    NOT the literal id 'ungoverned', which a bundle need not use. With
    `condition_id`, only that condition is checked (used by export_cp so the
    bridge is about the condition actually being deployed)."""
    baseline_id = _baseline_condition_id(bundle)
    if baseline_id is None:
        return False
    if condition_id is not None:
        return _earned_for(bundle, condition_id, baseline_id)[0]
    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        if condition["enforcement"] == "on" and _earned_for(bundle, str(condition["id"]), baseline_id)[0]:
            return True
    return False


def _earned_for(
    bundle: dict[str, object], condition_id: str, baseline_id: str | None
) -> tuple[bool, tuple[str, ...]]:
    """Did `condition_id` beat the baseline on ASR? Returns (earned, refs)."""
    if baseline_id is None:
        return False, ()
    aggs = {
        (str(a["metric"]), str(a["condition_id"])): a
        for a in bundle.get("aggregates", [])  # type: ignore[union-attr]
    }
    treated = aggs.get(("ASR", condition_id))
    base = aggs.get(("ASR", baseline_id))
    if treated is None or base is None:
        return False, ()
    earned = float(base["estimate"]) > float(treated["estimate"])  # type: ignore[arg-type]
    refs = (f"agg:ASR:{baseline_id}", f"agg:ASR:{condition_id}") if earned else ()
    return earned, refs


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
