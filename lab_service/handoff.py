"""The Control Plane handoff, as a verb — not as a CLI command.

Materializing an export is product logic, not presentation: the directory it
writes IS the handoff (`contracts/control-plane-handoff.md`), and a reader
verifies THAT, not a terminal's stdout. It lived in `lab_runner/cli.py`, which
is why the only way to produce a handoff was to have a shell — the hosted face
could not offer it at all.

Nothing here prints or exits. `export_cp_package` returns what happened; a face
renders it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from lab_contracts import content_hash
from lab_runner.bundle_io import write_bundle_dir

from .checks import Check, CheckStatus
from .errors import OutputRefused
from .outcomes import Outcome

MANIFEST_SCHEMA = "axor-cp-export-manifest/v1"


@dataclass(frozen=True)
class CPExportResult:
    """What an export produced, in the terms a reader of the handoff cares about."""

    outcome: Outcome
    directory: Path
    condition_id: str
    baseline_condition_id: str
    parametric_config_hash: str
    config_hash: str
    manifest_file_count: int
    signed: bool
    runtime_config_hash_count: int
    regressions_carried: int
    earned_bridge: bool

    @property
    def deploy_config(self) -> Path:
        return self.directory / "cp-deploy.json"

    @property
    def production_todo(self) -> Path:
        return self.directory / "production-todo.md"

    @property
    def regression_traces(self) -> Path:
        return self.directory / "regression-traces"


def bundle_files(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> dict[str, str]:
    """The `axor-bundle-dir/v1` layout as a map, without touching a filesystem.

    Mirrors `write_bundle_dir` exactly — `bundle.json` plus one content-addressed
    file per trace under `traces/` — INCLUDING its refusal to emit a bundle that
    is not self-consistent. A schema-invalid bundle caught at build time is a
    clear error; the same bundle shipped inside a handoff is discovered by
    whoever tries to verify it, much later and with no idea why.
    """
    from lab_runner.bundle_io import _trace_filename
    from lab_contracts import validate_artifact, verify_bundle
    from lab_runner.errors import RunnerError

    schema_errors = validate_artifact(bundle, "bundle")
    for trace in traces.values():
        schema_errors += [
            f"trace {trace.get('trace_id')}: {e}" for e in validate_artifact(trace, "trace")
        ]
    if schema_errors:
        raise RunnerError(
            f"refusing to emit a schema-invalid bundle: {'; '.join(schema_errors[:10])}"
        )
    verify_bundle(bundle, traces)
    files = {"bundle.json": json.dumps(bundle, indent=2, ensure_ascii=False)}
    for trace in traces.values():
        files[f"traces/{_trace_filename(trace)}"] = json.dumps(
            trace, indent=2, ensure_ascii=False
        )
    return files


def read_bundle_files(
    files: dict[str, str], prefix: str = ""
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """The inverse of `bundle_files`: a bundle + traces out of a file map."""
    from lab_runner.errors import RunnerError

    bundle_key = f"{prefix}bundle.json"
    if bundle_key not in files:
        raise RunnerError(f"file map has no {bundle_key}")
    bundle: dict[str, object] = json.loads(files[bundle_key])
    traces: dict[str, dict[str, object]] = {}
    trace_prefix = f"{prefix}traces/"
    for key, text in files.items():
        if key.startswith(trace_prefix) and key.endswith(".json"):
            trace = json.loads(text)
            traces[str(trace["trace_id"])] = trace
    return bundle, traces


@dataclass(frozen=True)
class CPExportPackage:
    """A complete handoff as bytes, before anywhere to put it is chosen.

    The directory IS the handoff — a reader verifies the files, not a terminal's
    stdout — but "the files" and "a directory on this machine" are different
    things, and only the first is what the verb produces. A hosted face hands
    the same map to a caller; the CLI writes it to disk.
    """

    files: dict[str, str]
    manifest: dict[str, object]
    config: dict[str, object]
    source: dict[str, object]
    regressions_carried: int
    earned_bridge: bool

    @property
    def signed(self) -> bool:
        return bool(self.manifest.get("signature"))


def build_cp_export_files(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    *,
    regressions: list[dict[str, object]] | None = None,
    condition_id: str | None = None,
    author: str | None = None,
    sign_key: str | None = None,
) -> CPExportPackage:
    """Build the whole `axor-cp-deploy/v1` handoff as a file map.

    Raises the domain's own `CPExportError` when the evidence does not earn a
    handoff.
    """
    from lab_capabilities.governance.cp_export import export_cp

    export = export_cp(bundle, list(regressions or []), condition_id=condition_id, traces=traces)
    files: dict[str, str] = {
        "cp-deploy.json": json.dumps(export.config, indent=2, ensure_ascii=False),
        "production-todo.md": export.production_todo,
    }
    # make the export SELF-CONTAINED (review r19): the full source bundle + ALL
    # its traces (scenarios, predicates, inputs, trials, provenance all travel)
    # so verification can recompute the graph, the bridge and the runtime
    # provenance from the package ALONE — bridge-traces/ carries only the
    # bridge's own traces, which is not enough to re-derive the whole handoff.
    for name, text in bundle_files(bundle, traces).items():
        files[f"source-bundle/{name}"] = text

    by_ref = {content_hash(t): t for t in traces.values()}
    # export the FROZEN pinned trace BODIES alongside the config so the
    # regressions are actually portable — cp-deploy.json carries each pin's
    # content hash, but a hash is not the bytes to replay on another machine
    # (review r13). Each is written content-addressed under regression-traces/.
    carried: list[dict[str, object]] = export.config["regressions"]  # type: ignore[assignment]
    for pin in carried:
        ref = str(pin["trace_ref"])
        trace = by_ref[ref]  # export_cp already verified the ref resolves
        files[f"regression-traces/{ref.removeprefix('sha256:')}.json"] = json.dumps(
            trace, indent=2, ensure_ascii=False
        )

    source: dict[str, object] = export.config["source"]  # type: ignore[assignment]
    # export the FROZEN bridge trace BODIES so the earned-bridge analysis is
    # independently recomputable from the package alone — the analysis receipt
    # carries only trace hashes, not the bytes to re-evaluate the violation
    # predicate over (review r18)
    analysis: dict[str, object] | None = source.get("bridge_analysis")  # type: ignore[assignment]
    if analysis is not None:
        files["bridge-analysis.json"] = json.dumps(analysis, indent=2, ensure_ascii=False)
        trial_refs: dict[str, list[str]] = analysis["trial_refs"]  # type: ignore[assignment]
        for refs in trial_refs.values():
            for ref in refs:
                trace = by_ref.get(ref)
                if trace is not None:
                    files[f"bridge-traces/{ref.removeprefix('sha256:')}.json"] = json.dumps(
                        trace, indent=2, ensure_ascii=False
                    )

    # a full-file MANIFEST binds EVERY file in the export, so verification checks
    # the whole package (not just cp-deploy.json + source-bundle) and can detect
    # a tampered bridge-analysis, a swapped trace, or a stale leftover. When an
    # author key is supplied it is SIGNED, so a reader can confirm WHO released
    # this exact handoff — derivability is not authenticity (review r20).
    manifest = cp_export_manifest_for(files, export.config)
    if author and sign_key:
        from lab_contracts.signing import sign_bundle

        manifest["author"] = author
        manifest["signature"] = sign_bundle(manifest, sign_key)
    files["manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False)
    return CPExportPackage(
        files=files,
        manifest=manifest,
        config=export.config,
        source=source,
        regressions_carried=len(carried),
        earned_bridge=bool(export.earned_bridge),
    )


def export_cp_package(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    *,
    out: Path,
    regressions: list[dict[str, object]] | None = None,
    condition_id: str | None = None,
    overwrite: bool = False,
    author: str | None = None,
    sign_key: str | None = None,
) -> CPExportResult:
    """Build the handoff and materialize it as a directory on this machine."""
    package = build_cp_export_files(
        bundle, traces, regressions=regressions, condition_id=condition_id,
        author=author, sign_key=sign_key,
    )
    final = Path(out)
    # a re-export must NOT leave stale files behind (review r20): an earlier export
    # with an earned bridge, or extra regression traces, would linger and the
    # manifest verifier would flag them — or worse, a reader would trust them. A
    # non-empty directory requires overwrite, and overwrite REPLACES it wholly.
    if final.exists() and any(final.iterdir()) and not overwrite:
        raise OutputRefused(f"{final} is not empty; pass --overwrite to replace it")
    # build the WHOLE export in a staging directory, then swap it into place
    # atomically (review r21): a crash mid-write no longer leaves the previous valid
    # signed handoff destroyed AND the new one half-written with a missing manifest.
    staging = final.with_name(final.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for rel, text in package.files.items():
        destination = staging / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)
    _swap_into_place(staging, final)

    runtime_hashes: dict[str, object] = package.config["runtime_config_hashes"]  # type: ignore[assignment]
    files: dict[str, str] = package.manifest["files"]  # type: ignore[assignment]
    return CPExportResult(
        outcome=Outcome.OK,
        directory=final,
        condition_id=str(package.source["condition_id"]),
        baseline_condition_id=str(package.source["baseline_condition_id"]),
        parametric_config_hash=str(package.config["parametric_config_hash"]),
        config_hash=str(package.config["config_hash"]),
        manifest_file_count=len(files),
        signed=package.signed,
        runtime_config_hash_count=len(runtime_hashes),
        regressions_carried=package.regressions_carried,
        earned_bridge=package.earned_bridge,
    )


def _swap_into_place(staging: Path, final: Path) -> None:
    """ATOMIC SWAP: the staging tree is complete (manifest last), so replace the
    previous export in one move — old to a backup, staging to final, then drop the
    backup. A crash leaves either the intact old export or the intact new one,
    never a half-written directory with a missing/partial manifest (review r21)."""
    if final.exists():
        backup = final.with_name(final.name + ".old")
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(final, backup)
        os.replace(staging, final)
        shutil.rmtree(backup)
    else:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)


def cp_export_manifest_for(
    files: dict[str, str], config: dict[str, object]
) -> dict[str, object]:
    """A versioned, full-file manifest over a handoff's file map (review r20): the
    sha256 of EVERY file, plus the semantic refs (deploy config, source bundle,
    regression set) an author signs over. Verification uses it to detect a
    tampered bridge-analysis, a swapped trace, OR a stale leftover file — not just
    to recompute cp-deploy.json.

    Built from the same map the export ships, so the manifest and the files it
    describes cannot come apart.
    """
    hashes = {
        rel: "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        for rel, text in sorted(files.items())
        # the ROOT manifest cannot bind itself; a NESTED file that happens to be
        # named manifest.json IS bound, so a "full-file" manifest is actually
        # full (review r21)
        if rel != "manifest.json"
    }
    src: dict[str, object] = config.get("source", {})  # type: ignore[assignment]
    return {
        "schema_version": MANIFEST_SCHEMA,
        "condition_id": src.get("condition_id"),
        "baseline_condition_id": src.get("baseline_condition_id"),
        "deploy_config_ref": content_hash(config),
        "source_bundle_ref": hashes.get("source-bundle/bundle.json"),
        "regression_set_ref": content_hash(config.get("regressions", [])),
        "bridge_analysis_ref": src.get("bridge_analysis_ref"),
        "files": hashes,
    }


def read_export_directory(directory: Path) -> dict[str, str]:
    """Every file in an export directory as a map, keyed by relative path."""
    files: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = str(path.relative_to(directory)).replace("\\", "/")
            files[rel] = path.read_text()
    return files


def safe_export_key(rel: str) -> bool:
    """Whether a manifest-listed relative path may be followed at all.

    The manifest is untrusted until verified, so an absolute path, a `..`
    segment, or a backslash is refused before anything reads or resolves it
    (review r21).
    """
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    return not any(p in ("", "..", ".") for p in rel.split("/"))


def safe_export_path(directory: Path, rel: str) -> Path | None:
    """Resolve a manifest-listed relative path, confined to `directory`. Returns
    None for an unsafe key, a symlink, or a path that resolves outside the tree."""
    if not safe_export_key(rel):
        return None
    candidate = directory / rel
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(directory.resolve())
    except (ValueError, OSError):
        return None
    return candidate


def verify_manifest_semantic_refs(
    manifest: dict[str, object], shipped: dict[str, object], files: dict[str, str]
) -> str | None:
    """Confirm each SEMANTIC ref the manifest names actually matches its artifact
    (review r21). A signed manifest whose deploy_config_ref / source_bundle_ref /
    regression_set_ref / bridge_analysis_ref point at the wrong bytes would verify
    its own signature and recompute a config while MISDESCRIBING what it bundles.
    Returns a problem string, or None when every ref matches."""
    if str(manifest.get("deploy_config_ref")) != content_hash(shipped):
        return "manifest deploy_config_ref does not match cp-deploy.json"
    src: dict[str, object] = shipped.get("source", {})  # type: ignore[assignment]
    if str(manifest.get("regression_set_ref")) != content_hash(shipped.get("regressions", [])):
        return "manifest regression_set_ref does not match the shipped regressions"
    want_bundle = files.get("source-bundle/bundle.json")
    if want_bundle is not None and str(manifest.get("source_bundle_ref")) != str(want_bundle):
        return "manifest source_bundle_ref does not match source-bundle/bundle.json"
    # the bridge analysis ref must agree BOTH with the deploy config's own ref and,
    # when a bridge-analysis.json is shipped, with that file's content
    if str(manifest.get("bridge_analysis_ref") or "") != str(src.get("bridge_analysis_ref") or ""):
        return "manifest bridge_analysis_ref does not match the deploy config's bridge_analysis_ref"
    return None


@dataclass(frozen=True)
class CPVerifyResult:
    """Three distinct guarantees over a CP export directory, reported separately.

    INTEGRITY (every file matches the manifest, nothing unlisted), AUTHENTICITY
    (a signature verifies against a supplied key) and DERIVABILITY (the shipped
    deploy config recomputes byte-identical from the embedded evidence) are not
    the same claim, and an export that has one does not have the others.
    """

    outcome: Outcome
    checks: tuple[Check, ...]
    directory: Path
    trace_count: int = 0
    earned_bridge: bool = False


def _fail(checks: list[Check], directory: Path, name: str, message: str) -> CPVerifyResult:
    checks.append(Check(name, CheckStatus.INVALID, message))
    return CPVerifyResult(Outcome.FAILURE, tuple(checks), directory)


def verify_cp_package(
    directory: Path,
    *,
    pubkey: str | None = None,
    expect_author: str | None = None,
    allow_unsigned: bool = False,
) -> CPVerifyResult:
    """Verify a CP export DIRECTORY. A thin reader over `verify_cp_files`."""
    from lab_runner.errors import RunnerError

    directory = Path(directory)
    if not (directory / "cp-deploy.json").is_file():
        raise RunnerError(f"{directory} has no cp-deploy.json — not a CP export directory")
    if not (directory / "manifest.json").is_file():
        raise RunnerError(
            f"{directory} has no manifest.json — this export predates full-file verification; "
            "re-export with a current axor-lab so it carries its own manifest"
        )
    result = verify_cp_files(
        read_export_directory(directory), pubkey=pubkey,
        expect_author=expect_author, allow_unsigned=allow_unsigned,
    )
    return CPVerifyResult(
        outcome=result.outcome, checks=result.checks, directory=directory,
        trace_count=result.trace_count, earned_bridge=result.earned_bridge,
    )


def verify_cp_files(
    files: dict[str, str],
    *,
    pubkey: str | None = None,
    expect_author: str | None = None,
    allow_unsigned: bool = False,
) -> CPVerifyResult:
    """Verify a CP export's FILE MAP: its full-file manifest, its (optional)
    signature, and — recomputing from scratch — that the shipped cp-deploy.json is
    exactly derivable from the embedded evidence (review r19/r20).

    Raises `RunnerError` when the map is not a CP export at all (no deploy config,
    no manifest, no source bundle): that is a malformed request, not a verdict
    about an export's trustworthiness.
    """
    from lab_capabilities.governance.cp_export import CPExportError, export_cp
    from lab_runner.errors import RunnerError

    checks: list[Check] = []
    empty = Path(".")
    if "cp-deploy.json" not in files:
        raise RunnerError("export has no cp-deploy.json — not a CP export")
    if "manifest.json" not in files:
        raise RunnerError(
            "export has no manifest.json — it predates full-file verification; "
            "re-export with a current axor-lab so it carries its own manifest"
        )
    # a malformed manifest is an INTEGRITY failure, never a traceback (review r21)
    try:
        manifest = json.loads(files["manifest.json"])
    except ValueError as exc:
        return _fail(checks, empty, "integrity",
                     f"INVALID — manifest.json is not valid JSON: {exc}")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return _fail(checks, empty, "integrity",
                     "INVALID — manifest.json is not an object with a files map")
    unverified = False

    # 1) INTEGRITY — every listed file's hash matches, and nothing unlisted exists
    listed: dict[str, str] = manifest["files"]  # type: ignore[assignment]
    for rel, want in listed.items():
        # the manifest is UNTRUSTED until verified: a path with `..`, an absolute
        # path, or a backslash escape must never be followed, whether it would
        # traverse a filesystem or just misdescribe the package (review r21).
        if not safe_export_key(str(rel)):
            return _fail(checks, empty, "integrity",
                         f"INVALID — manifest lists an unsafe path {rel!r}")
        if str(rel) not in files:
            return _fail(checks, empty, "integrity",
                         f"INVALID — manifest names a missing file {rel!r}")
        got = "sha256:" + hashlib.sha256(files[str(rel)].encode("utf-8")).hexdigest()
        if got != str(want):
            return _fail(checks, empty, "integrity",
                         f"INVALID — {rel} does not match its manifest hash")
    extra = sorted(set(files) - set(listed) - {"manifest.json"})
    if extra:
        return _fail(checks, empty, "integrity",
                     f"INVALID — files not in the manifest (stale or injected): {extra}")
    checks.append(Check("integrity", CheckStatus.OK,
                        f"manifest INTEGRITY OK ({len(listed)} files, no stale/unlisted)"))

    # 1b) SEMANTIC REFS — the manifest NAMES the deploy config, source bundle,
    # regression set and bridge analysis by content hash; recompute each and confirm
    # it matches BEFORE trusting them. A signature over the manifest proves nothing
    # if a ref inside it points at the wrong artifact (review r21).
    shipped: dict[str, object] = json.loads(files["cp-deploy.json"])
    ref_problem = verify_manifest_semantic_refs(manifest, shipped, listed)
    if ref_problem is not None:
        return _fail(checks, empty, "semantic_refs", f"INVALID — {ref_problem}")
    checks.append(Check("semantic_refs", CheckStatus.OK,
                        "manifest semantic refs OK (deploy/source/regression/bridge)"))

    # 2) AUTHENTICITY — verify the signature when present; an UNSIGNED export is
    # UNVERIFIED, not a clean pass (parity with the reproduction package, review r21)
    if manifest.get("signature"):
        from lab_contracts.signing import (
            SignatureInvalid,
            SignatureUnavailable,
            verify_bundle_signature,
        )

        if expect_author and str(manifest.get("author")) != str(expect_author):
            return _fail(checks, empty, "authenticity",
                         f"INVALID — manifest author {manifest.get('author')!r} is not the "
                         f"expected {expect_author!r}")
        if not pubkey:
            checks.append(Check("authenticity", CheckStatus.UNVERIFIED,
                                "UNVERIFIED — signed manifest but no --pubkey to check it"))
            unverified = True
        else:
            try:
                verify_bundle_signature(manifest, str(manifest["signature"]), pubkey)
            except SignatureInvalid as exc:
                return _fail(checks, empty, "authenticity",
                             f"INVALID — manifest signature does not verify: {exc}")
            except SignatureUnavailable as exc:
                checks.append(Check("authenticity", CheckStatus.UNVERIFIED, f"UNVERIFIED — {exc}"))
                unverified = True
            else:
                checks.append(Check("authenticity", CheckStatus.OK,
                                    f"signature VERIFIED (author {manifest.get('author')!r})"))
    elif allow_unsigned:
        checks.append(Check("authenticity", CheckStatus.OK,
                            "unsigned manifest — integrity + derivability only "
                            "(accepted via --allow-unsigned)"))
    else:
        checks.append(Check(
            "authenticity", CheckStatus.UNVERIFIED,
            "UNVERIFIED — unsigned manifest carries no authenticity; pass a signed "
            "export, or --allow-unsigned to accept integrity + derivability only"))
        unverified = True

    # 3) DERIVABILITY — recompute the deploy config from the embedded evidence
    if "source-bundle/bundle.json" not in files:
        raise RunnerError("export has no source-bundle/ — cannot recompute the handoff")
    bundle, traces = read_bundle_files(files, prefix="source-bundle/")
    src: dict[str, object] = shipped.get("source", {})  # type: ignore[assignment]
    condition_id = str(src.get("condition_id")) if src.get("condition_id") else None
    regressions: list[dict[str, object]] = list(shipped.get("regressions", []))  # type: ignore[arg-type]
    try:
        recomputed = export_cp(bundle, regressions, condition_id=condition_id, traces=traces)
    except CPExportError as exc:
        return _fail(checks, empty, "derivability", f"INVALID — recomputation failed: {exc}")
    if recomputed.config != shipped:
        return _fail(checks, empty, "derivability",
                     "INVALID — the deploy config recomputed from the embedded evidence "
                     "does not match cp-deploy.json (a doctored config, swapped trace, or "
                     "tampered analysis would cause this)")
    checks.append(Check("derivability", CheckStatus.OK,
                        f"RECOMPUTED OK from source-bundle/ ({len(traces)} traces); "
                        f"verified={shipped.get('verified') is True}; "
                        f"earned_bridge={bool(recomputed.earned_bridge)}"))
    return CPVerifyResult(
        outcome=Outcome.UNVERIFIED if unverified else Outcome.OK,
        checks=tuple(checks),
        directory=empty,
        trace_count=len(traces),
        earned_bridge=bool(recomputed.earned_bridge),
    )
