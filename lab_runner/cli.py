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
    document = load_axl(Path(args.file))
    if args.real_kernel:
        _repin_to_real_kernel(document)
    resolved = resolve(document)

    print("[estimate]")
    _print_estimate(resolved, _estimate_model(args, resolved))
    if not _confirmed(args):
        print(
            "not confirmed — pass --yes (or answer y) to execute; nothing ran",
            file=sys.stderr,
        )
        return EXIT_UNCONFIRMED

    print("[running_local]")
    run_id = args.run_id or f"r_{content_hash(resolved.experiment)[7:15]}"
    agent = _resolve_agent_override(args.agent) if args.agent else resolved.agent
    result = run_experiment_suite(
        list(resolved.scenarios),
        resolved.manifests,
        list(resolved.conditions),
        resolved.kernel_registry,
        repeats=resolved.repeats,
        run_id=run_id,
        agent=agent,
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
        environment=_environment(resolved, _estimate_model(args, resolved)),
        trials=result.trials,
        aggregates=aggregates,
        traces=result.traces,
        packaging=dict(PACKAGING),
    )
    out = Path(args.out)
    write_bundle_dir(out, bundle, result.traces, overwrite=bool(getattr(args, "overwrite", False)))
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
    print("bit-identical: verdict-core (verdict+gate+driving value) matches the "
          "recorded traces; the replay report is byte-identical across machines")
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

    if args.server:
        return _publish_to_server(args, bundle, traces)
    if not args.out:
        raise RunnerError("publish needs --out (local publication JSON) or --server (upload)")

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
    provider = str(bundle.get("environment", {}).get("model", {}).get("provider", ""))  # type: ignore[union-attr]
    agent_note = " (scripted agent)" if provider in ("", "scripted") else ""
    for aggregate in aggregates:
        interval: dict[str, object] = aggregate["interval"]  # type: ignore[assignment]
        claims.append(
            make_claim(
                "statistically_reproducible",
                f"{aggregate['metric']} under {aggregate['condition_id']}: "
                f"{aggregate['estimate']:.2f} "
                f"[{interval['low']:.2f}, {interval['high']:.2f}] over {aggregate['n']} trials{agent_note}.",
                f"agg:{aggregate['metric']}:{aggregate['condition_id']}",
                trace_refs=trace_refs,
                aggregate_refs=aggregate_refs,
            )
        )
    publication = build_publication(
        publication_id=f"e_{bundle_ref.removeprefix('sha256:')[:32]}",  # 128-bit id (§6.3)
        bundle_ref=bundle_ref,
        question=args.question,
        origin="local",
        integrity="hash_verified",
        claims=claims,
        license_id=args.license,
        # local publish reports its own numbers; only the server independently
        # recomputes them (→ recomputed_from_traces). Be honest about which.
        statistics_integrity="self_reported" if aggregates else None,
    )
    errors = validate_artifact(publication, "publication")
    if errors:
        raise RunnerError(f"publication failed schema validation: {errors}")
    Path(args.out).write_text(json.dumps(publication, indent=2, ensure_ascii=False))
    print(f"publication {publication['publication_id']} -> {args.out}")
    print("origin=local integrity=hash_verified")
    print(f"host it: axor-lab publish {args.bundle} --question ... --server <url>")
    return EXIT_OK


def _publish_to_server(
    args: argparse.Namespace,
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
) -> int:
    """Upload via the publish handshake; the server re-verifies before minting."""
    import urllib.error
    import urllib.request

    payload = json.dumps(
        {
            "bundle": bundle,
            "traces": traces,
            "question": args.question,
            "license": args.license,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        args.server.rstrip("/") + "/api/publications",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 (operator-supplied URL)
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"server rejected publish ({exc.code}): {detail}", file=sys.stderr)
        return EXIT_FAILURE
    except urllib.error.URLError as exc:
        raise RunnerError(f"cannot reach server {args.server}: {exc.reason}") from exc
    print(f"published {result['publication_id']} -> {args.server.rstrip('/')}{result['url']}")
    return EXIT_OK


def _cmd_export_cp(args: argparse.Namespace) -> int:
    from .cp_export import CPExportError, export_cp

    bundle, _ = read_bundle_dir(Path(args.bundle))
    regressions: list[dict[str, object]] = []
    if args.pins:
        regressions = [
            {"trace_id": p["trace_id"], "expected_verdict": p["expected_verdict"]}
            for p in json.loads(Path(args.pins).read_text())
        ]
    try:
        export = export_cp(bundle, regressions)
    except CPExportError as exc:
        raise RunnerError(str(exc)) from exc
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cp-deploy.json").write_text(json.dumps(export.config, indent=2, ensure_ascii=False))
    (out / "production-todo.md").write_text(export.production_todo)
    print(f"exported CP deploy config -> {out}/cp-deploy.json")
    print(f"  config_hash (carry-over key): {export.config['config_hash']}")
    print(f"  regressions carried: {len(export.config['regressions'])}")  # type: ignore[arg-type]
    print(f"  production-todo (NOT reused): {out}/production-todo.md")
    if export.earned_bridge:
        print("  earned bridge: governance changed the outcome on your agent — "
              "run this governed config in production.")
    else:
        print("  note: no aggregate shows governance changed an outcome yet "
              "(the bridge surfaces once one does).")
    return EXIT_OK


def _cmd_import_incident(args: argparse.Namespace) -> int:
    """Second funnel: a production trace -> a trace-replay bundle you can test a
    policy against, pin, and export (control-plane-handoff.md §Second funnel)."""
    from lab_contracts import build_bundle, content_hash, validate_artifact

    trace: dict[str, object] = json.loads(Path(args.trace).read_text())
    errors = validate_artifact(trace, "trace")
    if errors:
        raise RunnerError(f"incident trace is not a conformant trace/v1: {errors}")
    scenario: dict[str, object] = json.loads(Path(args.scenario).read_text())
    manifests: list[dict[str, object]] = json.loads(Path(args.manifests).read_text())
    trial: dict[str, object] = trace["trial"]  # type: ignore[assignment]
    condition = {
        "schema_version": "condition/v1",
        "id": str(trial["condition_id"]),
        "enforcement": "on",
        "kernel": str(trace["producer"]["kernel_version"]),  # type: ignore[index]
    }
    trials = [{
        "trial_id": content_hash(trace), "scenario_id": str(trial["scenario_id"]),
        "condition_id": str(trial["condition_id"]), "seed": str(trial["seed"]),
        "repeat_index": int(trial["repeat_index"]), "status": "completed",
        "trace_ref": content_hash(trace),
    }]
    bundle = build_bundle(
        bundle_id=f"b_incident_{content_hash(trace)[7:13]}",
        created=args.created or "1970-01-01T00:00:00Z",
        scenarios=[scenario], conditions=[condition], tool_manifests=manifests,
        environment={"kernel_version": str(trace["producer"]["kernel_version"]),  # type: ignore[index]
                     "model": {"provider": "imported", "id": "production-incident"}},
        trials=trials, aggregates=[], traces={str(trace["trace_id"]): trace},
        packaging=dict(PACKAGING),
    )
    write_bundle_dir(Path(args.out), bundle, {str(trace["trace_id"]): trace})
    print(f"imported incident -> {args.out} (trace-replay bundle)")
    print(f"  replay it:  axor-lab replay {args.out}")
    print(f"  pin + export:  axor-lab pin ... && axor-lab export-cp {args.out} --pins pins.json")
    return EXIT_OK


def _cmd_import_agentdojo(args: argparse.Namespace) -> int:
    from lab_adapters import (
        UnknownSuiteError,
        available_suites,
        build_experiment_document,
    )
    from lab_contracts import condition_config_hash

    kernel = "axor-core@0.4.2"
    conditions = [
        {
            "schema_version": "condition/v1",
            "id": "ungoverned",
            "label": "ungoverned",
            "enforcement": "off",
            "kernel": kernel,
            "config_hash": condition_config_hash(kernel, None),
        },
        {
            "schema_version": "condition/v1",
            "id": "governed",
            "label": "governed",
            "enforcement": "on",
            "kernel": kernel,
            "policy": {"profile": "strict", "trust_model": "content-ledger"},
            "config_hash": condition_config_hash(
                kernel, {"profile": "strict", "trust_model": "content-ledger"}
            ),
        },
    ]
    try:
        document = build_experiment_document(
            args.suite, conditions, repeats=args.repeats, agent_ref=args.agent_ref
        )
    except UnknownSuiteError as exc:
        print(f"error: {exc}; available: {list(available_suites())}", file=sys.stderr)
        return EXIT_VALIDATION
    # a materialized suite that cannot resolve is a bug — fail loudly, not silently
    resolve(document)
    Path(args.out).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    scenarios = document["scenarios"]  # type: ignore[index]
    print(f"imported AgentDojo '{args.suite}': {len(scenarios)} scenario(s) -> {args.out}")
    print(f"  run: axor-lab run {args.out} --out ./bundle --yes")
    return EXIT_OK


# -- helpers ------------------------------------------------------------------


def _repin_to_real_kernel(document: dict[str, object]) -> None:
    """Repin every enforcement-on condition to the installed axor-core version,
    so the run governs with the REAL kernel (not the reference)."""
    from lab_contracts import condition_config_hash
    from lab_runner import axor_available, real_kernel_version

    if not axor_available():
        raise RunnerError("--real-kernel requested but axor-core is not installed")
    version = real_kernel_version()
    experiment: dict[str, object] = document["experiment"]  # type: ignore[assignment]
    for condition in experiment.get("conditions", []):  # type: ignore[union-attr]
        if condition.get("enforcement") == "on":
            condition["kernel"] = version
            condition["config_hash"] = condition_config_hash(version, condition.get("policy"))
    print(f"  repinned governed condition(s) to the real kernel: {version}")


def _resolve_agent_override(spec: str) -> object:
    """Build a BYOK agent from --agent (cassette:<file> | anthropic:<model>)."""
    from lab_agent import AnthropicBackend, FileCassetteAgent, WrappedModelAgent

    kind, _, param = spec.partition(":")
    if kind == "cassette":
        return FileCassetteAgent(path=Path(param))
    if kind == "anthropic":
        return WrappedModelAgent(backend=AnthropicBackend(model=param or "claude-opus-4-8"))
    raise RunnerError(f"unknown --agent {spec!r}; use cassette:<file> or anthropic:<model>")


def _estimate_model(args: argparse.Namespace, resolved: ResolvedExperiment) -> str:
    if args.agent:
        kind, _, param = args.agent.partition(":")
        if kind == "cassette":
            return "cassette (recorded transcript)"
        return param or kind
    return str(resolved.experiment["agent_ref"])


def _print_estimate(resolved: ResolvedExperiment, model: str) -> None:
    from lab_agent import estimate_cost

    print(
        f"  {len(resolved.scenarios)} scenario(s) x {len(resolved.conditions)} condition(s) "
        f"x {resolved.repeats} repeat(s) = {resolved.trial_count} trials"
    )
    estimate = estimate_cost(resolved.trial_count, model)
    print(f"  agent: {model} -> {estimate.line()}")


def _confirmed(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        return False
    answer = input("Proceed with the run? [y/N] ")
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


def _environment(resolved: ResolvedExperiment, model: str) -> dict[str, object]:
    """Record the ACTUAL agent that ran — not always 'scripted' (review §6.1).
    The bundle stays self-describing: kernel, the real model provider/id, the
    experiment id, and (when imported) the dataset version."""
    kernels = sorted({str(c["kernel"]) for c in resolved.conditions})
    provider = model.split(":", 1)[0] if ":" in model else (
        "scripted" if model.startswith("scripted") else
        "anthropic" if model.startswith("claude") else
        "cassette" if model.startswith("cassette") else "unknown"
    )
    env: dict[str, object] = {
        "kernel_version": kernels[0] if len(kernels) == 1 else ",".join(kernels),
        "model": {"provider": provider, "id": model,
                  "inference_params": {"experiment_id": str(resolved.experiment["id"])}},
    }
    return env


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
    p_run.add_argument(
        "--agent", default=None,
        help="BYOK agent override: cassette:<file> (offline) or anthropic:<model>",
    )
    p_run.add_argument(
        "--real-kernel", action="store_true",
        help="govern with the installed axor-core kernel (not the reference)",
    )
    p_run.add_argument(
        "--overwrite", action="store_true",
        help="replace a non-empty --out directory (clears stale traces first)",
    )
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
    p_publish.add_argument("--out", help="write publication/v1 JSON locally")
    p_publish.add_argument("--server", help="upload via the publish handshake to this base URL")
    p_publish.add_argument("--license", default="CC-BY-4.0")
    p_publish.set_defaults(func=_cmd_publish)

    p_export = sub.add_parser(
        "export-cp", help="export the validated policy + manifests + regressions to a CP config"
    )
    p_export.add_argument("bundle")
    p_export.add_argument("--out", required=True, help="output directory for cp-deploy.json")
    p_export.add_argument("--pins", default=None, help="regression pins to carry over")
    p_export.set_defaults(func=_cmd_export_cp)

    p_incident = sub.add_parser(
        "import-incident", help="build a trace-replay bundle from a production incident trace"
    )
    p_incident.add_argument("--trace", required=True, help="a conformant trace/v1 JSON")
    p_incident.add_argument("--scenario", required=True, help="scenario/v1 JSON")
    p_incident.add_argument("--manifests", required=True, help="tool-manifest/v1 list JSON")
    p_incident.add_argument("--out", required=True)
    p_incident.add_argument("--created", default=None)
    p_incident.set_defaults(func=_cmd_import_incident)

    p_import = sub.add_parser(
        "import-agentdojo", help="materialize a curated AgentDojo suite as an .axl file"
    )
    p_import.add_argument("suite", nargs="?", default="banking")
    p_import.add_argument("--out", required=True, help="output .axl path")
    p_import.add_argument("--repeats", type=int, default=30)
    p_import.add_argument("--agent-ref", default="scripted@0.6")
    p_import.set_defaults(func=_cmd_import_agentdojo)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
