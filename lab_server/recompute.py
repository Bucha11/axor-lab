"""Server-side recomputation of a bundle's statistical claims (review r2 Patch 4).

The publish handshake must not take an uploaded aggregate on faith and then mint
a 'statistically reproducible' claim over it: a caller could upload
``{estimate: 0.0, n: 1000000}`` with perfectly self-consistent content hashes,
and hash verification alone would pass. So the server recomputes every
aggregate from the trials + traces + scenario predicates — the same evidence a
reader could recompute — and compares it to what was uploaded. A mismatch means
the numbers do not follow from the evidence, and the publish is rejected.

The recomputation mirrors the runner's own aggregation (``ExperimentResult``):
ASR maps to the `violation` predicate, any other metric to `task_success`, and
n is the number of trials that form a complete set across the conditions the
metric is compared over (the paired unit of analysis, statistics.md §1).
"""

from __future__ import annotations

from lab_analysis import binary_aggregate
from lab_contracts import content_hash
from lab_runner import evaluate


def _metric_field(metric: str) -> str:
    # mirror ExperimentResult.pairs: ASR ← violation, else task_success
    return "violation" if metric == "ASR" else "task_success"


def _rows(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> dict[tuple[str, str, int], dict[str, dict[str, bool]]]:
    """Per (scenario, seed, repeat) → {condition_id: {violation, task_success}}."""
    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    by_hash = {content_hash(t): t for t in traces.values()}
    rows: dict[tuple[str, str, int], dict[str, dict[str, bool]]] = {}
    for trial in bundle["trials"]:  # type: ignore[union-attr]
        if trial.get("status") != "completed":
            continue
        trace = by_hash.get(str(trial.get("trace_ref")))
        if trace is None:
            continue
        scenario = scenarios[str(trial["scenario_id"])]
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        outcome = {
            "violation": bool(evaluate(scenario["violation"], trace, inputs)),  # type: ignore[arg-type]
            "task_success": bool(evaluate(scenario["task_success"], trace, inputs)),  # type: ignore[arg-type]
        }
        key = (str(trial["scenario_id"]), str(trial["seed"]), int(trial["repeat_index"]))
        rows.setdefault(key, {})[str(trial["condition_id"])] = outcome
    return rows


def recompute_aggregates(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> dict[tuple[str, str], dict[str, object]]:
    """Recompute each uploaded aggregate from the evidence.

    Returns {(metric, condition_id): recomputed aggregate dict}."""
    uploaded: list[dict[str, object]] = bundle["aggregates"]  # type: ignore[assignment]
    rows = _rows(bundle, traces)
    metric_conditions: dict[str, set[str]] = {}
    for agg in uploaded:
        metric_conditions.setdefault(str(agg["metric"]), set()).add(str(agg["condition_id"]))
    out: dict[tuple[str, str], dict[str, object]] = {}
    for agg in uploaded:
        metric = str(agg["metric"])
        cid = str(agg["condition_id"])
        field = _metric_field(metric)
        design = str(agg.get("comparison_design", "matched_pairs"))
        if design == "independent_samples":
            # independent samples: n is the MARGINAL count for this condition,
            # not the paired intersection
            marg = [r[cid] for r in rows.values() if cid in r]
            n = len(marg)
            successes = sum(1 for o in marg if o[field])
        else:
            # matched pairs: a trial counts once it exists under every condition
            # this metric is compared across (the paired unit of analysis)
            cond_ids = metric_conditions[metric]
            complete = [r for r in rows.values() if cond_ids <= r.keys()]
            n = len(complete)
            successes = sum(1 for r in complete if r[cid][field])
        out[(metric, cid)] = binary_aggregate(metric, cid, successes, n)
    return out


def check_aggregates(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> list[str]:
    """Compare uploaded aggregates to the recompute; return mismatch messages
    (empty ⇒ every aggregate is reproduced from the evidence)."""
    recomputed = recompute_aggregates(bundle, traces)
    problems: list[str] = []
    for agg in bundle["aggregates"]:  # type: ignore[union-attr]
        key = (str(agg["metric"]), str(agg["condition_id"]))
        rec = recomputed.get(key)
        if rec is None:
            problems.append(f"{key}: no recomputation")
            continue
        if int(agg["n"]) != int(rec["n"]):
            problems.append(f"{key}: n uploaded {agg['n']} != recomputed {rec['n']}")
        if abs(float(agg["estimate"]) - float(rec["estimate"])) > 1e-9:
            problems.append(
                f"{key}: estimate uploaded {agg['estimate']} != recomputed {rec['estimate']}"
            )
        ui: dict[str, object] = agg.get("interval", {})  # type: ignore[assignment]
        ri: dict[str, object] = rec.get("interval", {})  # type: ignore[assignment]
        if (
            abs(float(ui.get("low", 0.0)) - float(ri.get("low", 0.0))) > 1e-6  # type: ignore[arg-type]
            or abs(float(ui.get("high", 0.0)) - float(ri.get("high", 0.0))) > 1e-6  # type: ignore[arg-type]
        ):
            problems.append(f"{key}: interval uploaded {ui} != recomputed {ri}")
    return problems
