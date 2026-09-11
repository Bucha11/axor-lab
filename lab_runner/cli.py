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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from lab_analysis import binary_aggregate, mcnemar_test, missingness, two_proportion_test
from lab_analysis.errors import AnalysisError
from lab_contracts import (
    BundleIntegrityError,
    ContractsError,
    build_bundle,
    build_publication,
    content_hash,
    finalize_publication_id,
    make_claim,
    validate_artifact,
)

from .bundle_io import (
    PACKAGING,
    read_bundle_dir,
    read_bundle_source,
    write_bundle_dir,
    write_superseded_attempts,
)
from lab_suite.errors import SuiteError

from .errors import ExperimentFileError, RunnerError
from .invariants import STATUS_ERROR, STATUS_FAILED
from .verdicts import contained

# The CLI is a COMPOSITION ROOT, not part of the platform spine: it wires
# whatever the user's command needs. Every governance command below —
# validate/run/replay/pin/regress/evidence/export-cp on an `.axl` experiment —
# is the governance capability's own surface, so it reaches into the capability
# here and nowhere else in `lab_runner`. That is why this file is a declared
# wiring point in `tests/test_capability_boundary.py`.
from lab_capabilities.governance import (
    AxorKernel,
    RegressionPin,
    ResolvedExperiment,
    build_evidence_case,
    check_pins,
    default_registry,
    evidence_condition,
    governor_config,
    load_axl,
    pin,
    replay_bundle,
    resolve,
    resolve_candidate_kernel_for_trace,
    resolve_kernel,
    run_experiment_suite,
    validate_twin,
)
from lab_capabilities.governance.claims import deny_claim_text
from lab_capabilities.governance.regression import STATUS_DIFFERS, STATUS_MATCHES

# Statistics failures are a separate hierarchy from RunnerError;
# main() maps them to stable exit codes instead of leaking a traceback
_AGENT_ANALYSIS_ERRORS = (RunnerError, AnalysisError)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_VALIDATION = 2
EXIT_UNCONFIRMED = 3
EXIT_REGRESSION_DIFFERS = 4
# a signed receipt whose signature could NOT be checked (no key supplied, or
# PyNaCl absent) — distinct from a clean pass (0) and from a tampered/invalid
# receipt (1), so automation never reads "unverified" as "verified" (review r15)
EXIT_UNVERIFIED = 5

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
    except _AGENT_ANALYSIS_ERRORS as exc:
        # analysis failures (
        # ProtocolViolation, AnalysisError, InsufficientDataError) are their own
        # hierarchies, not RunnerError — catch them so the user gets a stable
        # message + exit code instead of a Python traceback
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except ContractsError as exc:
        # claim typing / contract-layer errors surface as validation failures
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_VALIDATION
    except SuiteError as exc:
        # an unknown suite id / invalid manifest from the suite commands is the
        # user's to fix — a clean message + exit code, not a raw traceback
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_VALIDATION


# -- commands -----------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    from lab_service import validate_experiment

    result = validate_experiment(load_axl(Path(args.file)))
    print(f"valid: {result.experiment_id}")
    print(
        f"  scenarios={result.scenarios} conditions={result.conditions} "
        f"repeats={result.repeats} -> {result.trial_count} trials"
    )
    return EXIT_OK


def _cmd_suites(args: argparse.Namespace) -> int:
    """The Suite Catalog, from the terminal.

    Same payload the screen endpoint serves (`lab_suite.suite_catalog`), so the
    two cannot disagree about which suites exist.
    """
    from lab_suite import suite_catalog

    for card in suite_catalog():
        available = bool(card.get("available"))
        mark = " " if available else "!"
        caps = ",".join(str(c) for c in card.get("capabilities") or ()) or "-"
        print(f"{mark} {card['id']:<18} {card['name']:<22} capabilities={caps}")
        if card.get("description"):
            print(f"    {card['description']}")
        if not available:
            print(f"    UNAVAILABLE: {card.get('reason', 'not implemented')}")
    return EXIT_OK


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the screen API and the built web app from one process.

    Without this the app had no documented way to start: `python -m lab_server`
    runs the CATALOG server (publications), which is a different surface and
    serves none of the screens. An interface a user cannot launch is the same
    island the Suite SDK was.
    """
    import os

    from lab_server.runtime_jobs import make_runtime_server
    from lab_server.static import default_root

    token = args.control_token or os.environ.get("AXOR_LAB_CONTROL_TOKEN")
    data_dir = getattr(args, "data_dir", None) or os.environ.get("AXOR_LAB_DATA_DIR")
    billing_secret = (getattr(args, "billing_webhook_secret", None)
                      or os.environ.get("AXOR_LAB_BILLING_WEBHOOK_SECRET"))
    plans_file = getattr(args, "plans_file", None) or os.environ.get("AXOR_LAB_PLANS_FILE")
    plan_catalog = None
    if plans_file:
        import json

        plan_catalog = json.loads(Path(plans_file).read_text())
    # axor-identity login: verify human access tokens against the identity JWKS,
    # supplied as a URL (fetched once at boot) or a file. Absent, only static
    # control/member tokens authenticate.
    jwks_url = (getattr(args, "identity_jwks_url", None)
                or os.environ.get("AXOR_LAB_IDENTITY_JWKS_URL"))
    jwks_file = (getattr(args, "identity_jwks_file", None)
                 or os.environ.get("AXOR_LAB_IDENTITY_JWKS_FILE"))
    identity_jwks = None
    if jwks_file:
        import json

        identity_jwks = json.loads(Path(jwks_file).read_text())
    elif jwks_url:
        from lab_server.identity_client import fetch_jwks

        identity_jwks = fetch_jwks(jwks_url)
    identity_issuer = (getattr(args, "identity_issuer", None)
                       or os.environ.get("AXOR_LAB_IDENTITY_ISSUER") or "axor-identity")
    guest_sessions = (getattr(args, "guest_sessions", False)
                      or os.environ.get("AXOR_LAB_GUEST_SESSIONS") == "1")
    server = make_runtime_server(
        host=args.host, port=args.port, control_token=token, data_dir=data_dir,
        billing_webhook_secret=billing_secret, plan_catalog=plan_catalog,
        identity_jwks=identity_jwks, identity_issuer=identity_issuer,
        guest_sessions=guest_sessions)
    site = default_root()
    print(f"axor-lab on http://{args.host}:{args.port}")
    print(f"  storage:  {'durable → ' + str(data_dir) if data_dir else 'in-memory (lost on restart)'}")
    if site is None:
        # said plainly rather than serving a 404 the user has to diagnose
        print("  web app:  NOT BUILT — run `npm --prefix web install && "
              "npm --prefix web run build`")
    else:
        print(f"  web app:  {site}")
    print(
        "  auth:     "
        + ("token-gated" if token else
           "OPEN — every screen endpoint is unauthenticated. Local dev only; "
           "pass --control-token before exposing this.")
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return EXIT_OK


def _cmd_suite_yaml(args: argparse.Namespace) -> int:
    """Print a suite manifest as YAML — the Builder's third editing mode, from
    the terminal.

    Round-tripping through this and back must not change the manifest; that is
    acceptance criterion 8.5 and it is pinned in
    `tests/test_yaml_mode_round_trip.py`.
    """
    from lab_service import resolve_suite_target
    from lab_suite import to_yaml
    from lab_suite.yaml_mode import YamlUnavailable

    manifest, _ = resolve_suite_target(args.suite)
    try:
        print(to_yaml(manifest), end="")
    except YamlUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


def _cmd_run_suite(args: argparse.Namespace) -> int:
    """The suite lifecycle, printed as it goes. Plan and execution live in
    `lab_service.suites`; the confirm gate is the face's."""
    from lab_service import Outcome, execute_suite_run, plan_suite_run

    print("[validating]")
    plan = plan_suite_run(args.suite, run_id=args.run_id)
    if plan.outcome is Outcome.VALIDATION:
        for error in plan.errors:
            print(f"  {error}", file=sys.stderr)
        return EXIT_VALIDATION
    print(f"valid: {plan.suite_id}")
    print(
        f"  scenarios={plan.scenarios} conditions={plan.conditions} "
        f"repeats={plan.repeats} -> {plan.trials} trials"
        + ("  (single-arm: no governance)" if plan.single_arm else "")
    )

    print("[estimate]")
    print(f"  {plan.trials} trial(s), local simulated tools, no paid inference")
    if not _confirmed(args):
        print(
            "not confirmed — pass --yes (or answer y) to execute; nothing ran",
            file=sys.stderr,
        )
        return EXIT_UNCONFIRMED

    print("[running_local]")
    result = execute_suite_run(
        plan,
        out=Path(args.out),
        created=args.created,
        overwrite=bool(getattr(args, "overwrite", False)),
        command=f"axor-lab run-suite {args.suite}",
    )
    if result.schema_errors:
        for error in result.schema_errors:
            print(f"  [schema] {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(
        f"  planned {result.planned}: "
        + ", ".join(f"{n} {status}" for status, n in sorted(result.by_status.items()))
    )

    print("[analyzing]")
    print(f"  {result.missingness}")
    for aggregate in result.aggregates:
        _print_aggregate(aggregate)
    for entry in result.invariants:
        print(f"  invariant {entry.regression_id}: {entry.status}"
              + (f" — {entry.detail}" if entry.detail else ""))

    print("[uploading_artifacts]  (local: writing artifact + bundle)")
    print(f"[completed]  artifact: {result.directory}/artifact.json "
          f"({result.trace_count} traces)")
    print(f"  reproduce verdicts (exact):    axor-lab replay {result.directory}")
    # Three outcomes, three exit codes — collapsing them loses the distinction the
    # whole invariant model rests on. `failed` is a violated invariant; `error` is
    # one that could NOT be evaluated, which is not a pass either but is a
    # different thing to tell a CI than "your change regressed".
    if result.violated:
        print("  invariants VIOLATED: "
              + ", ".join(str(r.regression_id) for r in result.violated), file=sys.stderr)
        return EXIT_REGRESSION_DIFFERS
    if result.unevaluable:
        print("  invariants could not be evaluated: "
              + ", ".join(f"{r.regression_id} ({r.detail})" for r in result.unevaluable),
              file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    """The lifecycle stages a run passes through, printed as it goes. The work on
    either side of the confirm gate lives in `lab_service.experiments`; the GATE
    is the face's — a prompt here, `POST /runs/{id}/confirm` over HTTP."""
    from lab_service import execute_run, plan_run

    print("[validating]")
    plan = plan_run(
        load_axl(Path(args.file)),
        real_kernel=bool(args.real_kernel),
        run_id=args.run_id,
    )
    if plan.repinned_kernel:
        print("  repinned ALL conditions (baseline + governed) to the real kernel: "
              f"{plan.repinned_kernel}")

    print("[estimate]")
    _print_estimate(plan.resolved)
    if not _confirmed(args):
        print(
            "not confirmed — pass --yes (or answer y) to execute; nothing ran",
            file=sys.stderr,
        )
        return EXIT_UNCONFIRMED

    print("[running_local]")
    result = execute_run(
        plan,
        out=Path(args.out),
        created=args.created,
        overwrite=bool(getattr(args, "overwrite", False)),
    )
    # report the plan outcome by status separately — "N trials completed" over
    # every trial record was misleading, since the records also hold failed and
    # cost-excluded trials (review r14). planned = everything the plan intended.
    print(
        f"  planned {result.planned}: {result.completed} completed, "
        f"{result.failed} failed, {result.excluded} excluded"
    )
    print("[analyzing]")
    # missingness FIRST (denominator honesty)
    print(f"  {result.missingness}")
    for aggregate in result.aggregates:
        _print_aggregate(aggregate)

    print("[uploading_artifacts]  (local: writing bundle directory)")
    if result.superseded_log is not None:
        print(f"  superseded attempts: {result.superseded_log} ({result.superseded_count})")
    print(f"[completed]  bundle: {result.directory}/bundle.json "
          f"({result.trace_count} traces)")
    print(f"  reproduce verdicts (exact):    axor-lab replay {result.directory}")
    print(f"  reproduce behavior (fresh):    axor-lab run {args.file} --out <new-dir>")
    return EXIT_OK


def _cmd_replay(args: argparse.Namespace) -> int:
    from lab_service import replay_source

    result = replay_source(Path(args.bundle))
    print(f"replayed {result.decisions} trace(s): {result.denies} DENY, {result.allows} ALLOW")
    if not result.bit_identical:
        print("MISMATCH: recomputed verdicts differ from recorded", file=sys.stderr)
        return EXIT_FAILURE
    print("bit-identical: verdict-core (verdict+gate+driving value) matches the "
          "recorded traces; the replay report is byte-identical across machines")
    print("(exact claim — no CI; behavioral outcomes reproduce statistically via `run`)")
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    """Standalone, offline verification of a downloaded reproduction package —
    NO server trusted. The checks live in `lab_service.packages`; this prints
    them and maps the outcome to an exit code."""
    from lab_service import CheckStatus, Outcome, verify_package

    result = verify_package(
        Path(args.package),
        pubkey=getattr(args, "pubkey", None),
        author=getattr(args, "author", None),
        server_pubkey=getattr(args, "server_pubkey", None),
        server=getattr(args, "server", None),
        server_key_id=getattr(args, "server_key_id", None),
        allow_bare=bool(getattr(args, "allow_bare", False)),
        allow_unsigned_server=bool(getattr(args, "allow_unsigned_server", False)),
    )
    for check in result.checks:
        stream = sys.stdout if check.status is CheckStatus.OK else sys.stderr
        print(f"{check.name}: {check.message}", file=stream)
    return {
        Outcome.OK: EXIT_OK,
        Outcome.VALIDATION: EXIT_VALIDATION,
        Outcome.UNVERIFIED: EXIT_UNVERIFIED,
    }.get(result.outcome, EXIT_FAILURE)


def _cmd_pin(args: argparse.Namespace) -> int:
    from lab_service import pin_trace

    _, traces = read_bundle_dir(Path(args.bundle))
    out = Path(args.out)
    existing: list[dict[str, object]] = json.loads(out.read_text()) if out.is_file() else []
    result = pin_trace(traces, args.trace_id, args.expected, existing=existing)
    out.write_text(json.dumps(list(result.pins), indent=2))
    print(f"pinned {result.trace_id} -> expected {list(result.expected_sequence)} ({out})")
    return EXIT_OK


def _cmd_regress(args: argparse.Namespace) -> int:
    from lab_service import Outcome, check_regression

    bundle, traces = read_bundle_dir(Path(args.bundle))
    result = check_regression(
        bundle, traces, json.loads(Path(args.pins).read_text()),
        condition_id=args.condition,
        kernel_override=args.kernel,
        disable_taint_floor=bool(args.disable_taint_floor),
    )
    for entry in result.results:
        print(
            f"{entry['trace_id']}: expected {entry['expected']}, got {entry['actual']} "
            f"under {entry['kernel']} -> {entry['status']}"
        )
    if result.outcome is Outcome.REGRESSION_DIFFERS:
        if result.differs:
            print(
                f"{len(result.differs)} pin(s) differ from expected — label each as regression "
                "or approved baseline update (not auto-resolved)",
                file=sys.stderr,
            )
        if result.other_unresolved:
            print(
                f"{len(result.other_unresolved)} pin(s) could not be cleanly replayed "
                "(missing / tampered / malformed / unsupported kernel) — not a pass",
                file=sys.stderr,
            )
        return EXIT_REGRESSION_DIFFERS
    print(f"all {len(result.results)} pin(s) match expected verdicts")
    return EXIT_OK


def _cmd_evidence(args: argparse.Namespace) -> int:
    from lab_service import build_evidence

    bundle, traces = read_bundle_dir(Path(args.bundle))
    result = build_evidence(
        bundle, traces, args.trace_id,
        twin_id=args.twin,
        policy=getattr(args, "policy", None),
    )
    print(json.dumps(result.case, indent=2, ensure_ascii=False))
    return EXIT_OK


def _cmd_publish(args: argparse.Namespace) -> int:
    from lab_service import build_local_publication, check_publishable

    bundle, traces = read_bundle_dir(Path(args.bundle))
    if not check_publishable(bundle, traces):
        print("refusing to publish: recomputed verdicts differ from recorded", file=sys.stderr)
        return EXIT_FAILURE

    if args.server:
        return _publish_to_server(args, bundle, traces)
    if not args.out:
        raise RunnerError("publish needs --out (local publication JSON) or --server (upload)")

    result = build_local_publication(
        bundle, traces,
        question=args.question,
        license_id=args.license,
        visibility=getattr(args, "visibility", "unlisted"),
    )
    Path(args.out).write_text(json.dumps(result.publication, indent=2, ensure_ascii=False))
    print(f"publication {result.publication_id} -> {args.out}")
    print("origin=local integrity=hash_verified")
    if result.aggregate_count:
        print(
            f"  ({result.aggregate_count} aggregate(s) in the bundle are NOT published as "
            "claims — host with --server for server-recomputed statistical claims)"
        )
    print(f"host it: axor-lab publish {args.bundle} --question ... --server <url>")
    return EXIT_OK


def _publish_to_server(
    args: argparse.Namespace,
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
) -> int:
    """The CLI half of a hosted publish: resolve the token from the ENVIRONMENT,
    hand the upload to `lab_service.publishing`, then save the receipt the server
    issued. The token is read from the environment, never a CLI arg, so it does
    not land in the process list or shell history (review r6)."""
    from lab_service import Outcome, default_acceptance_path, upload_publication

    visibility = getattr(args, "visibility", "unlisted")
    if visibility == "public":
        print("NOTE: --visibility public — this artifact will be publicly listed on the server.")
    token = None
    if getattr(args, "token_env", None):
        import os

        token = os.environ.get(args.token_env)
        if not token:
            raise RunnerError(f"--token-env {args.token_env} is not set in the environment")
    signature = None
    if getattr(args, "signature_file", None):
        signature = Path(args.signature_file).read_text().strip()
    result = upload_publication(
        bundle, traces,
        server=args.server,
        question=args.question,
        license_id=args.license,
        visibility=visibility,
        author=getattr(args, "author", None),
        signature=signature,
        token=token,
    )
    if result.outcome is not Outcome.OK:
        print(f"server rejected publish ({result.status}): {result.error}", file=sys.stderr)
        return EXIT_FAILURE
    print(f"published {result.publication_id} -> {args.server.rstrip('/')}{result.url}")
    # SAVE the server acceptance receipt — the signed proof of what the server
    # verified. The old CLI dropped it on the floor (review r15). Default to a
    # sidecar file next to the bundle, or an explicit --acceptance-out.
    if result.acceptance is not None:
        dest = getattr(args, "acceptance_out", None)
        out_path = Path(dest) if dest else default_acceptance_path(result.publication_id)
        out_path.write_text(json.dumps(result.acceptance, indent=2))
        print(f"  acceptance receipt: {out_path} "
              f"({'signed' if result.acceptance_is_signed else 'unsigned'})")
    return EXIT_OK


def _cmd_export_cp(args: argparse.Namespace) -> int:
    from lab_capabilities.governance.cp_export import CPExportError
    from lab_service import OutputRefused, export_cp_package

    bundle, traces = read_bundle_dir(Path(args.bundle))
    regressions: list[dict[str, object]] = []
    if args.pins:
        # pass the FULL pin objects (trace_ref + expected_sequence too) so
        # export_cp can validate each against the bundle's traces before it
        # carries the pin into a production Control Plane config (review r12)
        regressions = list(json.loads(Path(args.pins).read_text()))
    try:
        result = export_cp_package(
            bundle, traces,
            out=Path(args.out),
            regressions=regressions,
            condition_id=args.condition,
            overwrite=bool(getattr(args, "overwrite", False)),
            author=getattr(args, "author", None),
            sign_key=getattr(args, "sign_key", None),
        )
    except (CPExportError, OutputRefused) as exc:
        raise RunnerError(str(exc)) from exc
    print(f"exported CP deploy config -> {result.deploy_config}")
    print(f"  manifest: {result.manifest_file_count} files"
          + (" (signed)" if result.signed else " (unsigned — derivability + integrity only)"))
    print(f"  condition: {result.condition_id} (baseline: {result.baseline_condition_id})")
    # the parametric_config_hash is the carry-over key: kernel + policy + manifests
    # (effect classes, driving args, untrusted-field taint) with allowlist $inputs
    # left SYMBOLIC — the same parametric policy transfers, re-parameterized with
    # production inputs. It is NOT a byte-identical runtime config: that depends on
    # scenario inputs and is recorded per-scenario as runtime_config_hashes (r17).
    print(f"  parametric_config_hash (carry-over key): {result.parametric_config_hash}")
    print(f"  config_hash (kernel+policy anchor): {result.config_hash}")
    if result.runtime_config_hash_count:
        print("  runtime_config_hashes (per-scenario concrete config): "
              f"{result.runtime_config_hash_count} scenario(s)")
    print(f"  regressions carried: {result.regressions_carried}"
          + (f" (frozen trace bodies in {result.regression_traces}/)"
             if result.regressions_carried else ""))
    print(f"  production-todo (NOT reused): {result.production_todo}")
    if result.earned_bridge:
        print(f"  earned bridge: {result.condition_id} changed the outcome vs "
              f"{result.baseline_condition_id} — run THIS governed config in production.")
    else:
        print("  note: no aggregate shows governance changed an outcome yet "
              "(the bridge surfaces once one does).")
    return EXIT_OK


def _cmd_verify_cp_export(args: argparse.Namespace) -> int:
    """Verify a CP export directory and report its three guarantees separately:
    INTEGRITY (every file matches the manifest, nothing stale or injected),
    AUTHENTICITY (a present signature verifies against a supplied key) and
    DERIVABILITY (the shipped config recomputes from the embedded evidence).
    The checks live in `lab_service.handoff`; this prints them."""
    from lab_service import CheckStatus, Outcome, verify_cp_package

    result = verify_cp_package(
        Path(args.dir),
        pubkey=getattr(args, "pubkey", None),
        expect_author=getattr(args, "expect_author", None),
        allow_unsigned=bool(getattr(args, "allow_unsigned", False)),
    )
    for check in result.checks:
        # an OK step is progress the user asked to see; anything else is a finding
        stream = sys.stdout if check.status is CheckStatus.OK else sys.stderr
        print(f"cp-export: {check.message}", file=stream)
    if result.outcome is Outcome.FAILURE:
        return EXIT_FAILURE
    return EXIT_UNVERIFIED if result.outcome is Outcome.UNVERIFIED else EXIT_OK


def _cmd_import_incident(args: argparse.Namespace) -> int:
    """Second funnel: a production trace -> a trace-replay bundle you can test a
    policy against, pin, and export (control-plane-handoff.md §Second funnel).
    Validation, replay and materialization live in `lab_service.incidents`, which
    takes the loaded artifacts — this reads them off disk and reports."""
    from lab_service import import_incident

    result = import_incident(
        json.loads(Path(args.trace).read_text()),
        json.loads(Path(args.scenario).read_text()),
        json.loads(Path(args.manifests).read_text()),
        json.loads(Path(args.condition).read_text()),
        out=Path(args.out),
        created=args.created,
        overwrite=bool(getattr(args, "overwrite", False)),
    )
    print(f"imported incident -> {result.directory} (trace-replay bundle)")
    print(f"  replay it:  axor-lab replay {result.directory}")
    print(f"  pin + export:  axor-lab pin ... && axor-lab export-cp {result.directory} "
          "--pins pins.json")
    return EXIT_OK


def _cmd_import_agentdojo(args: argparse.Namespace) -> int:
    from lab_adapters import UnknownSuiteError, available_suites
    from lab_service import build_agentdojo_experiment

    try:
        result = build_agentdojo_experiment(
            args.suite, repeats=args.repeats, agent_ref=args.agent_ref
        )
    except UnknownSuiteError as exc:
        print(f"error: {exc}; available: {list(available_suites())}", file=sys.stderr)
        return EXIT_VALIDATION
    Path(args.out).write_text(json.dumps(result.document, indent=2, ensure_ascii=False) + "\n")
    print(f"imported AgentDojo '{args.suite}': {result.scenarios} scenario(s) -> {args.out}")
    print(f"  run: axor-lab run {args.out} --out ./bundle --yes")
    return EXIT_OK


# -- helpers ------------------------------------------------------------------


def _print_estimate(resolved: ResolvedExperiment) -> None:
    """The plan's size. There is no cost line: the agent is scripted, so a run
    makes no provider calls and spends nothing. A confirmation prompt that
    always said "$0.00" was asking the operator to approve a number that could
    not be anything else."""
    print(
        f"  {len(resolved.scenarios)} scenario(s) x {len(resolved.conditions)} condition(s) "
        f"x {resolved.repeats} repeat(s) = {resolved.trial_count} trials"
    )
    print(f"  agent: {resolved.experiment['agent_ref']}")


def _confirmed(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        return False
    answer = input("Proceed with the run? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _print_aggregate(aggregate: dict[str, object]) -> None:
    interval: dict[str, object] = aggregate["interval"]  # type: ignore[assignment]
    line = (
        f"  {aggregate['metric']}[{aggregate['condition_id']}] = {aggregate['estimate']:.2f} "
        f"[{interval['low']:.2f}, {interval['high']:.2f}] n={aggregate['n']}"
    )
    test: dict[str, object] | None = aggregate.get("test")  # type: ignore[assignment]
    if test is not None and test.get("name") == "mcnemar":
        discordant: dict[str, object] = test["discordant"]  # type: ignore[assignment]
        line += (
            f"  mcnemar (paired) vs {test['vs']}: b={discordant['b']} c={discordant['c']} "
            f"p={float(test['p']):.2g}"  # type: ignore[arg-type]
        )
    elif test is not None and test.get("name") == "two_proportion":
        line += (
            f"  two-proportion (independent, exploratory) vs {test['vs']}: "
            f"Δ={float(test['difference']):.2f} p={float(test['p']):.2g}"  # type: ignore[arg-type]
        )
    print(line)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="axor-lab", description="Axor Lab local runner (runner-protocol.md)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate an .axl experiment file")
    p_validate.add_argument("file")
    p_validate.set_defaults(func=_cmd_validate)

    p_suites = sub.add_parser("suites", help="list the suite catalog")
    p_suites.set_defaults(func=_cmd_suites)

    p_serve = sub.add_parser(
        "serve", help="run the screen API + the built web app",
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8871)
    p_serve.add_argument(
        "--control-token", default=None,
        help="require this bearer token on every screen endpoint "
             "(or AXOR_LAB_CONTROL_TOKEN)",
    )
    p_serve.add_argument(
        "--data-dir", default=None,
        help="persist the workspace (suites, evidence, regressions, artifacts) "
             "in this directory and reload it on restart (or AXOR_LAB_DATA_DIR); "
             "omitted, storage is in-memory",
    )
    p_serve.add_argument(
        "--billing-webhook-secret", default=None,
        help="shared secret the payment provider sends on /billing/webhook "
             "(or AXOR_LAB_BILLING_WEBHOOK_SECRET); omitted, the webhook is off",
    )
    p_serve.add_argument(
        "--plans-file", default=None,
        help="a JSON plan catalog {plan_id: {name, price_usd, max_suites, "
             "max_artifacts, max_hosted_runtimes, capabilities}} — YOUR pricing "
             "(or AXOR_LAB_PLANS_FILE). Omitted, a placeholder example catalog is "
             "used with no pricing authority",
    )
    p_serve.add_argument(
        "--identity-jwks-url", default=None,
        help="URL of the axor-identity JWKS (or AXOR_LAB_IDENTITY_JWKS_URL); "
             "enables login with identity access tokens, verified against it",
    )
    p_serve.add_argument(
        "--identity-jwks-file", default=None,
        help="path to a JWKS document (or AXOR_LAB_IDENTITY_JWKS_FILE), an "
             "alternative to --identity-jwks-url for an air-gapped deployment",
    )
    p_serve.add_argument(
        "--identity-issuer", default=None,
        help="expected token issuer (or AXOR_LAB_IDENTITY_ISSUER); "
             "default 'axor-identity'",
    )
    p_serve.add_argument(
        "--guest-sessions", action="store_true",
        help="allow anonymous, ephemeral hosted trial sessions via "
             "POST /guest-session (or AXOR_LAB_GUEST_SESSIONS=1); off by default",
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_suite_yaml = sub.add_parser(
        "suite-yaml", help="print a suite manifest as YAML (the Builder's third mode)",
    )
    p_suite_yaml.add_argument("suite", help="a registered suite id, or a manifest path")
    p_suite_yaml.set_defaults(func=_cmd_suite_yaml)

    p_run_suite = sub.add_parser(
        "run-suite",
        help="run a suite by id or manifest path -> artifact (no governance required)",
    )
    p_run_suite.add_argument("suite", help="a registered suite id, or a suite/v1 manifest path")
    p_run_suite.add_argument("--out", required=True, help="artifact + bundle output directory")
    p_run_suite.add_argument(
        "--yes", action="store_true", help="confirm the estimate non-interactively",
    )
    p_run_suite.add_argument("--run-id", default=None)
    p_run_suite.add_argument("--created", default=None, help="override timestamp (RFC3339)")
    p_run_suite.add_argument(
        "--overwrite", action="store_true",
        help="replace a non-empty --out directory (clears stale traces first)",
    )
    p_run_suite.set_defaults(func=_cmd_run_suite)

    p_run = sub.add_parser("run", help="resolve -> estimate -> execute -> analyze -> bundle")
    p_run.add_argument("file")
    p_run.add_argument("--out", required=True, help="bundle output directory")
    p_run.add_argument("--yes", action="store_true", help="confirm the estimate non-interactively")
    p_run.add_argument("--run-id", default=None)
    p_run.add_argument("--created", default=None, help="override bundle timestamp (RFC3339)")
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

    p_verify = sub.add_parser(
        "verify",
        help="offline-verify a downloaded reproduction package (hashes + replay + receipt)",
    )
    p_verify.add_argument("package", help="a downloaded .json package or a bundle directory")
    p_verify.add_argument(
        "--pubkey", help="author Ed25519 public key (hex) to verify a signed receipt"
    )
    p_verify.add_argument(
        "--author", help="expected author id (trust anchor); the receipt's author must match it"
    )
    p_verify.add_argument(
        "--server-pubkey", help="server Ed25519 public key (hex) to verify the acceptance receipt"
    )
    p_verify.add_argument(
        "--server", help="expected server_id (trust anchor) for the acceptance receipt"
    )
    p_verify.add_argument(
        "--server-key-id", help="expected server key_id (trust anchor) for the acceptance receipt"
    )
    p_verify.add_argument(
        "--allow-bare", action="store_true",
        help="verify a bare {bundle,traces} JSON (no versioned envelope) as integrity+replay only; "
             "without this a non-envelope JSON is refused so a server package cannot be downgraded",
    )
    p_verify.add_argument(
        "--allow-unsigned-server", action="store_true",
        help="accept an UNSIGNED server acceptance as passing (local dev only); by default an "
             "unsigned acceptance is UNVERIFIED because it is not an authenticated server verification",
    )
    p_verify.set_defaults(func=_cmd_verify)

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
    p_evidence.add_argument(
        "--policy", default=None,
        help="condition id to replay the counterfactual under (must be enforcement-on)",
    )
    p_evidence.set_defaults(func=_cmd_evidence)

    p_publish = sub.add_parser("publish", help="mint a publication/v1 from a verified bundle")
    p_publish.add_argument("bundle")
    p_publish.add_argument("--question", required=True)
    p_publish.add_argument("--out", help="write publication/v1 JSON locally")
    p_publish.add_argument("--server", help="upload via the publish handshake to this base URL")
    p_publish.add_argument("--license", default="CC-BY-4.0")
    p_publish.add_argument(
        "--visibility", choices=["public", "unlisted", "private"], default="unlisted",
        help="publication visibility; default unlisted (public must be explicit)",
    )
    p_publish.add_argument(
        "--token-env", default=None,
        help="ENV VAR holding the server write bearer token (never pass the token directly)",
    )
    p_publish.add_argument("--author", default=None, help="author id for a signed upload")
    p_publish.add_argument(
        "--signature-file", default=None,
        help="path to the detached bundle signature (hex) for a signed upload",
    )
    p_publish.add_argument(
        "--acceptance-out", default=None,
        help="where to save the server's acceptance receipt (default: <pid>.acceptance.json)",
    )
    p_publish.set_defaults(func=_cmd_publish)

    p_export = sub.add_parser(
        "export-cp", help="export the validated policy + manifests + regressions to a CP config"
    )
    p_export.add_argument("bundle")
    p_export.add_argument("--out", required=True, help="output directory for cp-deploy.json")
    p_export.add_argument("--pins", default=None, help="regression pins to carry over")
    p_export.add_argument(
        "--condition", default=None,
        help="which enforcing condition to deploy (required when several enforce)",
    )
    p_export.add_argument("--overwrite", action="store_true",
                          help="replace a non-empty output directory (clears stale files)")
    p_export.add_argument("--author", default=None, help="author id to sign the export manifest as")
    p_export.add_argument("--sign-key", dest="sign_key", default=None,
                          help="Ed25519 private key (hex) to sign the export manifest")
    p_export.set_defaults(func=_cmd_export_cp)

    p_verify_cp = sub.add_parser(
        "verify-cp-export",
        help="verify a CP export's manifest, signature, and that cp-deploy.json recomputes",
    )
    p_verify_cp.add_argument("dir", help="a CP export directory produced by export-cp")
    p_verify_cp.add_argument("--pubkey", default=None,
                             help="Ed25519 public key (hex) to verify a signed manifest")
    p_verify_cp.add_argument("--expect-author", dest="expect_author", default=None,
                             help="trust anchor: the signed manifest's author must equal this")
    p_verify_cp.add_argument("--allow-unsigned", dest="allow_unsigned", action="store_true",
                             help="accept an UNSIGNED export as integrity+derivability only "
                                  "(default: unsigned exits UNVERIFIED)")
    p_verify_cp.set_defaults(func=_cmd_verify_cp_export)

    p_incident = sub.add_parser(
        "import-incident", help="build a trace-replay bundle from a production incident trace"
    )
    p_incident.add_argument("--trace", required=True, help="a conformant trace/v1 JSON")
    p_incident.add_argument("--scenario", required=True, help="scenario/v1 JSON")
    p_incident.add_argument("--manifests", required=True, help="tool-manifest/v1 list JSON")
    p_incident.add_argument(
        "--condition", required=True,
        help="the EXACT recorded condition/v1 (enforcement, policy, kernel, config_hash)",
    )
    p_incident.add_argument("--out", required=True)
    p_incident.add_argument("--created", default=None)
    p_incident.add_argument("--overwrite", action="store_true", help="replace a non-empty --out")
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
