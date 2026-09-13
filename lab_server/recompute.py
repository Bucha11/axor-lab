"""Server-side recomputation of a bundle's statistical claims (review r2 Patch 4).

The publish handshake must not take an uploaded aggregate on faith and then mint
a 'statistically reproducible' claim over it: a caller could upload
``{estimate: 0.0, n: 1000000}`` with perfectly self-consistent content hashes,
and hash verification alone would pass. So the server recomputes every
aggregate from the trials + traces + scenario predicates — the same evidence a
reader could recompute — and compares it to what was uploaded. A mismatch means
the numbers do not follow from the evidence, and the publish is rejected.

The recomputation mirrors the runner's own aggregation (``ExperimentResult``):
TWO TIERS, and the difference is what may be CLAIMED.

A metric the server can DERIVE from the evidence — a boolean predicate over each
trace — is recomputed here from the traces and the scenario, so an aggregate
over it can back a `statistically_reproducible` claim. A metric the server
cannot derive — latency, tokens, spend: the runner's own measurements, absent
from any trace — has its ESTIMATOR re-applied to the reported per-trial values.
That proves the arithmetic and nothing about the observations, so it is
`self_reported` and mints no claim, which is exactly what publication/v1 already
says the value means.

The implementation had only the first tier and REJECTED the second: a suite
declaring `mean(duration_ms)` was refused with "unknown metric", so three of the
four built-ins could not be published at all. Rejecting is not the safe choice
here — it is the choice that makes an honest figure unpublishable while teaching
nobody anything about attestation.

ASR maps to the `violation` predicate, any other metric to `task_success`, and
the marginal n is the completed trials OF THAT CONDITION (the runner's
per-condition marginal), for both designs. The pairing lives only in the test
object — McNemar over the baseline∩treated intersection (statistics.md §1) —
never in the marginal denominator, so the two agree exactly at missingness.
"""

from __future__ import annotations

from lab_analysis import (
    NUMERIC_ESTIMATORS,
    binary_aggregate,
    mcnemar_test,
    numeric_aggregate,
    two_proportion_test,
)
from lab_contracts import content_hash
from lab_runner import evaluate

# CLOSED metric registry: a metric maps to exactly one recorded outcome. An
# unknown metric is rejected — otherwise the old `else task_success` fallback let
# a caller launder an arbitrary label ("zero_production_incidents") into a
# server-recomputed claim carrying the task-success rate (review r7).
_METRIC_OUTCOME = {
    "ASR": "violation",
    # the literal name of the recorded outcome. Three of the four built-in
    # suites declare their success metric as `task_success` (it IS
    # `trial.metrics.task_success`), so the whole Suite Platform was
    # unpublishable: "unknown metric 'task_success'". Adding it is not the
    # laundering this registry exists to stop — that is an ARBITRARY label
    # ("zero_production_incidents") resolving to the task-success rate, and this
    # is the one name that cannot be arbitrary.
    "task_success": "task_success",
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
    """The recorded outcome a metric is DERIVED from, or None when it is not
    derivable and can only be re-applied over what the runner reported."""
    return _METRIC_OUTCOME.get(metric)


def _reported_values(
    bundle: dict[str, object], metric: str, condition_id: str,
) -> list[float]:
    """The runner's own per-trial numbers for one metric under one arm.

    Booleans are excluded deliberately: `True` is not 1.0 here. A boolean metric
    belongs to the derived tier, and silently averaging it would publish a rate
    the server never checked against a single trace.
    """
    values: list[float] = []
    for trial in bundle.get("trials", []):  # type: ignore[union-attr]
        if trial.get("status") != "completed":
            continue
        if str(trial.get("condition_id")) != condition_id:
            continue
        raw = (trial.get("metrics") or {}).get(metric)  # type: ignore[union-attr]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        values.append(float(raw))
    return values


def reapply_reported_aggregates(
    bundle: dict[str, object],
) -> dict[tuple[str, str], dict[str, object]]:
    """Re-apply each self-reported aggregate's own estimator to its own values.

    The weaker of the two checks, and still worth running: it catches an
    estimate that does not follow from the per-trial numbers beside it. What it
    cannot catch is a fabricated per-trial number, which is why nothing here
    earns a claim.

    An aggregate that names no `estimator` is skipped rather than guessed —
    trying every function until one matches would accept whichever the uploader
    meant it to be.
    """
    out: dict[tuple[str, str], dict[str, object]] = {}
    for aggregate in bundle.get("aggregates", []):  # type: ignore[union-attr]
        metric, cid = str(aggregate["metric"]), str(aggregate["condition_id"])
        if _metric_field(metric) is not None:
            continue  # derived from the traces; the stronger tier handles it
        estimator = aggregate.get("estimator")
        if not isinstance(estimator, str) or estimator not in NUMERIC_ESTIMATORS:
            continue
        values = _reported_values(bundle, metric, cid)
        if not values:
            continue
        out[(metric, cid)] = numeric_aggregate(
            metric, cid, values, estimator,
            unit_of_analysis=str(aggregate.get("unit_of_analysis", "trial")),
        )
    return out


def derived_metrics(bundle: dict[str, object]) -> set[tuple[str, str]]:
    """Which aggregates the server DERIVED from the evidence — the only ones a
    statistical claim may rest on."""
    return {
        (str(a["metric"]), str(a["condition_id"]))
        for a in bundle.get("aggregates", [])  # type: ignore[union-attr]
        if _metric_field(str(a["metric"])) is not None
    }


def _rows(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> dict[tuple[str, str, int, str], dict[str, dict[str, bool]]]:
    """Per (scenario, seed, repeat, execution) → {condition_id: {violation, task_success}}."""
    scenarios = {str(s["name"]): s for s in bundle["scenarios"]}  # type: ignore[union-attr]
    by_hash = {content_hash(t): t for t in traces.values()}
    rows: dict[tuple[str, str, int, str], dict[str, dict[str, bool]]] = {}
    for trial in bundle["trials"]:  # type: ignore[union-attr]
        if trial.get("status") != "completed":
            continue
        trace = by_hash.get(str(trial.get("trace_ref")))
        if trace is None:
            continue
        scenario = scenarios[str(trial["scenario_id"])]
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        outcome = {
            "task_success": bool(evaluate(scenario["task_success"], trace, inputs)),  # type: ignore[arg-type]
        }
        # `violation` is OPTIONAL: a budget / performance / reliability scenario
        # has no attack model and therefore no breach predicate, and the suite
        # schema says so. Reading it unconditionally turned any suite carrying
        # one clean scenario beside an attacked one into a 400 on publish —
        # and that mix is exactly what proves containment did not break the job.
        #
        # ABSENT, not False: the runner's own `_aggregate` leaves ASR off such a
        # trial, so its denominator counts attacked trials only. Contributing
        # False here would recompute 17/36 against the bundle's 17/24 and reject
        # an honest run for a mismatch this code invented.
        if scenario.get("violation") is not None:
            outcome["violation"] = bool(
                evaluate(scenario["violation"], trace, inputs),  # type: ignore[arg-type]
            )
        key = (str(trial["scenario_id"]), str(trial["seed"]), int(trial["repeat_index"]),
               str(trial.get("execution_id", "")))
        cid = str(trial["condition_id"])
        # never last-write-wins: two completed trials of the same coordinate+condition
        # would make the recomputed aggregate depend on trial array order (review r21).
        # A publish that reaches here has already passed verify_bundle (which rejects
        # a duplicate coordinate), so this is a defensive invariant, raised loudly.
        if cid in rows.get(key, {}):
            raise ValueError(
                f"duplicate experimental-unit coordinate {key!r} for condition {cid!r} — "
                "the recomputed aggregate would depend on trial array order"
            )
        rows.setdefault(key, {})[cid] = outcome
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
            continue  # not derivable — the reported tier re-applies its estimator instead
        # The marginal aggregate n is ALWAYS the completed trials OF THIS
        # CONDITION — identical to the runner's _condition_counts, for BOTH
        # designs. The pairing is a property of the TEST (McNemar over the
        # baseline∩treated intersection), never of the marginal denominator.
        # Computing the matched-pairs marginal over the all-conditions
        # intersection made the server reject honest runner bundles at
        # missingness: a single failed baseline trial shrank every condition's
        # recomputed n below the runner's per-condition marginal (review r12).
        # ...and the marginal is over the trials that MEASURED this metric. A
        # scenario with no breach predicate contributes a row without
        # `violation`, and counting it in ASR's denominator would report a rate
        # over trials that could not have violated anything.
        marg = [r[cid] for r in rows.values() if cid in r and field in r[cid]]
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
    reapplied = reapply_reported_aggregates(bundle)
    rows = _rows(bundle, traces)
    env_live = not _environment_is_deterministic(bundle)
    problems: list[str] = []
    for agg in bundle["aggregates"]:  # type: ignore[union-attr]
        metric = str(agg["metric"])
        cid = str(agg["condition_id"])
        key = (metric, cid)
        if _metric_field(metric) is None:
            # SELF-REPORTED tier: the server cannot derive this metric from any
            # trace, so it re-applies the declared estimator to the reported
            # per-trial values. That checks the arithmetic, not the
            # observations, and mints no claim (see derived_metrics).
            problems += _check_reported(bundle, dict(agg), reapplied.get(key), key)
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
        declared = agg.get("comparison_design")
        design = str(declared) if isinstance(declared, str) else "matched_pairs"
        test = agg.get("test")
        # A pairing is asserted by DECLARING one or by attaching a test that
        # rests on it — not by a marginal rate that declares nothing. Reading
        # the default "matched_pairs" off such an aggregate rejected every run a
        # CONNECTED RUNTIME produced: the environment attests independent
        # samples (Lab never saw the model), no aggregate declares a design
        # because none makes a comparison, and the server refused the whole
        # bundle over a pairing nobody claimed. An explicit declaration and a
        # paired test are both still refused over live samples, which is what
        # review r14 was actually about.
        if design == "matched_pairs" and env_live and (declared is not None or test is not None):
            problems.append(
                f"{key}: comparison_design=matched_pairs but the environment is a live model "
                "(independently sampled) — a paired test is invalid"
            )
        if test is not None:
            problems += _check_test(dict(test), metric, cid, design, rows)  # type: ignore[arg-type]
    return problems


def _check_reported(
    bundle: dict[str, object],
    agg: dict[str, object],
    rec: dict[str, object] | None,
    key: tuple[str, str],
) -> list[str]:
    """Check one self-reported aggregate: its own estimator over its own values.

    A metric the server cannot derive is not therefore unchecked. What CAN be
    checked is the step from the per-trial numbers to the headline: apply the
    declared estimator and compare. What cannot be checked is whether those
    per-trial numbers describe anything that happened — which is why this tier
    backs no `statistically_reproducible` claim, only `self_reported`.

    A statistical TEST is refused outright here. The server cannot recompute a
    test it cannot recompute the outcomes for, and a test riding along unchecked
    is precisely the fabrication surface the recompute exists to close.
    """
    problems: list[str] = []
    if agg.get("test") is not None:
        problems.append(
            f"{key}: metric {key[0]!r} is not derivable from the traces, so the "
            "server cannot recompute a comparison test over it — publish the "
            "aggregate without `test`, or declare a derivable metric"
        )
    estimator = agg.get("estimator")
    if isinstance(estimator, str) and estimator and estimator not in NUMERIC_ESTIMATORS:
        # `rate` lands here, and it is the whole point of the closed registry:
        # a RATE the server cannot derive from the traces is the laundering the
        # registry exists to stop — an arbitrary label
        # ("zero_production_incidents") carrying a proportion nothing computed.
        # The reported tier is weaker, not a way around it (review r7).
        problems.append(
            f"{key}: metric {key[0]!r} is not derivable from the traces and "
            f"estimator {estimator!r} is not one the server can re-apply "
            f"(derivable: {sorted(_METRIC_OUTCOME)}; re-appliable: "
            f"{sorted(NUMERIC_ESTIMATORS)})"
        )
        return problems
    if not _reported_values(bundle, str(key[0]), str(key[1])):
        # nothing MEASURED it. Publishing this would put a number on the page
        # that follows from no trial at all — weaker than self-reported, which at
        # least means reported by the run.
        problems.append(
            f"{key}: no completed trial reports a numeric {key[0]!r}, so the "
            "aggregate summarizes nothing the bundle contains"
        )
        return problems
    if not isinstance(estimator, str) or estimator not in NUMERIC_ESTIMATORS:
        # measured, but no declared estimator: nothing to re-apply. Taken as
        # reported, which is exactly what `self_reported` means — guessing the
        # estimator would let the uploader pick whichever function matched.
        return problems
    if rec is None:  # defensive: reapply_reported_aggregates saw what we just did
        return problems
    if int(agg["n"]) != int(rec["n"]):
        problems.append(
            f"{key}: n uploaded {agg['n']} != {int(rec['n'])} reported trial values"
        )
    if abs(float(agg["estimate"]) - float(rec["estimate"])) > 1e-9:
        problems.append(
            f"{key}: estimate uploaded {agg['estimate']} != {estimator} of the "
            f"reported values {rec['estimate']}"
        )
    ui: dict[str, object] = agg.get("interval", {})  # type: ignore[assignment]
    ri: dict[str, object] = rec.get("interval", {})  # type: ignore[assignment]
    # The server re-applies the estimator, not an inference procedure, so the
    # only interval it can check is the observed range it computes itself. An
    # aggregate declaring some other method (a bootstrap CI, say) keeps it
    # unchecked — which is the honest reading of `self_reported`, and why this
    # tier mints no claim.
    if str(ui.get("method", "")) == str(ri.get("method", "")) and (
        abs(float(ui.get("low", 0.0)) - float(ri.get("low", 0.0))) > 1e-9  # type: ignore[arg-type]
        or abs(float(ui.get("high", 0.0)) - float(ri.get("high", 0.0))) > 1e-9  # type: ignore[arg-type]
    ):
        problems.append(
            f"{key}: interval uploaded {ui} != observed range of the reported values {ri}"
        )
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
    # Every selection below is filtered by `field in …`, not just by condition: a
    # scenario with no breach predicate contributes a row without `violation`,
    # and a trial that could not have violated anything belongs in neither the
    # denominator nor a pair. Reading it unconditionally was a KeyError, which
    # the publish route surfaced as `400 malformed request: 'violation'` — so a
    # suite carrying one clean scenario beside its attacked ones could not be
    # published at all.
    if design == "independent_samples":
        base = [r[baseline_id] for r in rows.values()
                if baseline_id in r and field in r[baseline_id]]
        treat = [r[treated_id] for r in rows.values()
                 if treated_id in r and field in r[treated_id]]
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
        for r in rows.values()
        if baseline_id in r and treated_id in r
        and field in r[baseline_id] and field in r[treated_id]
    ]
    rec = mcnemar_test(pairs, vs=baseline_id)
    if str(test.get("name")) != "mcnemar":
        return [f"{key}: matched_pairs must use mcnemar, not {test.get('name')!r}"]
    # exact recomputed shape: discordant{b,c}, paired_n, effective_n (the
    # discordant n), p, status — an underpowered (inconclusive) McNemar is refused
    return _test_shape_problems(test, rec, key)
