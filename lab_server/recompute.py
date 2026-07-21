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
# providers whose behavior is DECLARED deterministic (fixed by scenario+seed), so
# a matched-pairs design is at least self-consistent. This is an UPLOADER-DECLARED
# signal read from environment.model.provider — not proof, since the whole bundle
# is uploader-controlled (review r14). The empty string and "imported" are NOT
# evidence of determinism (an empty/unknown provider must not silently enable a
# paired test), so they are excluded; a live run that self-labels one of the
# names below still cannot PROVE its pairing — the publication marks the design
# uploader-declared, not attested (see the claim text in store._mint).
_DETERMINISTIC_PROVIDERS = frozenset({"scripted", "cassette"})


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


def _values_match(uploaded: object, recomputed: object) -> bool:
    """Deep equality with float tolerance — the uploaded value came through JSON
    but was produced by the SAME statistics functions, so an honest field
    reproduces the recompute exactly (numbers within a tiny tolerance)."""
    if isinstance(recomputed, dict):
        if not isinstance(uploaded, dict) or set(uploaded) != set(recomputed):
            return False
        return all(_values_match(uploaded[k], recomputed[k]) for k in recomputed)
    if isinstance(recomputed, bool) or isinstance(uploaded, bool):
        return uploaded is recomputed
    if isinstance(recomputed, (int, float)) and isinstance(uploaded, (int, float)):
        return abs(float(uploaded) - float(recomputed)) <= 1e-6
    return uploaded == recomputed


def _test_shape_problems(
    test: dict[str, object], rec: dict[str, object], key: tuple[str, str]
) -> list[str]:
    """The uploaded test must be the EXACT recomputed shape (review r15/r16).

    The bundle schema allows arbitrary `test` properties (additionalProperties);
    the SERVER does not. The uploaded test must carry precisely the fields the
    server recomputes — no extra fields riding along unchecked, and no missing
    field silently defaulting — and every value must reproduce the recompute.
    This subsumes the old per-field checks: a fabricated interval, a dropped
    discordant count, or a stale effective_n all fail the same shape gate."""
    # a test the server recomputes as underpowered is one the RUNNER would never
    # have attached (binary_aggregate drops tests below the minimum effective n),
    # so accepting it here would mint a 'statistically reproducible' claim over a
    # test the tool that made the bundle would have refused to publish (review r16)
    if str(rec.get("status")) == "inconclusive":
        return [
            f"{key}: test is underpowered (recomputed status 'inconclusive', "
            f"effective_n {rec.get('effective_n')}) — the runner would not attach it; "
            "refusing to publish an inconclusive test as a reproducible claim"
        ]
    missing = sorted(set(rec.keys()) - set(test.keys()))
    extra = sorted(set(test.keys()) - set(rec.keys()))
    problems: list[str] = []
    if missing:
        problems.append(f"{key}: test is missing recomputed field(s) {missing}")
    if extra:
        problems.append(f"{key}: test carries unrecognized field(s) {extra} the server does not recompute")
    for field in sorted(set(rec.keys()) & set(test.keys())):
        if not _values_match(test[field], rec[field]):
            problems.append(f"{key}: test.{field} {test[field]!r} != recomputed {rec[field]!r}")
    return problems


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
        # the whole test must be the EXACT recomputed shape — difference, p, the
        # Newcombe interval, design, effective_n, status — nothing fabricated rides
        # along, nothing underpowered is accepted (review r15/r16)
        return _test_shape_problems(test, rec, key)
    # matched pairs → McNemar
    pairs = [
        (r[baseline_id][field], r[treated_id][field])
        for r in rows.values() if baseline_id in r and treated_id in r
    ]
    rec = mcnemar_test(pairs, vs=baseline_id)
    if str(test.get("name")) != "mcnemar":
        return [f"{key}: matched_pairs must use mcnemar, not {test.get('name')!r}"]
    # exact recomputed shape: discordant{b,c}, paired_n, effective_n (the
    # discordant n), p, status — an underpowered (inconclusive) McNemar is refused
    return _test_shape_problems(test, rec, key)
