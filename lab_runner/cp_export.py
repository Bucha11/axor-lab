"""Control Plane export — the earned bridge (plan B2, control-plane-handoff.md).

What carries over (reused, identical): the validated policy + `config_hash`,
the tool manifests, and any pinned regression cases. What must be ADDED for
production (the honest half, NOT reused): real tool bindings, credentials,
deployment topology, notifications/owners. This module emits the first as an
`axor-cp-deploy/v1` config and the second as a `production-todo.md`, so the
handoff never reads as "nothing is re-done."

The `config_hash` is the carry-over KEY: it is emitted byte-identical to the
condition the researcher measured, so the Control Plane governs under exactly
the config Lab validated.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_contracts import condition_config_hash

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
) -> CPExport:
    """Build the CP deploy config + production-todo from a verified bundle.

    The condition to deploy is chosen EXPLICITLY when several enforce, so the
    exported policy is provably the one whose aggregate showed the delta — not
    just the first enforcement-on condition (which could differ from the one the
    earned bridge measured, deploying a config that never changed the outcome)."""
    governed = _select_condition(bundle, condition_id)
    policy: dict[str, object] = governed.get("policy", {})  # type: ignore[assignment]
    kernel = str(governed["kernel"])
    # recompute the carry-over key from policy+kernel; assert it matches what the
    # bundle recorded, so the exported hash is provably the measured config
    recomputed = condition_config_hash(kernel, policy)
    recorded = str(governed.get("config_hash", recomputed))
    if recorded != recomputed:
        raise CPExportError(
            f"condition config_hash {recorded} does not match its policy+kernel "
            f"({recomputed}); refusing to export a config the researcher did not measure"
        )

    baseline_id = _baseline_condition_id(bundle)
    earned, supporting = _earned_for(bundle, str(governed["id"]), baseline_id)
    config: dict[str, object] = {
        "schema_version": EXPORT_SCHEMA,
        "kernel": kernel,
        "policy": policy,
        "config_hash": recomputed,  # the carry-over key, byte-identical
        "tool_manifests": bundle["tool_manifests"],
        "regressions": [
            {"trace_id": r["trace_id"], "expected_verdict": r["expected_verdict"]}
            for r in (regressions or [])
        ],
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
