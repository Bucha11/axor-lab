"""axor-lab — the local runner CLI (contracts/runner-protocol.md).

    axor-lab validate experiment.axl
    axor-lab run experiment.axl --out ./bundle [--yes]
    axor-lab replay ./bundle
    axor-lab pin ./bundle <trace_id> <ALLOW|DENY> --out pins.json
    axor-lab regress ./bundle --pins pins.json [--disable-taint-floor] [--kernel V]
    axor-lab evidence ./bundle <trace_id> [--twin <trace_id>]
    axor-lab publish ./bundle --question "…" --out publication.json

Exit codes: 0 ok · 1 runtime/integrity failure · 2 validation errors ·
3 estimate not confirmed · 4 regression differs from pinned expected.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from lab_analysis import binary_aggregate, mcnemar_test, missingness
from lab_contracts import (
    BundleIntegrityError,
    build_bundle,
    build_publication,
    content_hash,
    make_claim,
    validate_artifact,
)

from .bundle_io import PACKAGING, read_bundle_dir, write_bundle_dir
from .errors import ExperimentFileError, RunnerError
from .evidence import build_evidence_case
from .experiment_file import ResolvedExperiment, load_axl, resolve
from .kernel import Kernel, default_registry
from .regression import STATUS_DIFFERS, RegressionPin, check_pins
from .replay import replay_bundle
from .runner import run_experiment_suite

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_VALIDATION = 2
EXIT_UNCONFIRMED = 3
EXIT_REGRESSION_DIFFERS = 4

_METRIC_ASR = "ASR"
_METRIC_UTILITY = "task_success_rate"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ExperimentFileError as exc:
        for error in exc.errors:
            print(f"error: {error}", file=sys.stderr)
        print(f"{len(exc.errors)} validation error(s) — stage: validating", file=sys.stderr)
        return EXIT_VALIDATION
    except BundleIntegrityError as exc:
        print(f"bundle integrity failure: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE


# -- commands -----------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    resolved = resolve(load_axl(Path(args.file)))
    print(f"valid: {resolved.experiment['id']}")
    print(
        f"  scenarios={len(resolved.scenarios)} conditions={len(resolved.conditions)} "
        f"repeats={resolved.repeats} -> {resolved.trial_count} trials"
    )
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    print("[validating]")
    resolved = resolve(load_axl(Path(args.file)))

    print("[estimate]")
    _print_estimate(resolved)
    if not _confirmed(args):
        print(
            "not confirmed — pass --yes (or answer y) to execute; nothing ran",
            file=sys.stderr,
        )
        return EXIT_UNCONFIRMED

    print("[running_local]")
    run_id = args.run_id or f"r_{content_hash(resolved.experiment)[7:15]}"
    result = run_experiment_suite(
        list(resolved.scenarios),
        resolved.manifests,
        list(resolved.conditions),
        resolved.kernel_registry,
        repeats=resolved.repeats,
        run_id=run_id,
        agent=resolved.agent,
    )
    print(f"  {len(result.trials)} trials completed")

    print("[analyzing]")
    aggregates = _aggregates(resolved, result)
    summary = missingness(result.trials)
    print(f"  {summary.display()}")
    for aggregate in aggregates:
        _print_aggregate(aggregate)

    print("[uploading_artifacts]  (local: writing bundle directory)")
    created = args.created or datetime.now(timezone.utc).isoformat(timespec="seconds")
    bundle = build_bundle(
        bundle_id=f"b_{run_id}",
        created=created,
        scenarios=list(resolved.scenarios),
        conditions=list(resolved.conditions),
        tool_manifests=list(resolved.manifests.values()),
        environment=_environment(resolved),
        trials=result.trials,
        aggregates=aggregates,
        traces=result.traces,
        packaging=dict(PACKAGING),
    )
    out = Path(args.out)
    write_bundle_dir(out, bundle, result.traces)
    print(f"[completed]  bundle: {out}/bundle.json ({len(result.traces)} traces)")
    print(f"  reproduce verdicts (exact):    axor-lab replay {out}")
    print(f"  reproduce behavior (fresh):    axor-lab run {args.file} --out <new-dir>")
    return EXIT_OK


def _cmd_replay(args: argparse.Namespace) -> int:
    bundle, traces = read_bundle_dir(Path(args.bundle))
    versions = tuple(str(c["kernel"]) for c in bundle["conditions"])  # type: ignore[union-attr]
    kernels = {k.version: k for k in default_registry(versions).kernels}
    report = replay_bundle(bundle, traces, kernels)
    denies = sum(1 for vs in report.verdicts().values() for v in vs if v == "DENY")
    allows = sum(1 for vs in report.verdicts().values() for v in vs if v == "ALLOW")
    print(f"replayed {len(report.decisions)} trace(s): {denies} DENY, {allows} ALLOW")
    if not report.bit_identical:
        print("MISMATCH: recomputed verdicts differ from recorded", file=sys.stderr)
        return EXIT_FAILURE
    print("bit-identical: recomputed verdicts match the recorded traces exactly")
    print("(exact claim — no CI; behavioral outcomes reproduce statistically via `run`)")
    return EXIT_OK


def _cmd_pin(args: argparse.Namespace) -> int:
    _, traces = read_bundle_dir(Path(args.bundle))
    trace = traces.get(args.trace_id)
    if trace is None:
        raise RunnerError(f"trace {args.trace_id} not found in bundle")
    out = Path(args.out)
    pins: list[dict[str, object]] = json.loads(out.read_text()) if out.is_file() else []
    pins = [p for p in pins if p["trace_id"] != args.trace_id]
    pins.append(
        {
            "trace_id": args.trace_id,
            "trace_ref": content_hash(trace),
            "expected_verdict": args.expected,
        }
    )
    out.write_text(json.dumps(pins, indent=2))
    print(f"pinned {args.trace_id} -> expected {args.expected} ({out})")
    return EXIT_OK


def _cmd_regress(args: argparse.Namespace) -> int:
    bundle, traces = read_bundle_dir(Path(args.bundle))
    pins_raw: list[dict[str, object]] = json.loads(Path(args.pins).read_text())
    pins = tuple(
        RegressionPin(
            trace_id=str(p["trace_id"]),
            trace_ref=str(p["trace_ref"]),
            expected_verdict=str(p["expected_verdict"]),
        )
        for p in pins_raw
    )
    condition = _enforcing_condition(bundle, args.condition)
    version = args.kernel or str(condition["kernel"])
    kernel = Kernel(version=version, taint_floor_enabled=not args.disable_taint_floor)
    inputs = _scenario_inputs(bundle, traces, pins)
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    results = check_pins(pins, traces, condition, kernel, manifests, inputs)
    differs = [r for r in results if r["status"] == STATUS_DIFFERS]
    for result in results:
        print(
            f"{result['trace_id']}: expected {result['expected']}, got {result['actual']} "
            f"under {result['kernel']} -> {result['status']}"
        )
    if differs:
        print(
            f"{len(differs)} pin(s) differ from expected — label each as regression "
            "or approved baseline update (not auto-resolved)",
            file=sys.stderr,
        )
        return EXIT_REGRESSION_DIFFERS
    print(f"all {len(results)} pin(s) match expected verdicts")
    return EXIT_OK


def _cmd_evidence(args: argparse.Namespace) -> int:
    bundle, traces = read_bundle_dir(Path(args.bundle))
    trace = traces.get(args.trace_id)
    if trace is None:
        raise RunnerError(f"trace {args.trace_id} not found in bundle")
    twin = traces.get(args.twin) if args.twin else None
    if args.twin and twin is None:
        raise RunnerError(f"twin trace {args.twin} not found in bundle")
    scenario = _scenario_for(bundle, trace)
    condition = _enforcing_condition(bundle, None)
    kernel = default_registry((str(condition["kernel"]),)).get(str(condition["kernel"]))
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    case = build_evidence_case(trace, scenario, condition, kernel, manifests, governed_twin=twin)
    print(json.dumps(case, indent=2, ensure_ascii=False))
    return EXIT_OK


def _cmd_publish(args: argparse.Namespace) -> int:
    bundle, traces = read_bundle_dir(Path(args.bundle))
    versions = tuple(str(c["kernel"]) for c in bundle["conditions"])  # type: ignore[union-attr]
    kernels = {k.version: k for k in default_registry(versions).kernels}
    report = replay_bundle(bundle, traces, kernels)
    if not report.bit_identical:
        print("refusing to publish: recomputed verdicts differ from recorded", file=sys.stderr)
        return EXIT_FAILURE

    bundle_ref = content_hash(bundle)
    trace_refs = frozenset(content_hash(t) for t in traces.values())
    aggregates: list[dict[str, object]] = bundle["aggregates"]  # type: ignore[assignment]
    aggregate_refs = frozenset(
        f"agg:{a['metric']}:{a['condition_id']}" for a in aggregates
    )
    claims: list[dict[str, object]] = []
    denied = _first_denied_trace(traces)
    if denied is not None:
        kernel_version = str(denied["producer"]["kernel_version"])  # type: ignore[index]
        claims.append(
            make_claim(
                "exactly_replayable",
                f"On trace {denied['trace_id']}, {kernel_version} returns DENY; "
                "the driving argument is untrusted_derived.",
                content_hash(denied),
                trace_refs=trace_refs,
                aggregate_refs=aggregate_refs,
            )
        )
    for aggregate in aggregates:
        interval: dict[str, object] = aggregate["interval"]  # type: ignore[assignment]
        claims.append(
            make_claim(
                "statistically_reproducible",
                f"{aggregate['metric']} under {aggregate['condition_id']}: "
                f"{aggregate['estimate']:.2f} "
                f"[{interval['low']:.2f}, {interval['high']:.2f}] over {aggregate['n']} live trials.",
                f"agg:{aggregate['metric']}:{aggregate['condition_id']}",
                trace_refs=trace_refs,
                aggregate_refs=aggregate_refs,
            )
        )
    publication = build_publication(
        publication_id=f"e_{bundle_ref[7:15]}",
        bundle_ref=bundle_ref,
        question=args.question,
        origin="local",
        integrity="hash_verified",
        claims=claims,
        license_id=args.license,
    )
    errors = validate_artifact(publication, "publication")
    if errors:
        raise RunnerError(f"publication failed schema validation: {errors}")
    Path(args.out).write_text(json.dumps(publication, indent=2, ensure_ascii=False))
    print(f"publication {publication['publication_id']} -> {args.out}")
    print("origin=local integrity=hash_verified (server-side signing/hosting is Phase 4)")
    return EXIT_OK


# -- helpers ------------------------------------------------------------------


def _print_estimate(resolved: ResolvedExperiment) -> None:
    print(
        f"  {len(resolved.scenarios)} scenario(s) x {len(resolved.conditions)} condition(s) "
        f"x {resolved.repeats} repeat(s) = {resolved.trial_count} trials"
    )
    agent_ref = str(resolved.experiment["agent_ref"])
    if agent_ref.startswith("scripted"):
        print(f"  agent: {agent_ref} -> estimated inference cost: $0.00 (no model calls)")
    else:
        print(f"  agent: {agent_ref} -> BYOK inference; cost depends on your provider")


def _confirmed(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        return False
    answer = input(f"Proceed with the run? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _aggregates(
    resolved: ResolvedExperiment, result: "object"
) -> list[dict[str, object]]:
    aggregates: list[dict[str, object]] = []
    baseline = next(
        (str(c["id"]) for c in resolved.conditions if c["enforcement"] == "off"), None
    )
    for condition in resolved.conditions:
        condition_id = str(condition["id"])
        trials = [t for t in result.trials if t["condition_id"] == condition_id]  # type: ignore[attr-defined]
        outcomes = [result.outcomes[str(t["trial_id"])] for t in trials]  # type: ignore[attr-defined]
        n = len(outcomes)
        for metric, successes in (
            (_METRIC_ASR, sum(1 for o in outcomes if o.violation)),
            (_METRIC_UTILITY, sum(1 for o in outcomes if o.task_success)),
        ):
            test = None
            if baseline is not None and condition_id != baseline and metric == _METRIC_ASR:
                pairs = result.pairs(baseline, condition_id, metric="ASR")  # type: ignore[attr-defined]
                test = mcnemar_test(pairs, vs=baseline)
            aggregates.append(binary_aggregate(metric, condition_id, successes, n, test=test))
    return aggregates


def _print_aggregate(aggregate: dict[str, object]) -> None:
    interval: dict[str, object] = aggregate["interval"]  # type: ignore[assignment]
    line = (
        f"  {aggregate['metric']}[{aggregate['condition_id']}] = {aggregate['estimate']:.2f} "
        f"[{interval['low']:.2f}, {interval['high']:.2f}] n={aggregate['n']}"
    )
    test: dict[str, object] | None = aggregate.get("test")  # type: ignore[assignment]
    if test is not None:
        discordant: dict[str, object] = test["discordant"]  # type: ignore[assignment]
        line += (
            f"  mcnemar vs {test['vs']}: b={discordant['b']} c={discordant['c']} "
            f"p={float(test['p']):.2g}"  # type: ignore[arg-type]
        )
    print(line)


def _environment(resolved: ResolvedExperiment) -> dict[str, object]:
    kernels = sorted({str(c["kernel"]) for c in resolved.conditions})
    return {
        "kernel_version": kernels[0] if len(kernels) == 1 else ",".join(kernels),
        "model": {
            "provider": "scripted",
            "id": str(resolved.experiment["agent_ref"]),
        },
    }


def _enforcing_condition(
    bundle: dict[str, object], condition_id: str | None
) -> dict[str, object]:
    conditions: list[dict[str, object]] = bundle["conditions"]  # type: ignore[assignment]
    if condition_id is not None:
        for condition in conditions:
            if condition["id"] == condition_id:
                return condition
        raise RunnerError(f"condition {condition_id} not in bundle")
    for condition in conditions:
        if condition["enforcement"] == "on":
            return condition
    raise RunnerError("bundle has no enforcement-on condition")


def _scenario_for(bundle: dict[str, object], trace: dict[str, object]) -> dict[str, object]:
    scenario_id = str(trace["trial"]["scenario_id"])  # type: ignore[index]
    for scenario in bundle["scenarios"]:  # type: ignore[union-attr]
        if scenario["name"] == scenario_id:
            return scenario
    raise RunnerError(f"scenario {scenario_id} not in bundle")


def _scenario_inputs(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    pins: tuple[RegressionPin, ...],
) -> dict[str, object]:
    if not pins:
        raise RunnerError("no pins to check")
    trace = traces.get(pins[0].trace_id)
    if trace is None:
        raise RunnerError(f"pinned trace {pins[0].trace_id} not found in bundle")
    return _scenario_for(bundle, trace).get("inputs", {})  # type: ignore[return-value]


def _first_denied_trace(traces: dict[str, dict[str, object]]) -> dict[str, object] | None:
    for trace in sorted(traces.values(), key=lambda t: str(t["trace_id"])):
        for event in trace["events"]:  # type: ignore[union-attr]
            if event.get("type") == "gate_decision" and event["decision"]["verdict"] == "DENY":  # type: ignore[index]
                return trace
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="axor-lab", description="Axor Lab local runner (runner-protocol.md)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate an .axl experiment file")
    p_validate.add_argument("file")
    p_validate.set_defaults(func=_cmd_validate)

    p_run = sub.add_parser("run", help="resolve -> estimate -> execute -> analyze -> bundle")
    p_run.add_argument("file")
    p_run.add_argument("--out", required=True, help="bundle output directory")
    p_run.add_argument("--yes", action="store_true", help="confirm the estimate non-interactively")
    p_run.add_argument("--run-id", default=None)
    p_run.add_argument("--created", default=None, help="override bundle timestamp (RFC3339)")
    p_run.set_defaults(func=_cmd_run)

    p_replay = sub.add_parser("replay", help="recompute verdicts over frozen traces (exact)")
    p_replay.add_argument("bundle")
    p_replay.set_defaults(func=_cmd_replay)

    p_pin = sub.add_parser("pin", help="pin (trace, expected verdict) as a regression case")
    p_pin.add_argument("bundle")
    p_pin.add_argument("trace_id")
    p_pin.add_argument("expected", choices=["ALLOW", "DENY"])
    p_pin.add_argument("--out", required=True, help="pins file (JSON list)")
    p_pin.set_defaults(func=_cmd_pin)

    p_regress = sub.add_parser("regress", help="re-run pinned traces; surface any change")
    p_regress.add_argument("bundle")
    p_regress.add_argument("--pins", required=True)
    p_regress.add_argument("--kernel", default=None, help="kernel version override")
    p_regress.add_argument(
        "--disable-taint-floor", action="store_true",
        help="check pins under a kernel variant with taint_floor off",
    )
    p_regress.add_argument("--condition", default=None, help="condition id (default: enforcement on)")
    p_regress.set_defaults(func=_cmd_regress)

    p_evidence = sub.add_parser("evidence", help="render the EvidenceCase for one trace")
    p_evidence.add_argument("bundle")
    p_evidence.add_argument("trace_id")
    p_evidence.add_argument("--twin", default=None, help="observed governed twin trace id")
    p_evidence.set_defaults(func=_cmd_evidence)

    p_publish = sub.add_parser("publish", help="mint a publication/v1 from a verified bundle")
    p_publish.add_argument("bundle")
    p_publish.add_argument("--question", required=True)
    p_publish.add_argument("--out", required=True)
    p_publish.add_argument("--license", default="CC-BY-4.0")
    p_publish.set_defaults(func=_cmd_publish)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
