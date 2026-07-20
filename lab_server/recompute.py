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
the marginal n is the completed trials OF THAT CONDITION (the runner's
per-condition marginal), for both designs. The pairing lives only in the test
object — McNemar over the baseline∩treated intersection (statistics.md §1) —
never in the marginal denominator, so the two agree exactly at missingness.
"""

from __future__ import annotations

from lab_analysis import binary_aggregate, mcnemar_test, two_proportion_test
from lab_contracts import content_hash
from lab_runner import evaluate

# CLOSED metric registry: a metric maps to exactly one recorded outcome. An
# unknown metric is rejected — otherwise the old `else task_success` fallback let
# a caller launder an arbitrary label ("zero_production_incidents") into a
# server-recomputed claim carrying the task-success rate (review r7).
_METRIC_OUTCOME = {
    "ASR": "violation",
    "task_success_rate": "task_success",
    "utility": "task_success",
}
# providers whose behavior is fixed by scenario+seed → matched pairs are real
_DETERMINISTIC_PROVIDERS = frozenset({"scripted", "cassette", "imported", ""})


def _metric_field(metric: str) -> str | None:
    return _METRIC_OUTCOME.get(metric)


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
    out: dict[tuple[str, str], dict[str, object]] = {}
    for agg in uploaded:
        metric = str(agg["metric"])
        cid = str(agg["condition_id"])
        field = _metric_field(metric)
        if field is None:
            continue  # unknown metric — reported by check_aggregates, not recomputed
        # The marginal aggregate n is ALWAYS the completed trials OF THIS
        # CONDITION — identical to the runner's _condition_counts, for BOTH
        # designs. The pairing is a property of the TEST (McNemar over the
        # baseline∩treated intersection), never of the marginal denominator.
        # Computing the matched-pairs marginal over the all-conditions
        # intersection made the server reject honest runner bundles at
        # missingness: a single failed baseline trial shrank every condition's
        # recomputed n below the runner's per-condition marginal (review r12).
        marg = [r[cid] for r in rows.values() if cid in r]
        n = len(marg)
        successes = sum(1 for o in marg if o[field])
        out[(metric, cid)] = binary_aggregate(metric, cid, successes, n)
    return out


def check_aggregates(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> list[str]:
    """Compare uploaded aggregates to the recompute; return mismatch messages
    (empty ⇒ every aggregate is reproduced from the evidence)."""
    recomputed = recompute_aggregates(bundle, traces)
    rows = _rows(bundle, traces)
    env_live = not _environment_is_deterministic(bundle)
    problems: list[str] = []
    for agg in bundle["aggregates"]:  # type: ignore[union-attr]
        metric = str(agg["metric"])
        cid = str(agg["condition_id"])
        key = (metric, cid)
        if _metric_field(metric) is None:
            problems.append(f"{key}: unknown metric {metric!r} (known: {sorted(_METRIC_OUTCOME)})")
            continue
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

        # the STATISTICAL TEST is where fabrication hid: hash + marginals verify,
        # but the paired McNemar / independent two-proportion object was never
        # recomputed. Recompute it from the evidence and compare (review r7).
        design = str(agg.get("comparison_design", "matched_pairs"))
        if design == "matched_pairs" and env_live:
            problems.append(
                f"{key}: comparison_design=matched_pairs but the environment is a live model "
                "(independently sampled) — a paired test is invalid"
            )
        test = agg.get("test")
        if test is not None:
            problems += _check_test(dict(test), metric, cid, design, rows)  # type: ignore[arg-type]
    return problems


def _environment_is_deterministic(bundle: dict[str, object]) -> bool:
    provider = str(
        bundle.get("environment", {}).get("model", {}).get("provider", "")  # type: ignore[union-attr]
    )
    return provider in _DETERMINISTIC_PROVIDERS


def _check_test(
    test: dict[str, object], metric: str, treated_id: str, design: str,
    rows: dict[tuple[str, str, int], dict[str, dict[str, bool]]],
) -> list[str]:
    """Recompute the comparison test from the evidence and compare every field."""
    field = _metric_field(metric)
    baseline_id = str(test.get("vs", ""))
    key = (metric, treated_id)
    if field is None or not baseline_id:
        return [f"{key}: test has no resolvable baseline"]
    if design == "independent_samples":
        base = [r[baseline_id] for r in rows.values() if baseline_id in r]
        treat = [r[treated_id] for r in rows.values() if treated_id in r]
        rec = two_proportion_test(
            sum(1 for o in base if o[field]), len(base),
            sum(1 for o in treat if o[field]), len(treat), vs=baseline_id,
        )
        if str(test.get("name")) != "two_proportion":
            return [f"{key}: independent_samples must use two_proportion, not {test.get('name')!r}"]
        problems = []
        if abs(float(test.get("difference", 0.0)) - float(rec["difference"])) > 1e-9:  # type: ignore[arg-type]
            problems.append(f"{key}: test.difference {test.get('difference')} != {rec['difference']}")
        if abs(float(test.get("p", -1)) - float(rec["p"])) > 1e-9:  # type: ignore[arg-type]
            problems.append(f"{key}: test.p {test.get('p')} != recomputed {rec['p']}")
        return problems
    # matched pairs → McNemar
    pairs = [
        (r[baseline_id][field], r[treated_id][field])
        for r in rows.values() if baseline_id in r and treated_id in r
    ]
    rec = mcnemar_test(pairs, vs=baseline_id)
    if str(test.get("name")) != "mcnemar":
        return [f"{key}: matched_pairs must use mcnemar, not {test.get('name')!r}"]
    problems = []
    if int(test.get("paired_n", -1)) != int(rec["paired_n"]):  # type: ignore[arg-type]
        problems.append(f"{key}: test.paired_n {test.get('paired_n')} != recomputed {rec['paired_n']}")
    ud: dict[str, object] = test.get("discordant", {})  # type: ignore[assignment]
    rd: dict[str, object] = rec["discordant"]  # type: ignore[assignment]
    if int(ud.get("b", -1)) != int(rd["b"]) or int(ud.get("c", -1)) != int(rd["c"]):  # type: ignore[arg-type]
        problems.append(f"{key}: test.discordant {ud} != recomputed {rd}")
    if abs(float(test.get("p", -1)) - float(rec["p"])) > 1e-9:  # type: ignore[arg-type]
        problems.append(f"{key}: test.p {test.get('p')} != recomputed {rec['p']}")
    return problems
