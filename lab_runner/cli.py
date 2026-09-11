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

from lab_service.handoff import (
    cp_export_manifest as _cp_export_manifest,
    safe_export_path as _safe_export_path,
    verify_manifest_semantic_refs as _verify_manifest_semantic_refs,
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
    resolved = resolve(load_axl(Path(args.file)))
    print(f"valid: {resolved.experiment['id']}")
    print(
        f"  scenarios={len(resolved.scenarios)} conditions={len(resolved.conditions)} "
        f"repeats={resolved.repeats} -> {resolved.trial_count} trials"
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
    from lab_suite import to_yaml
    from lab_suite.yaml_mode import YamlUnavailable

    manifest, _ = _suite_manifest(args.suite)
    try:
        print(to_yaml(manifest), end="")
    except YamlUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


def _suite_manifest(target: str) -> tuple[dict[str, object], object]:
    """Resolve `target` — a registered suite id or a manifest path — to
    (manifest, suite implementation).

    A manifest file may name a suite the registry knows; then that suite's hooks
    run over the file's manifest, which is exactly the Builder's edit-and-run
    loop. A manifest whose `id` is unregistered runs on `BaseSuite` defaults —
    no program, no metrics, no extractors — rather than being refused, because a
    manifest is authorable long before an implementation exists.
    """
    from lab_suite import builtin_registry, load_manifest
    from lab_suite.errors import SuiteNotFound
    from lab_suite.sdk import BaseSuite

    registry = builtin_registry()
    path = Path(target)
    if path.exists():
        manifest = load_manifest(path)
        try:
            return manifest, registry.get(str(manifest.get("id", "")))
        except SuiteNotFound:
            unimplemented = BaseSuite()
            unimplemented.id = str(manifest.get("id", ""))
            unimplemented.manifest = lambda: manifest  # type: ignore[method-assign]
            return manifest, unimplemented
    suite = registry.get(target)
    return suite.manifest(), suite


def _cmd_run_suite(args: argparse.Namespace) -> int:
    from lab_suite import run_suite, validate_manifest

    print("[validating]")
    manifest, suite = _suite_manifest(args.suite)
    errors = validate_manifest(manifest) + suite.validate(manifest)  # type: ignore[attr-defined]
    if errors:
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return EXIT_VALIDATION
    execution: dict[str, object] = manifest.get("execution") or {}  # type: ignore[assignment]
    scenarios = list(manifest.get("scenarios") or []) or list(manifest.get("scenario_refs") or [])
    conditions = list(execution.get("conditions") or [])
    repeats = int(execution.get("repeats", 1))  # type: ignore[arg-type]
    # a suite with NO conditions is single-arm — one trial per (scenario, repeat).
    # That is the platform's default path, not a degenerate case.
    trials = len(scenarios) * repeats * max(len(conditions), 1)
    print(f"valid: {manifest.get('id')}")
    print(
        f"  scenarios={len(scenarios)} conditions={len(conditions)} "
        f"repeats={repeats} -> {trials} trials"
        + ("  (single-arm: no governance)" if not conditions else "")
    )

    print("[estimate]")
    print(f"  {trials} trial(s), local simulated tools, no paid inference")
    if not _confirmed(args):
        print(
            "not confirmed — pass --yes (or answer y) to execute; nothing ran",
            file=sys.stderr,
        )
        return EXIT_UNCONFIRMED

    print("[running_local]")
    run_id = args.run_id or f"r_{content_hash(manifest)[7:39]}"
    run = run_suite(manifest, run_id=run_id, suite=suite)  # type: ignore[arg-type]
    by_status: dict[str, int] = {}
    for trial in run.trials:
        by_status[str(trial["status"])] = by_status.get(str(trial["status"]), 0) + 1
    print(
        f"  planned {len(run.trials)}: "
        + ", ".join(f"{n} {status}" for status, n in sorted(by_status.items()))
    )

    print("[analyzing]")
    summary = missingness(run.trials)
    print(f"  {summary.display()}")
    for aggregate in run.aggregates:
        _print_aggregate(aggregate)
    for result in run.invariants:
        print(f"  invariant {result.regression_id}: {result.status}"
              + (f" — {result.detail}" if result.detail else ""))

    print("[uploading_artifacts]  (local: writing artifact + bundle)")
    created = args.created or datetime.now(timezone.utc).isoformat(timespec="seconds")
    environment = {
        "model": {"provider": "scripted", "id": str(manifest.get("id", "")),
                  "inference_params": {"suite_id": str(manifest.get("id", ""))}},
    }
    artifact = run.artifact(
        artifact_id=f"a_{run_id}", created=created, environment=environment,
        command=f"axor-lab run-suite {args.suite}",
    )
    schema_errors = validate_artifact(artifact, "artifact")
    if schema_errors:
        # the artifact IS the deliverable; writing an invalid one and finding out
        # at publish time is how a run becomes unusable hours later
        for error in schema_errors:
            print(f"  [schema] {error}", file=sys.stderr)
        return EXIT_FAILURE
    out = Path(args.out)
    write_bundle_dir(
        out, artifact["bundle"], run.traces,  # type: ignore[arg-type]
        overwrite=bool(getattr(args, "overwrite", False)),
    )
    (out / "artifact.json").write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
    # Three outcomes, three exit codes — collapsing them loses the distinction
    # the whole invariant model rests on. `failed` is a violated invariant.
    # `error` is one that could NOT be evaluated, which is not a pass either
    # (an unmeasured latency is not a fast one, lifecycle.md) but is a different
    # thing to tell a CI than "your change regressed". `skipped` IS fine: the
    # rule kind executes elsewhere, and failing on it would fail every governed
    # suite for carrying a verdict_sequence pin.
    violated = [r for r in run.invariants if r.status == STATUS_FAILED]
    unevaluable = [r for r in run.invariants if r.status == STATUS_ERROR]
    print(f"[completed]  artifact: {out}/artifact.json ({len(run.traces)} traces)")
    print(f"  reproduce verdicts (exact):    axor-lab replay {out}")
    if violated:
        print(
            "  invariants VIOLATED: "
            + ", ".join(str(r.regression_id) for r in violated),
            file=sys.stderr,
        )
        return EXIT_REGRESSION_DIFFERS
    if unevaluable:
        print(
            "  invariants could not be evaluated: "
            + ", ".join(f"{r.regression_id} ({r.detail})" for r in unevaluable),
            file=sys.stderr,
        )
        return EXIT_FAILURE
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    print("[validating]")
    document = load_axl(Path(args.file))
    if args.real_kernel:
        _repin_to_real_kernel(document)
    resolved = resolve(document)

    print("[estimate]")
    _print_estimate(resolved)
    if not _confirmed(args):
        print(
            "not confirmed — pass --yes (or answer y) to execute; nothing ran",
            file=sys.stderr,
        )
        return EXIT_UNCONFIRMED

    print("[running_local]")
    agent = resolved.agent
    # run identity folds in the agent, so two runs of the same experiment under
    # different agents are different runs rather than retries of one (review r3).
    fingerprint = str(resolved.experiment["agent_ref"])
    # 128-bit id (32 hex chars) from the experiment+agent fingerprint — the old
    # 8-char (32-bit) slice was birthday-collision-searchable, so two unrelated
    # runs could share a run_id and look like retries of one trial (review r7).
    # For a NONDETERMINISTIC agent a fresh random execution nonce is folded in, so
    # two live runs of the same experiment are distinct executions (review r13).
    run_id = _derive_run_id(
        args.run_id, resolved.experiment, fingerprint,
        deterministic=bool(getattr(agent, "is_deterministic", True)),
    )
    result = run_experiment_suite(
        list(resolved.scenarios),
        resolved.manifests,
        list(resolved.conditions),
        resolved.kernel_registry,
        repeats=resolved.repeats,
        run_id=run_id,
        agent=agent,
    )
    # report the plan outcome by status separately — "N trials completed" over
    # result.trials was misleading, since result.trials also holds failed and
    # cost-excluded records (review r14). planned = everything the plan intended.
    by_status: dict[str, int] = {}
    for trial in result.trials:
        by_status[str(trial["status"])] = by_status.get(str(trial["status"]), 0) + 1
    n_completed = by_status.get("completed", 0)
    n_failed = by_status.get("failed", 0)
    n_excluded = by_status.get("excluded", 0)
    print(
        f"  planned {len(result.trials)}: {n_completed} completed, "
        f"{n_failed} failed, {n_excluded} excluded"
    )
    print("[analyzing]")
    # missingness FIRST (denominator honesty) — it must be reported even if a
    # whole condition has no completed trials, so it never depends on aggregates
    summary = missingness(result.trials)
    print(f"  {summary.display()}")
    aggregates = _aggregates(resolved, result, agent)
    for aggregate in aggregates:
        _print_aggregate(aggregate)

    print("[uploading_artifacts]  (local: writing bundle directory)")
    created = args.created or datetime.now(timezone.utc).isoformat(timespec="seconds")
    # no paid inference: the agent is scripted, so there is no spend to record.
    usage = None
    bundle = build_bundle(
        bundle_id=f"b_{run_id}",
        created=created,
        scenarios=list(resolved.scenarios),
        conditions=list(resolved.conditions),
        tool_manifests=list(resolved.manifests.values()),
        environment=_environment(resolved, usage=usage, agent=agent),
        trials=result.trials,
        aggregates=aggregates,
        traces=result.traces,
        packaging=dict(PACKAGING),
    )
    out = Path(args.out)
    write_bundle_dir(out, bundle, result.traces, overwrite=bool(getattr(args, "overwrite", False)))
    # superseded retry attempts are NOT publishable evidence (they would orphan
    # the bundle graph), but they ARE the audit trail — persist them beside the
    # bundle so "both attempts are preserved" holds on disk, not only in the
    # in-memory result (review r9)
    attempt_log = write_superseded_attempts(out, result.superseded)
    if attempt_log is not None:
        print(f"  superseded attempts: {attempt_log} ({len(result.superseded)})")
    print(f"[completed]  bundle: {out}/bundle.json ({len(result.traces)} traces)")
    print(f"  reproduce verdicts (exact):    axor-lab replay {out}")
    print(f"  reproduce behavior (fresh):    axor-lab run {args.file} --out <new-dir>")
    return EXIT_OK


def _cmd_replay(args: argparse.Namespace) -> int:
    # accept a bundle DIRECTORY or a downloaded .json reproduction package, so a
    # reader can replay exactly what a publication page served (review r13)
    bundle, traces = read_bundle_source(Path(args.bundle))
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


_REPRODUCTION_PACKAGE_SCHEMA = "axor-reproduction-package/v1"


def _package_data(path: Path) -> dict[str, object] | None:
    """The raw JSON of a downloaded `.json` package, or None for a bundle
    DIRECTORY (which ships no receipt/publication/acceptance)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _cmd_verify(args: argparse.Namespace) -> int:
    """Standalone, offline verification of a downloaded reproduction package —
    NO server trusted. Confirms content hashes, bit-identical replay, and — for a
    server-issued package — that EVERY proof object is present and binds: the
    author receipt (signed_ref/signature), the publication (id + bundle_ref +
    claims), and the server acceptance (semantic report + signature). Stripping
    any proof from a server package is a failure, not a silent pass (review r16)."""
    path = Path(args.package)
    bundle, traces = read_bundle_source(path)
    print(f"content hashes: OK ({len(traces)} trace(s), {len(bundle['conditions'])} conditions)")  # type: ignore[arg-type]
    versions = tuple(str(c["kernel"]) for c in bundle["conditions"])  # type: ignore[union-attr]
    kernels = {k.version: k for k in default_registry(versions).kernels}
    report = replay_bundle(bundle, traces, kernels)
    if not report.bit_identical:
        print("replay MISMATCH: recomputed verdicts differ from recorded", file=sys.stderr)
        return EXIT_FAILURE
    print(f"replay: bit-identical over {len(report.decisions)} trace(s)")

    # A bundle DIRECTORY is a local artifact that never carried server proofs — it
    # verifies as bundle-integrity + replay only, and cannot be "downgraded".
    if path.is_dir():
        print("receipt: none (a bundle directory carries no portable receipt)")
        return EXIT_OK

    data = _package_data(path)
    # A downloaded `.json` MUST be a VERSIONED reproduction envelope. Detection can
    # no longer key on the PRESENCE of a publication/acceptance/receipt: an
    # attacker downgrades a server package to a bare {bundle,traces} by stripping
    # the envelope AND every proof at once, and autodetection then reads it as an
    # honest bare package and exits 0. The envelope schema_version is the one
    # marker whose ABSENCE is meaningful — a bare file without --allow-bare is
    # refused, so a stripped server package cannot masquerade as bare (review r17).
    has_envelope = bool(data) and str(data.get("schema_version")) == _REPRODUCTION_PACKAGE_SCHEMA
    if not has_envelope:
        if not getattr(args, "allow_bare", False):
            print(
                f"package: NOT a versioned reproduction envelope (missing schema_version "
                f"{_REPRODUCTION_PACKAGE_SCHEMA!r}). A server package cannot be silently "
                "downgraded to a bare bundle — pass --allow-bare to verify a local "
                "bundle+traces file as bare (integrity + replay only, no server proofs).",
                file=sys.stderr,
            )
            return EXIT_VALIDATION
        print("package: bare (bundle+traces only, --allow-bare) — no server proofs to check")
        return EXIT_OK

    assert data is not None
    receipt = data.get("receipt")
    if not isinstance(receipt, dict):
        print("receipt: MISSING from a server package (stripped?) — refusing to pass",
              file=sys.stderr)
        return EXIT_FAILURE

    from lab_contracts.publication import verify_publication_binding
    from lab_contracts.signing import (
        SignatureInvalid,
        SignatureUnavailable,
        verify_acceptance,
        verify_receipt,
    )

    unverified = False  # a signature we could not check (distinct from invalid)
    # 1) author receipt
    try:
        verify_receipt(bundle, receipt, getattr(args, "pubkey", None),
                       expected_author=getattr(args, "author", None))
    except SignatureInvalid as exc:
        print(f"receipt: INVALID — {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except SignatureUnavailable as exc:
        print(f"receipt: UNVERIFIED — {exc}", file=sys.stderr)
        unverified = True
    else:
        kind = "signature VERIFIED" if str(receipt.get("integrity")) == "signed" else "signed_ref OK (hash-only)"
        print(f"receipt: {kind}")

    publication = data.get("publication")
    acceptance = data.get("acceptance")
    if not isinstance(publication, dict) or not isinstance(acceptance, dict):
        print("package: MISSING publication or acceptance (server package) — refusing to pass",
              file=sys.stderr)
        return EXIT_FAILURE
    # 2) publication binds to the bundle and its own id
    problems = verify_publication_binding(publication, bundle)
    if problems:
        print("publication: INVALID — " + "; ".join(problems[:5]), file=sys.stderr)
        return EXIT_FAILURE
    print("publication: bound (id + bundle_ref + claims)")
    # 2b) the AUTHOR receipt's integrity must match the publication's — otherwise a
    # `signed` publication can be downgraded by swapping in a valid hash-only
    # receipt (the author signature stripped) while the server acceptance stays
    # signed, and verify would still exit 0. The portable author receipt exists
    # precisely to prove author authenticity WITHOUT trusting the server, so a
    # signed publication must carry a signed, VERIFIED author receipt (review r18).
    pub_integrity = str(publication.get("integrity", "hash_verified"))
    receipt_integrity = str(receipt.get("integrity", "hash_verified"))
    if receipt_integrity != pub_integrity:
        print(
            f"receipt: INTEGRITY MISMATCH — receipt is {receipt_integrity!r} but the "
            f"publication is {pub_integrity!r} (author-signature downgrade?) — refusing to pass",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    if pub_integrity == "signed" and not getattr(args, "pubkey", None):
        # a signed publication whose author signature we hold no key to check is
        # UNVERIFIED, never a pass (the server acceptance is a separate attestation)
        print(
            "receipt: UNVERIFIED — signed publication but no author public key (--pubkey) "
            "was supplied to verify its receipt",
            file=sys.stderr,
        )
        unverified = True
    # 3) server acceptance binds + (optionally) verifies. verify_acceptance also
    # requires acceptance.integrity == publication.integrity. v0.3 keeps a single
    # acceptance/v1 form: a server that finds its persisted acceptance damaged
    # simply re-mints a fresh acceptance/v1 from the current bundle on load, so
    # there is no reacceptance/history chain to resolve here.
    try:
        verify_acceptance(
            acceptance, publication,
            server_pubkey_hex=getattr(args, "server_pubkey", None),
            expected_server=getattr(args, "server", None),
            expected_key_id=getattr(args, "server_key_id", None),
        )
    except SignatureInvalid as exc:
        print(f"acceptance: INVALID — {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except SignatureUnavailable as exc:
        print(f"acceptance: UNVERIFIED — {exc}", file=sys.stderr)
        unverified = True
    else:
        if str(acceptance.get("algorithm")) == "ed25519":
            print("acceptance: signature VERIFIED")
        elif getattr(args, "allow_unsigned_server", False):
            print("acceptance: unsigned (accepted via --allow-unsigned-server; local dev only)")
        else:
            # an UNSIGNED acceptance proves only internal self-consistency, NOT that
            # a specific Axor Lab server ran the checks — anyone can mint one. It is
            # not an authenticated server verification, so it is UNVERIFIED (r17).
            print(
                "acceptance: UNVERIFIED — unsigned server acceptance is not an authenticated "
                "verification (pass --server-pubkey to check a signed one, or "
                "--allow-unsigned-server for local development)",
                file=sys.stderr,
            )
            unverified = True

    return EXIT_UNVERIFIED if unverified else EXIT_OK


def _cmd_pin(args: argparse.Namespace) -> int:
    _, traces = read_bundle_dir(Path(args.bundle))
    trace = traces.get(args.trace_id)
    if trace is None:
        raise RunnerError(f"trace {args.trace_id} not found in bundle")
    out = Path(args.out)
    pins: list[dict[str, object]] = json.loads(out.read_text()) if out.is_file() else []
    pins = [p for p in pins if p["trace_id"] != args.trace_id]
    # use the regression model's pin(), which records the WHOLE ordered verdict
    # sequence — not just the final verdict. Persisting only expected_verdict made
    # regress compare a multi-call trace's real sequence (ALLOW, ALLOW, DENY) to a
    # singleton (DENY) and cry regression on an unchanged trace/kernel (review r12).
    # pin() also rejects an expected_verdict that contradicts the trace's final
    # recorded verdict (review r13) — surface that as a clean CLI error.
    try:
        p = pin(trace, args.expected)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc
    pins.append(
        {
            "trace_id": p.trace_id,
            "trace_ref": p.trace_ref,
            "expected_verdict": p.expected_verdict,
            "expected_sequence": list(p.expected_sequence),
        }
    )
    out.write_text(json.dumps(pins, indent=2))
    print(f"pinned {args.trace_id} -> expected {list(p.expected_sequence)} ({out})")
    return EXIT_OK


def _cmd_regress(args: argparse.Namespace) -> int:
    bundle, traces = read_bundle_dir(Path(args.bundle))
    pins_raw: list[dict[str, object]] = json.loads(Path(args.pins).read_text())
    pins = tuple(
        RegressionPin(
            trace_id=str(p["trace_id"]),
            trace_ref=str(p["trace_ref"]),
            expected_verdict=str(p["expected_verdict"]),
            # restore the pinned ORDERED sequence (default to the singleton for
            # older pin files) so a multi-call trace is compared correctly (r12)
            expected_sequence=tuple(str(v) for v in p.get("expected_sequence", ())),
        )
        for p in pins_raw
    )
    condition = _enforcing_condition(bundle, args.condition)
    version = args.kernel or str(condition["kernel"])
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    kernel_for = None
    if args.disable_taint_floor:
        # An explicit real-kernel VARIANT demonstration: the same installed
        # build, but with its egress-sink declarations dropped so the taint /
        # confidentiality floor is never armed — the exfiltration the pinned run
        # DENIED is now ALLOWED, which is exactly the regression a pin exists to
        # catch. The fingerprint marks it a different kernel (behavior_version
        # gains `+taint_floor=off`), so the report names the variant, not the
        # pinned build (review r4).
        cfg = governor_config(manifests, condition.get("policy"), None)  # type: ignore[arg-type]
        cfg.pop("egress_sinks", None)
        cfg.pop("value_policies", None)
        kernel: object = AxorKernel(version=version, config=cfg, taint_floor_enabled=False)
    else:
        # regress under the CANDIDATE kernel — the one named by --kernel or the
        # chosen regression condition — NOT the kernel the trace was recorded
        # under (review r18). Each pin resolves the candidate against its OWN
        # scenario inputs so a real-kernel allowlist expands per scenario. The
        # candidate resolver takes policy/enforcement from the selected condition
        # and the version from the override, so `regress --kernel axor-core@X`
        # actually runs axor-core@X.
        registry = default_registry((version,))
        kernel = resolve_kernel(version, manifests, condition.get("policy"), registry)  # type: ignore[arg-type]
        kernel_for = lambda trace: resolve_candidate_kernel_for_trace(  # noqa: E731
            bundle, trace, condition, args.kernel, registry
        )
    # each pinned trace replays against ITS OWN scenario's inputs — a single
    # shared inputs dict would replay every pin under the first scenario's
    # allowlist / effect-resolution inputs (review r12)
    results = check_pins(
        pins, traces, condition, kernel, manifests,
        inputs_for=lambda trace: _scenario_for(bundle, trace).get("inputs", {}),  # type: ignore[union-attr,arg-type]
        kernel_for=kernel_for,
    )
    for result in results:
        print(
            f"{result['trace_id']}: expected {result['expected']}, got {result['actual']} "
            f"under {result['kernel']} -> {result['status']}"
        )
    # ANY status other than a clean match is unresolved — a differing verdict, a
    # missing/tampered/malformed trace, or an unsupported kernel. A malformed
    # trace whose recomputed sequence coincidentally equals the pin used to fall
    # through to EXIT_OK; it must NOT (review r13).
    differs = [r for r in results if r["status"] == STATUS_DIFFERS]
    unresolved = [r for r in results if r["status"] != STATUS_MATCHES]
    if unresolved:
        if differs:
            print(
                f"{len(differs)} pin(s) differ from expected — label each as regression "
                "or approved baseline update (not auto-resolved)",
                file=sys.stderr,
            )
        other = [r for r in unresolved if r["status"] != STATUS_DIFFERS]
        if other:
            print(
                f"{len(other)} pin(s) could not be cleanly replayed "
                "(missing / tampered / malformed / unsupported kernel) — not a pass",
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
    if twin is not None:
        # a governed twin must be the SAME case under an enforcing policy — not
        # any unrelated trace the caller happened to name (review r13)
        try:
            validate_twin(trace, twin, bundle)
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc
    scenario = _scenario_for(bundle, trace)
    # the SAME condition resolver the HTML EvidenceCase uses: an explicit
    # --policy wins, else the trace's own enforcing condition, else the first
    # enforcing one — never just "the first enforcement-on condition", which
    # rendered a strict counterfactual for an allowlist trace (review r13)
    try:
        condition = evidence_condition(bundle, trace, getattr(args, "policy", None))
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    # resolve the SAME kernel replay/regress use — the REAL axor-core governor
    # when the condition pins the installed build — and pass THIS trace's scenario
    # inputs so a real-kernel `$inputs` allowlist expands to the concrete values,
    # not the symbolic ref (review r12/r17). The condition may be a policy-override
    # counterfactual, so we keep it and only thread the scenario inputs.
    version = str(condition["kernel"])
    kernel = resolve_kernel(
        version, manifests, condition.get("policy"),  # type: ignore[arg-type]
        default_registry((version,)), scenario.get("inputs", {}),  # type: ignore[union-attr]
    )
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
        claims.append(
            make_claim(
                "exactly_replayable",
                # same decision-derived text the server uses — not a template
                deny_claim_text(denied),
                content_hash(denied),
                trace_refs=trace_refs,
                aggregate_refs=aggregate_refs,
            )
        )
    # local publish proves REPLAY (it re-ran the verdicts above), NOT statistics:
    # it does not independently recompute the aggregates, so it must NOT mint a
    # `statistically_reproducible` claim over self-reported numbers — the schema
    # forbids self_reported backing that claim, and a hand-edited bundle could
    # carry a fabricated aggregate. Statistical claims are minted only by the
    # server, which recomputes from the traces (→ recomputed_from_traces, r12).
    stat_note = ""
    if aggregates:
        stat_note = (
            f"  ({len(aggregates)} aggregate(s) in the bundle are NOT published as claims — "
            "host with --server for server-recomputed statistical claims)"
        )
    publication = build_publication(
        publication_id="e_pending",  # placeholder; content-addressed below
        bundle_ref=bundle_ref,
        question=args.question,
        origin="local",
        integrity="hash_verified",
        claims=claims,
        license_id=args.license,
        visibility=getattr(args, "visibility", "unlisted"),
        statistics_integrity=None,  # no statistical claims are asserted locally
    )
    # content-address the WHOLE body (the shared definition), so the same bundle
    # published with a different question/visibility/license is a genuinely
    # different publication with its own id, not an id-colliding overwrite (r12)
    finalize_publication_id(publication)
    errors = validate_artifact(publication, "publication")
    if errors:
        raise RunnerError(f"publication failed schema validation: {errors}")
    Path(args.out).write_text(json.dumps(publication, indent=2, ensure_ascii=False))
    print(f"publication {publication['publication_id']} -> {args.out}")
    print("origin=local integrity=hash_verified")
    if stat_note:
        print(stat_note)
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

    visibility = getattr(args, "visibility", "unlisted")
    if visibility == "public":
        print("NOTE: --visibility public — this artifact will be publicly listed on the server.")
    body: dict[str, object] = {
        "bundle": bundle,
        "traces": traces,
        "question": args.question,
        "license": args.license,
        "visibility": visibility,
    }
    # a signed, attributed upload: author + detached signature travel in the body
    if getattr(args, "author", None):
        body["author"] = args.author
    if getattr(args, "signature_file", None):
        body["signature"] = Path(args.signature_file).read_text().strip()
    payload = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    # the write token is read from the ENVIRONMENT, never a CLI arg, so it does
    # not land in the process list or shell history (review r6)
    if getattr(args, "token_env", None):
        import os
        token = os.environ.get(args.token_env)
        if not token:
            raise RunnerError(f"--token-env {args.token_env} is not set in the environment")
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        args.server.rstrip("/") + "/api/publications",
        data=payload,
        headers=headers,
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
    # SAVE the server acceptance receipt — the signed proof of what the server
    # verified. The old CLI dropped it on the floor (review r15). Default to a
    # sidecar file next to the bundle, or an explicit --acceptance-out.
    acceptance = result.get("acceptance")
    if acceptance is not None:
        dest = getattr(args, "acceptance_out", None)
        out_path = Path(dest) if dest else Path(f"{result['publication_id']}.acceptance.json")
        out_path.write_text(json.dumps(acceptance, indent=2))
        signed = acceptance.get("algorithm") == "ed25519"
        print(f"  acceptance receipt: {out_path} ({'signed' if signed else 'unsigned'})")
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

    The recorded condition is REQUIRED and used verbatim — reconstructing it
    (enforcement=on, kernel from the trace) silently loses enforcement mode,
    policy, allowlist, criticality overrides and the config hash, so replay
    could then yield a different verdict than the incident actually produced.
    Everything is validated (schema + semantics + cross-references + config
    hash) and REPLAYED before anything is written."""
    from datetime import datetime, timezone

    from lab_contracts import (
        ScenarioValidationError,
        build_bundle,
        condition_config_hash,
        content_hash,
        validate_artifact,
        validate_scenario,
    )

    from lab_capabilities.governance.replay import REPLAY_MATCH, replay_trace_status

    trace: dict[str, object] = json.loads(Path(args.trace).read_text())
    scenario: dict[str, object] = json.loads(Path(args.scenario).read_text())
    manifests: list[dict[str, object]] = json.loads(Path(args.manifests).read_text())
    condition: dict[str, object] = json.loads(Path(args.condition).read_text())

    # 1. schema validation of every artifact
    for obj, name in ((trace, "trace"), (scenario, "scenario"), (condition, "condition")):
        errors = validate_artifact(obj, name)
        if errors:
            raise RunnerError(f"incident {name} is not conformant: {errors}")
    manifests_by_id: dict[str, dict[str, object]] = {}
    for manifest in manifests:
        errors = validate_artifact(manifest, "tool-manifest")
        if errors:
            raise RunnerError(f"incident manifest {manifest.get('id')} is not conformant: {errors}")
        manifests_by_id[str(manifest["id"])] = manifest

    # 2. semantic + cross-reference validation
    try:
        validate_scenario(scenario, manifests_by_id)
    except ScenarioValidationError as exc:
        raise RunnerError(f"incident scenario failed semantic validation: {exc}") from exc
    trial: dict[str, object] = trace["trial"]  # type: ignore[assignment]
    if str(condition["id"]) != str(trial["condition_id"]):
        raise RunnerError(
            f"condition.id {condition['id']!r} != trace condition_id {trial['condition_id']!r}"
        )
    if str(scenario["name"]) != str(trial["scenario_id"]):
        raise RunnerError(
            f"scenario.name {scenario['name']!r} != trace scenario_id {trial['scenario_id']!r}"
        )

    # 3. config-hash verification (if the recorded condition carries one)
    if "config_hash" in condition:
        expected = condition_config_hash(str(condition["kernel"]), condition.get("policy"))  # type: ignore[arg-type]
        if str(condition["config_hash"]) != expected:
            raise RunnerError(
                f"condition config_hash {condition['config_hash']!r} != recomputed {expected!r}"
            )

    # 4. replay the incident under its OWN recorded condition before writing — a
    # wrong/reconstructed condition would surface here as a mismatch. Pass the
    # scenario inputs so a real-kernel `$inputs` allowlist expands to the concrete
    # values the incident actually ran under, not the symbolic ref (review r17).
    kernel = resolve_kernel(
        str(condition["kernel"]), manifests_by_id, condition.get("policy"),  # type: ignore[arg-type]
        default_registry((str(condition["kernel"]),)), scenario.get("inputs", {}),  # type: ignore[arg-type]
    )
    _, status = replay_trace_status(
        trace, condition, kernel, manifests_by_id, scenario.get("inputs", {}),  # type: ignore[arg-type]
    )
    if status != REPLAY_MATCH:
        raise RunnerError(
            f"incident trace does not replay under its condition (status={status}) — "
            "refusing to import a bundle whose verdicts don't reproduce"
        )

    # a completed trial carries the runtime config it ran under, but this hash is
    # RECONSTRUCTED at import from the incident's condition + scenario inputs — the
    # original production trace never carried it, and this process did not observe
    # the runtime compilation. Mark it reconstructed_incident so config_provenance
    # reports the honest status and an evidence-backed CP export refuses it as
    # "the exact runtime config that actually ran in production" (review r21).
    from lab_contracts import CONFIG_COMPILER_VERSION, runtime_config_hash

    incident_rch = runtime_config_hash(
        str(condition["kernel"]), condition.get("policy"), manifests,
        scenario.get("inputs", {}),  # type: ignore[arg-type]
    )
    trials = [{
        "trial_id": content_hash(trace), "scenario_id": str(trial["scenario_id"]),
        "condition_id": str(trial["condition_id"]), "seed": str(trial["seed"]),
        "repeat_index": int(trial["repeat_index"]), "status": "completed",
        "trace_ref": content_hash(trace),
        "runtime_config_hash": incident_rch,
        "config_compiler_version": CONFIG_COMPILER_VERSION,
        "runtime_provenance": "reconstructed_incident",
    }]
    bundle = build_bundle(
        bundle_id="b_incident_" + content_hash(trace).removeprefix("sha256:")[:32],
        created=args.created or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        scenarios=[scenario], conditions=[condition], tool_manifests=manifests,
        environment={"kernel_version": str(trace["producer"]["kernel_version"]),  # type: ignore[index]
                     "model": {"provider": "imported", "id": "production-incident"}},
        trials=trials, aggregates=[], traces={str(trace["trace_id"]): trace},
        packaging=dict(PACKAGING),
    )
    write_bundle_dir(Path(args.out), bundle, {str(trace["trace_id"]): trace},
                     overwrite=bool(getattr(args, "overwrite", False)))
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
    """Repin EVERY condition — baseline included — to the installed axor-core
    version, so the run governs with the real kernel.

    Repinning only the enforcement-on conditions left the baseline on the
    reference kernel, so the compare no longer isolated enforcement: it mixed an
    enforcement change WITH a kernel change (the condition contract wants one
    kernel across the compared conditions). It also produced a bundle with two
    distinct condition kernels, so `_environment` wrote a comma-joined
    `kernel_version` that `verify_bundle` rejects — meaning the command ran every
    trial (paid model calls included) and only THEN failed at save (review r13).

    Repinning the baseline is load-bearing, not cosmetic. `enforcement: off` is
    observe-only, not gate-free: the kernel evaluates every call and records the
    verdict it would have enforced. So the baseline's verdicts are the REAL
    kernel's verdicts, and a baseline left on the reference kernel would report
    a different kernel's opinion of the same calls. Both arms share one kernel,
    and the bundle has a single kernel_version."""
    from lab_contracts import condition_config_hash
    from lab_capabilities.governance import axor_available, real_kernel_version

    if not axor_available():
        raise RunnerError("--real-kernel requested but axor-core is not installed")
    version = real_kernel_version()
    experiment: dict[str, object] = document["experiment"]  # type: ignore[assignment]
    for condition in experiment.get("conditions", []):  # type: ignore[union-attr]
        condition["kernel"] = version
        condition["config_hash"] = condition_config_hash(version, condition.get("policy"))
    print(f"  repinned ALL conditions (baseline + governed) to the real kernel: {version}")


def _derive_run_id(
    explicit: str | None,
    experiment: dict[str, object],
    fingerprint: str,
    *,
    deterministic: bool,
) -> str:
    """The run id. An explicit --run-id always wins. A DETERMINISTIC agent
    (scripted / replayed cassette) yields a content-derived id, so re-running the
    same experiment reproduces the same identity. A NONDETERMINISTIC agent (a live
    model) draws a fresh sample each execution, so two runs are DIFFERENT
    executions, not retries of one — a random execution nonce is folded in so
    their run/trial/trace ids differ (review r13)."""
    if explicit:
        return explicit
    body: dict[str, object] = {"experiment": experiment, "agent": fingerprint}
    if not deterministic:
        import secrets

        body["execution_nonce"] = secrets.token_hex(16)  # 128-bit per-execution
    return "r_" + content_hash(body).removeprefix("sha256:")[:32]


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


def _agent_is_deterministic(agent: object) -> bool:
    return bool(getattr(agent, "is_deterministic", False))


def _effective_design(resolved: ResolvedExperiment, agent: object) -> str:
    """paired (McNemar) only when the agent's behavior is fixed by scenario+seed.

    A live model draws each condition independently — the 'pairs' are nominal, so
    McNemar's paired test is invalid and the comparison is independent samples. A
    declared matched_pairs design is rejected for a non-deterministic agent
    rather than silently producing a spurious paired p-value (review r4)."""
    declared = None
    design_obj = resolved.experiment.get("comparison_design")  # type: ignore[union-attr]
    if isinstance(design_obj, dict):
        declared = design_obj.get("kind")
    deterministic = _agent_is_deterministic(agent)
    if declared == "matched_pairs":
        if not deterministic:
            raise RunnerError(
                "comparison_design=matched_pairs requires a deterministic agent; a live "
                "model is sampled independently per condition — use independent_samples"
            )
        return "matched_pairs"
    if declared == "independent_samples":
        return "independent_samples"
    return "matched_pairs" if deterministic else "independent_samples"


def _aggregates(
    resolved: ResolvedExperiment, result: "object", agent: object
) -> list[dict[str, object]]:
    design = _effective_design(resolved, agent)
    aggregates: list[dict[str, object]] = []
    baseline = next(
        (str(c["id"]) for c in resolved.conditions if c["enforcement"] == "off"), None
    )
    counts = {
        str(c["id"]): _condition_counts(result, str(c["id"])) for c in resolved.conditions
    }
    for condition in resolved.conditions:
        condition_id = str(condition["id"])
        n, asr_succ, util_succ = counts[condition_id]
        if n == 0:
            # a condition where every trial failed produces NO aggregate rather
            # than crashing wilson_interval; missingness reports the gap (r7)
            continue
        for metric, successes in ((_METRIC_ASR, asr_succ), (_METRIC_UTILITY, util_succ)):
            test = None
            is_treated = baseline is not None and condition_id != baseline and metric == _METRIC_ASR
            base_n = counts[baseline][0] if baseline is not None else 0  # type: ignore[index]
            if is_treated and base_n > 0 and design == "matched_pairs":
                pairs = result.pairs(baseline, condition_id, metric="ASR")  # type: ignore[attr-defined]
                test = mcnemar_test(pairs, vs=baseline)
            elif is_treated and base_n > 0 and design == "independent_samples":
                base_asr = counts[baseline][1]  # type: ignore[index]
                test = two_proportion_test(base_asr, base_n, successes, n, vs=baseline)
            aggregates.append(
                binary_aggregate(metric, condition_id, successes, n, test=test,
                                 comparison_design=design)
            )
    return aggregates


def _condition_counts(result: "object", condition_id: str) -> tuple[int, int, int]:
    # only COMPLETED trials that actually produced an outcome (a failed trial has
    # none) — accessing result.outcomes[...] for a failed trial used to KeyError
    trials = [
        t for t in result.trials  # type: ignore[attr-defined]
        if t["condition_id"] == condition_id and str(t["trial_id"]) in result.outcomes  # type: ignore[attr-defined]
    ]
    outcomes = [result.outcomes[str(t["trial_id"])] for t in trials]  # type: ignore[attr-defined]
    return (
        len(outcomes),
        sum(1 for o in outcomes if o.violation),
        sum(1 for o in outcomes if o.task_success),
    )


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


def _environment(
    resolved: ResolvedExperiment, model: str | None = None,
    usage: dict[str, object] | None = None,
    agent: object | None = None,
) -> dict[str, object]:
    """Record the ACTUAL agent that ran (review §6.1). The bundle stays
    self-describing: kernel, the agent id, and (when imported) the dataset
    version."""
    kernels = sorted({str(c["kernel"]) for c in resolved.conditions})
    model = model or str(resolved.experiment["agent_ref"])
    provider = model.split(":", 1)[0] if ":" in model else (
        "scripted" if model.startswith("scripted") else "unknown"
    )
    inference_params: dict[str, object] = {"experiment_id": str(resolved.experiment["id"])}
    if usage is not None:
        inference_params["usage"] = usage
    env: dict[str, object] = {
        "model": {"provider": provider, "id": model, "inference_params": inference_params},
    }
    # the FIRST-CLASS comparison design, recorded at run time and bound to the
    # ACTUAL agent's determinism — this, not the uploader-controlled aggregate, is
    # what the CP bridge reads to choose matched_pairs vs independent_samples
    # (review r21). _effective_design already refuses matched_pairs for a live agent.
    if agent is not None:
        design = _effective_design(resolved, agent)
        deterministic = _agent_is_deterministic(agent)
        env["experiment_design"] = {
            "schema_version": "comparison-design/v1",
            "kind": design,
            "unit_key": ["execution_id", "scenario_id", "condition_id", "seed", "repeat_index"],
            "assignment": "shared_deterministic_agent_state" if design == "matched_pairs"
                          else "independent_per_condition",
            "agent_deterministic": deterministic,
        }
    # the global kernel_version is a convenience that only makes sense when every
    # condition shares one kernel — verify_bundle requires it to equal a condition
    # kernel. Emitting a comma-joined pseudo-value for a mixed-kernel bundle would
    # fail that check AFTER every (paid) trial ran; omit it instead (each trace's
    # producer.kernel_version, bound to its own condition, stays authoritative).
    if len(kernels) == 1:
        env["kernel_version"] = kernels[0]
    else:
        # a mixed-kernel run omits the single global kernel_version (now optional
        # in the schema) and records the distinct kernels explicitly, so the
        # bundle is schema-VALID and readable rather than a write-now/read-never
        # artifact (review r15). Each trace's producer.kernel_version stays
        # authoritative for its own condition.
        env["kernel_versions"] = kernels
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


def _first_denied_trace(traces: dict[str, dict[str, object]]) -> dict[str, object] | None:
    """The first trace where a denial was actually ENFORCED.

    Not merely "verdict == DENY". An observe-only arm records real denials and
    executes the call anyway, so a bare verdict match would happily pin a
    regression on a trace where nothing was contained — asserting the kernel
    must keep denying, on the evidence of a run that denied nothing.
    """
    for trace in sorted(traces.values(), key=lambda t: str(t["trace_id"])):
        for event in trace["events"]:  # type: ignore[union-attr]
            if event.get("type") == "gate_decision" and contained(event["decision"]):  # type: ignore[index,arg-type]
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
