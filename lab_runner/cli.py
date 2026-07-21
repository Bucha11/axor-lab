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

from lab_analysis import binary_aggregate, mcnemar_test, missingness, two_proportion_test
from lab_analysis.errors import AnalysisError
from lab_agent.errors import AgentError
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

from .axor_backend import resolve_kernel
from .bundle_io import (
    PACKAGING,
    read_bundle_dir,
    read_bundle_source,
    write_bundle_dir,
    write_superseded_attempts,
)
from .claims import deny_claim_text
from .errors import ExperimentFileError, RunnerError
from .evidence import build_evidence_case, evidence_condition, validate_twin
from .experiment_file import ResolvedExperiment, load_axl, resolve
from .kernel import Kernel, default_registry
from .regression import STATUS_DIFFERS, STATUS_MATCHES, RegressionPin, check_pins, pin
from .replay import replay_bundle
from .runner import run_experiment_suite

# BYOK backend + statistics failures are separate hierarchies from RunnerError;
# main() maps them to stable exit codes instead of leaking a traceback
_AGENT_ANALYSIS_ERRORS = (AgentError, AnalysisError)

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
        # BYOK backend / analysis failures (BackendUnavailable, CassetteExhausted,
        # ProtocolViolation, AnalysisError, InsufficientDataError) are their own
        # hierarchies, not RunnerError — catch them so the user gets a stable
        # message + exit code instead of a Python traceback (review r2 §BYOK)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except ContractsError as exc:
        # claim typing / contract-layer errors surface as validation failures
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


def _terminal_label(stopped_reason: str | None, n_completed: int) -> str:
    """The honest terminal status line for a run (review r14). A run stopped by
    the cost ceiling is never `[completed]`: it is `[completed_partial]` if it
    produced any completed trials, else `[stopped_cost_ceiling]` (nothing ran)."""
    if not stopped_reason:
        return "[completed]"
    return "[completed_partial]" if n_completed > 0 else "[stopped_cost_ceiling]"


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
    # run identity includes the ACTUAL agent — otherwise two runs of the same
    # experiment with different --agent get the same run_id (and, before, the
    # same trial/trace ids), so different executions looked like retries of one
    # trial (review r3). The fingerprint is the agent's CONTENT (cassette bytes /
    # model id), so identical agents reproduce the same id and different ones don't.
    agent = _resolve_agent_override(args.agent) if args.agent else resolved.agent
    fingerprint = _agent_fingerprint(args, resolved)
    # 128-bit id (32 hex chars) from the experiment+agent fingerprint — the old
    # 8-char (32-bit) slice was birthday-collision-searchable, so two unrelated
    # runs could share a run_id and look like retries of one trial (review r7).
    # For a NONDETERMINISTIC agent a fresh random execution nonce is folded in, so
    # two live runs of the same experiment are distinct executions (review r13).
    run_id = _derive_run_id(
        args.run_id, resolved.experiment, fingerprint,
        deterministic=bool(getattr(agent, "is_deterministic", True)),
    )
    model = _estimate_model(args, resolved)
    # a HARD run-wide cost ceiling: checked against ACTUAL usage between trials,
    # so the run stops before the next provider call (review r11). Unset → no bind.
    from lab_agent.cost import CostBudget

    try:
        budget = CostBudget(
            max_usd=getattr(args, "max_usd", None),
            max_input_tokens=getattr(args, "max_input_tokens", None),
            max_output_tokens=getattr(args, "max_output_tokens", None),
        )
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc

    # thread the ceiling INTO the agent so its multi-turn loop is guarded before
    # every provider call — the between-trials check below only bounds overshoot
    # to whole trials, not to a single trial's fan-out of calls (review r12)
    if budget.is_set() and hasattr(agent, "budget"):
        agent.budget = budget  # type: ignore[attr-defined]
        agent.model = model  # type: ignore[attr-defined]

    def _budget_check() -> str | None:
        if not budget.is_set() or not hasattr(agent, "usage"):
            return None
        return budget.exceeded(agent.usage(), model)  # type: ignore[attr-defined]

    result = run_experiment_suite(
        list(resolved.scenarios),
        resolved.manifests,
        list(resolved.conditions),
        resolved.kernel_registry,
        repeats=resolved.repeats,
        run_id=run_id,
        agent=agent,
        budget_check=_budget_check,
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
    if result.stopped_reason:
        print(f"  [cost_ceiling] run stopped early: {result.stopped_reason}", file=sys.stderr)

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
    # record ACTUAL usage + spend when the agent tracks it (BYOK); scripted/
    # cassette report zero, which is correct (no paid inference)
    usage = None
    if hasattr(agent, "usage"):
        from lab_agent.cost import actual_usd
        u = agent.usage()  # type: ignore[attr-defined]
        in_tok, out_tok = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        usage = {"input_tokens": in_tok, "output_tokens": out_tok,
                 "usd": actual_usd(in_tok, out_tok, model)}
        if result.stopped_reason:
            usage["stopped_reason"] = result.stopped_reason
    bundle = build_bundle(
        bundle_id=f"b_{run_id}",
        created=created,
        scenarios=list(resolved.scenarios),
        conditions=list(resolved.conditions),
        tool_manifests=list(resolved.manifests.values()),
        environment=_environment(resolved, model, usage),
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
    # an honest terminal label: a cost-stopped run is NOT "[completed]" — it
    # either produced a usable partial (some completed trials) or nothing
    # (review r14). The bundle is still written for the partial evidence.
    label = _terminal_label(result.stopped_reason, n_completed)
    print(f"{label}  bundle: {out}/bundle.json ({len(result.traces)} traces)")
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


def _package_receipt(path: Path) -> dict[str, object] | None:
    """The portable verification receipt from a downloaded `.json` package, or
    None for a bundle DIRECTORY (which ships no receipt)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    receipt = data.get("receipt") if isinstance(data, dict) else None
    return receipt if isinstance(receipt, dict) else None


def _cmd_verify(args: argparse.Namespace) -> int:
    """Standalone, offline verification of a downloaded reproduction package —
    NO server trusted (review r14). Confirms: content hashes verify, verdicts
    replay bit-identically, and (for a downloaded package) the portable receipt's
    signed_ref matches the bundle and any author signature verifies."""
    path = Path(args.package)
    # read_bundle_source runs schema validation + verify_bundle (content hashes);
    # a corrupt package is a clean RunnerError → exit 1, not a traceback
    bundle, traces = read_bundle_source(path)
    print(f"content hashes: OK ({len(traces)} trace(s), {len(bundle['conditions'])} conditions)")  # type: ignore[arg-type]
    versions = tuple(str(c["kernel"]) for c in bundle["conditions"])  # type: ignore[union-attr]
    kernels = {k.version: k for k in default_registry(versions).kernels}
    report = replay_bundle(bundle, traces, kernels)
    if not report.bit_identical:
        print("replay MISMATCH: recomputed verdicts differ from recorded", file=sys.stderr)
        return EXIT_FAILURE
    print(f"replay: bit-identical over {len(report.decisions)} trace(s)")

    receipt = _package_receipt(path)
    if receipt is None:
        print("receipt: none (a bundle directory carries no portable receipt)")
        return EXIT_OK
    from lab_contracts.signing import SignatureInvalid, SignatureUnavailable, verify_receipt

    pubkey = getattr(args, "pubkey", None)
    expected_author = getattr(args, "author", None)
    try:
        verify_receipt(bundle, receipt, pubkey, expected_author=expected_author)
    except SignatureInvalid as exc:
        print(f"receipt: INVALID — {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except SignatureUnavailable as exc:
        # signed_ref + structure OK, but the signature itself could NOT be checked
        # (no key supplied / PyNaCl absent). This is NOT a pass — return a distinct
        # nonzero code so a CI gate never treats "unverified" as "verified" (r15)
        print(f"receipt: UNVERIFIED (integrity OK, authenticity unchecked) — {exc}", file=sys.stderr)
        return EXIT_UNVERIFIED
    if str(receipt.get("integrity")) == "signed":
        print(
            f"receipt: signature VERIFIED (author {receipt.get('author')!r}, "
            f"key {receipt.get('key_id')!r})"
        )
    else:
        print(f"receipt: signed_ref OK ({receipt.get('integrity')}; hash-only, no signature)")
    return EXIT_OK


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
    if args.disable_taint_floor:
        # explicit variant demonstration: force the reference kernel with the
        # gate off (the fingerprint marks it a different kernel, review r4)
        kernel: object = Kernel(version=version, taint_floor_enabled=False)
    else:
        # regress under the SAME kernel run/replay would use — so a real
        # axor-core pin regresses under the real governor, not the reference
        # taint-floor kernel (review r6: one kernel path everywhere)
        kernel = resolve_kernel(version, manifests, condition.get("policy"),  # type: ignore[arg-type]
                                default_registry((version,)))
    # each pinned trace replays against ITS OWN scenario's inputs — a single
    # shared inputs dict would replay every pin under the first scenario's
    # allowlist / effect-resolution inputs (review r12)
    results = check_pins(
        pins, traces, condition, kernel, manifests,
        inputs_for=lambda trace: _scenario_for(bundle, trace).get("inputs", {}),  # type: ignore[union-attr,arg-type]
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
    # when the condition pins the installed build — not always the reference
    # kernel via default_registry, which would render reference-kernel verdicts
    # under a production kernel version (review r12)
    version = str(condition["kernel"])
    kernel = resolve_kernel(version, manifests, condition.get("policy"),  # type: ignore[arg-type]
                            default_registry((version,)))
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
    return EXIT_OK


def _cmd_export_cp(args: argparse.Namespace) -> int:
    from .cp_export import CPExportError, export_cp

    bundle, traces = read_bundle_dir(Path(args.bundle))
    regressions: list[dict[str, object]] = []
    if args.pins:
        # pass the FULL pin objects (trace_ref + expected_sequence too) so
        # export_cp can validate each against the bundle's traces before it
        # carries the pin into a production Control Plane config (review r12)
        regressions = list(json.loads(Path(args.pins).read_text()))
    try:
        export = export_cp(bundle, regressions, condition_id=args.condition, traces=traces)
    except CPExportError as exc:
        raise RunnerError(str(exc)) from exc
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cp-deploy.json").write_text(json.dumps(export.config, indent=2, ensure_ascii=False))
    (out / "production-todo.md").write_text(export.production_todo)
    # export the FROZEN pinned trace BODIES alongside the config so the
    # regressions are actually portable — cp-deploy.json carries each pin's
    # content hash, but a hash is not the bytes to replay on another machine
    # (review r13). Each is written content-addressed under regression-traces/.
    carried: list[dict[str, object]] = export.config["regressions"]  # type: ignore[assignment]
    if carried:
        by_ref = {content_hash(t): t for t in traces.values()}
        rt_dir = out / "regression-traces"
        rt_dir.mkdir(exist_ok=True)
        for pin in carried:
            ref = str(pin["trace_ref"])
            trace = by_ref[ref]  # export_cp already verified the ref resolves
            (rt_dir / (ref.removeprefix("sha256:") + ".json")).write_text(
                json.dumps(trace, indent=2, ensure_ascii=False)
            )
    source: dict[str, object] = export.config["source"]  # type: ignore[assignment]
    print(f"exported CP deploy config -> {out}/cp-deploy.json")
    print(f"  condition: {source['condition_id']} (baseline: {source['baseline_condition_id']})")
    print(f"  config_hash (carry-over key): {export.config['config_hash']}")
    print(f"  regressions carried: {len(carried)}"
          + (f" (frozen trace bodies in {out}/regression-traces/)" if carried else ""))
    print(f"  production-todo (NOT reused): {out}/production-todo.md")
    if export.earned_bridge:
        print(f"  earned bridge: {source['condition_id']} changed the outcome vs "
              f"{source['baseline_condition_id']} — run THIS governed config in production.")
    else:
        print("  note: no aggregate shows governance changed an outcome yet "
              "(the bridge surfaces once one does).")
    return EXIT_OK


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

    from .replay import REPLAY_MATCH, replay_trace_status

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
    # wrong/reconstructed condition would surface here as a mismatch
    kernel = resolve_kernel(
        str(condition["kernel"]), manifests_by_id, condition.get("policy"),  # type: ignore[arg-type]
        default_registry((str(condition["kernel"]),)),
    )
    _, status = replay_trace_status(
        trace, condition, kernel, manifests_by_id, scenario.get("inputs", {}),  # type: ignore[arg-type]
    )
    if status != REPLAY_MATCH:
        raise RunnerError(
            f"incident trace does not replay under its condition (status={status}) — "
            "refusing to import a bundle whose verdicts don't reproduce"
        )

    trials = [{
        "trial_id": content_hash(trace), "scenario_id": str(trial["scenario_id"]),
        "condition_id": str(trial["condition_id"]), "seed": str(trial["seed"]),
        "repeat_index": int(trial["repeat_index"]), "status": "completed",
        "trace_ref": content_hash(trace),
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

    The real backend handles `enforcement == "off"` as observe-only (an ALLOW
    with observation still on, no gates applied), so a baseline on the real
    kernel behaves identically to one on the reference kernel — but now both
    arms share one kernel, and the bundle has a single kernel_version."""
    from lab_contracts import condition_config_hash
    from lab_runner import axor_available, real_kernel_version

    if not axor_available():
        raise RunnerError("--real-kernel requested but axor-core is not installed")
    version = real_kernel_version()
    experiment: dict[str, object] = document["experiment"]  # type: ignore[assignment]
    for condition in experiment.get("conditions", []):  # type: ignore[union-attr]
        condition["kernel"] = version
        condition["config_hash"] = condition_config_hash(version, condition.get("policy"))
    print(f"  repinned ALL conditions (baseline + governed) to the real kernel: {version}")


def _resolve_agent_override(spec: str) -> object:
    """Build a BYOK agent from --agent (cassette:<file> | anthropic:<model>)."""
    from lab_agent import AnthropicBackend, FileCassetteAgent, WrappedModelAgent

    kind, _, param = spec.partition(":")
    if kind == "cassette":
        return FileCassetteAgent(path=Path(param))
    if kind == "anthropic":
        return WrappedModelAgent(backend=AnthropicBackend(model=param or "claude-opus-4-8"))
    raise RunnerError(f"unknown --agent {spec!r}; use cassette:<file> or anthropic:<model>")


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


def _agent_fingerprint(args: argparse.Namespace, resolved: ResolvedExperiment) -> str:
    """A content fingerprint of the agent that will actually run — folded into
    run identity so different agents are different runs (review r3)."""
    if not args.agent:
        return str(resolved.experiment["agent_ref"])
    kind, _, param = args.agent.partition(":")
    if kind == "cassette":
        try:
            return "cassette:" + content_hash({"cassette": Path(param).read_text()})
        except OSError:
            return f"cassette:{param}"
    return args.agent  # e.g. anthropic:<model>


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
    resolved: ResolvedExperiment, model: str, usage: dict[str, object] | None = None
) -> dict[str, object]:
    """Record the ACTUAL agent that ran — not always 'scripted' (review §6.1).
    The bundle stays self-describing: kernel, the real model provider/id, the
    experiment id, the ACTUAL token usage + spend (review r11), and (when
    imported) the dataset version."""
    kernels = sorted({str(c["kernel"]) for c in resolved.conditions})
    provider = model.split(":", 1)[0] if ":" in model else (
        "scripted" if model.startswith("scripted") else
        "anthropic" if model.startswith("claude") else
        "cassette" if model.startswith("cassette") else "unknown"
    )
    inference_params: dict[str, object] = {"experiment_id": str(resolved.experiment["id"])}
    if usage is not None:
        inference_params["usage"] = usage  # actual tokens + spend, recorded in the bundle
    env: dict[str, object] = {
        "model": {"provider": provider, "id": model, "inference_params": inference_params},
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
    # hard run-wide cost ceilings (review r11): checked against ACTUAL usage
    # between trials, so the run stops before the next provider call
    p_run.add_argument("--max-usd", type=float, default=None,
                       help="stop the run once estimated spend reaches this USD ceiling")
    p_run.add_argument("--max-input-tokens", type=int, default=None,
                       help="stop the run once actual input tokens reach this ceiling")
    p_run.add_argument("--max-output-tokens", type=int, default=None,
                       help="stop the run once actual output tokens reach this ceiling")
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
    p_export.set_defaults(func=_cmd_export_cp)

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
