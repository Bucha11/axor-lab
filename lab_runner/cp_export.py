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
) -> CPExport:
    """Build the CP deploy config + production-todo from a verified bundle."""
    governed = _enforcing_condition(bundle)
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
        },
    }
    return CPExport(
        config=config,
        production_todo=_render_todo(),
        earned_bridge=earned_bridge(bundle),
    )


def earned_bridge(bundle: dict[str, object]) -> bool:
    """The bridge is EARNED, not nagged: true only when an aggregate shows
    governance changed the outcome on the researcher's own agent — an ASR that
    dropped from the ungoverned baseline to the governed condition."""
    aggregates: list[dict[str, object]] = bundle.get("aggregates", [])  # type: ignore[assignment]
    by_condition = {
        (str(a["metric"]), str(a["condition_id"])): a for a in aggregates
    }
    for (metric, condition_id), aggregate in by_condition.items():
        if metric != "ASR" or condition_id == "ungoverned":
            continue
        baseline = by_condition.get(("ASR", "ungoverned"))
        if baseline is None:
            continue
        if float(baseline["estimate"]) > float(aggregate["estimate"]):  # type: ignore[arg-type]
            return True
    return False


def _enforcing_condition(bundle: dict[str, object]) -> dict[str, object]:
    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        if condition["enforcement"] == "on":
            return condition
    raise CPExportError("bundle has no enforcement-on condition to carry over")


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
