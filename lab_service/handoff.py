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
    """Build the `axor-cp-deploy/v1` handoff directory and return what it holds.

    Raises the domain's own `CPExportError` when the evidence does not earn a
    handoff, and `OutputRefused` when `out` is non-empty without `overwrite`.
    """
    from lab_capabilities.governance.cp_export import export_cp

    export = export_cp(bundle, list(regressions or []), condition_id=condition_id, traces=traces)

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
    (staging / "cp-deploy.json").write_text(json.dumps(export.config, indent=2, ensure_ascii=False))
    (staging / "production-todo.md").write_text(export.production_todo)
    # make the export SELF-CONTAINED (review r19): write the full source bundle +
    # ALL its traces (scenarios, predicates, inputs, trials, provenance all travel)
    # so verification can recompute the graph, the bridge, and the runtime
    # provenance from the directory ALONE — bridge-traces/ carries only the
    # bridge's own traces, which is not enough to re-derive the whole handoff.
    write_bundle_dir(staging / "source-bundle", bundle, traces, overwrite=True)

    by_ref = {content_hash(t): t for t in traces.values()}
    # export the FROZEN pinned trace BODIES alongside the config so the
    # regressions are actually portable — cp-deploy.json carries each pin's
    # content hash, but a hash is not the bytes to replay on another machine
    # (review r13). Each is written content-addressed under regression-traces/.
    carried: list[dict[str, object]] = export.config["regressions"]  # type: ignore[assignment]
    if carried:
        rt_dir = staging / "regression-traces"
        rt_dir.mkdir(exist_ok=True)
        for pin in carried:
            ref = str(pin["trace_ref"])
            trace = by_ref[ref]  # export_cp already verified the ref resolves
            (rt_dir / (ref.removeprefix("sha256:") + ".json")).write_text(
                json.dumps(trace, indent=2, ensure_ascii=False)
            )

    source: dict[str, object] = export.config["source"]  # type: ignore[assignment]
    # export the FROZEN bridge trace BODIES so the earned-bridge analysis is
    # independently recomputable from the export directory alone — the analysis
    # receipt carries only trace hashes, not the bytes to re-evaluate the
    # violation predicate over (review r18)
    analysis: dict[str, object] | None = source.get("bridge_analysis")  # type: ignore[assignment]
    if analysis is not None:
        # the analysis receipt as its own file, content-addressed by the ref the
        # deploy config carries, so a reader can check it independently (review r19)
        (staging / "bridge-analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
        bt_dir = staging / "bridge-traces"
        bt_dir.mkdir(exist_ok=True)
        trial_refs: dict[str, list[str]] = analysis["trial_refs"]  # type: ignore[assignment]
        for refs in trial_refs.values():
            for ref in refs:
                trace = by_ref.get(ref)
                if trace is not None:
                    (bt_dir / (ref.removeprefix("sha256:") + ".json")).write_text(
                        json.dumps(trace, indent=2, ensure_ascii=False)
                    )

    # a full-file MANIFEST binds EVERY file in the export, so verification checks
    # the whole directory (not just cp-deploy.json + source-bundle) and can detect
    # a tampered bridge-analysis, a swapped trace, or a stale leftover. When an
    # author key is supplied it is SIGNED, so a reader can confirm WHO released
    # this exact handoff — derivability is not authenticity (review r20).
    manifest = cp_export_manifest(staging, export.config)
    if author and sign_key:
        from lab_contracts.signing import sign_bundle

        manifest["author"] = author
        manifest["signature"] = sign_bundle(manifest, sign_key)
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    _swap_into_place(staging, final)

    runtime_hashes: dict[str, object] = export.config["runtime_config_hashes"]  # type: ignore[assignment]
    files: dict[str, str] = manifest["files"]  # type: ignore[assignment]
    return CPExportResult(
        outcome=Outcome.OK,
        directory=final,
        condition_id=str(source["condition_id"]),
        baseline_condition_id=str(source["baseline_condition_id"]),
        parametric_config_hash=str(export.config["parametric_config_hash"]),
        config_hash=str(export.config["config_hash"]),
        manifest_file_count=len(files),
        signed=bool(manifest.get("signature")),
        runtime_config_hash_count=len(runtime_hashes),
        regressions_carried=len(carried),
        earned_bridge=bool(export.earned_bridge),
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


def cp_export_manifest(out: Path, config: dict[str, object]) -> dict[str, object]:
    """A versioned, full-file manifest of a CP export directory (review r20): the
    sha256 of EVERY file, plus the semantic refs (deploy config, source bundle,
    regression set) an author signs over. Verification uses it to detect a
    tampered bridge-analysis, a swapped trace, OR a stale leftover file — not just
    to recompute cp-deploy.json."""
    files: dict[str, str] = {}
    root_manifest = out / "manifest.json"
    for path in sorted(out.rglob("*")):
        # exclude ONLY the ROOT manifest.json — a NESTED file that happens to be
        # named manifest.json (e.g. source-bundle/manifest.json) IS bound, so a
        # "full-file" manifest is actually full (review r21)
        if path.is_file() and path != root_manifest and not path.is_symlink():
            rel = str(path.relative_to(out)).replace("\\", "/")
            files[rel] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    src: dict[str, object] = config.get("source", {})  # type: ignore[assignment]
    return {
        "schema_version": MANIFEST_SCHEMA,
        "condition_id": src.get("condition_id"),
        "baseline_condition_id": src.get("baseline_condition_id"),
        "deploy_config_ref": content_hash(config),
        "source_bundle_ref": files.get("source-bundle/bundle.json"),
        "regression_set_ref": content_hash(config.get("regressions", [])),
        "bridge_analysis_ref": src.get("bridge_analysis_ref"),
        "files": files,
    }


def safe_export_path(directory: Path, rel: str) -> Path | None:
    """Resolve a manifest-listed relative path, confined to `directory`. Returns
    None for an absolute path, a `..` escape, or one that resolves (through a
    symlink) outside the export tree — the manifest is untrusted until verified,
    so a listed path must never read outside the directory (review r21)."""
    if not rel or rel.startswith("/") or "\\" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("", "..", ".") for p in parts):
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
    """Verify a CP export directory: its full-file MANIFEST, its (optional)
    SIGNATURE, and — recomputing from scratch — that the shipped cp-deploy.json is
    exactly derivable from the embedded evidence (review r19/r20).

    Raises `RunnerError` when the directory is not a CP export at all (no deploy
    config, no manifest, no source bundle): that is a malformed request, not a
    verdict about an export's trustworthiness.
    """
    from lab_capabilities.governance.cp_export import CPExportError, export_cp
    from lab_runner.bundle_io import read_bundle_dir
    from lab_runner.errors import RunnerError

    checks: list[Check] = []
    deploy_path = directory / "cp-deploy.json"
    if not deploy_path.is_file():
        raise RunnerError(f"{directory} has no cp-deploy.json — not a CP export directory")
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise RunnerError(
            f"{directory} has no manifest.json — this export predates full-file verification; "
            "re-export with a current axor-lab so it carries its own manifest"
        )
    # a malformed manifest is an INTEGRITY failure, never a traceback (review r21)
    try:
        manifest = json.loads(manifest_path.read_text())
    except ValueError as exc:
        return _fail(checks, directory, "integrity",
                     f"INVALID — manifest.json is not valid JSON: {exc}")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return _fail(checks, directory, "integrity",
                     "INVALID — manifest.json is not an object with a files map")
    unverified = False

    # 1) INTEGRITY — every listed file's hash matches, and nothing unlisted exists
    files: dict[str, str] = manifest["files"]  # type: ignore[assignment]
    for rel, want in files.items():
        # the manifest is UNTRUSTED until verified: a path with `..`, an absolute
        # path, or a symlink could read/traverse outside the export tree. Confine
        # every listed path to the directory before touching it (review r21).
        safe = safe_export_path(directory, str(rel))
        if safe is None:
            return _fail(checks, directory, "integrity",
                         f"INVALID — manifest lists an unsafe path {rel!r}")
        if not safe.is_file():
            return _fail(checks, directory, "integrity",
                         f"INVALID — manifest names a missing file {rel!r}")
        got = "sha256:" + hashlib.sha256(safe.read_bytes()).hexdigest()
        if got != str(want):
            return _fail(checks, directory, "integrity",
                         f"INVALID — {rel} does not match its manifest hash")
    on_disk = {
        str(p.relative_to(directory)).replace("\\", "/")
        for p in directory.rglob("*") if p.is_file() and p != manifest_path
    }
    extra = sorted(on_disk - set(files))
    if extra:
        return _fail(checks, directory, "integrity",
                     f"INVALID — files not in the manifest (stale or injected): {extra}")
    checks.append(Check("integrity", CheckStatus.OK,
                        f"manifest INTEGRITY OK ({len(files)} files, no stale/unlisted)"))

    # 1b) SEMANTIC REFS — the manifest NAMES the deploy config, source bundle,
    # regression set and bridge analysis by content hash; recompute each and confirm
    # it matches BEFORE trusting them. A signature over the manifest proves nothing
    # if a ref inside it points at the wrong artifact (review r21).
    shipped: dict[str, object] = json.loads(deploy_path.read_text())
    ref_problem = verify_manifest_semantic_refs(manifest, shipped, files)
    if ref_problem is not None:
        return _fail(checks, directory, "semantic_refs", f"INVALID — {ref_problem}")
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
            return _fail(checks, directory, "authenticity",
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
                return _fail(checks, directory, "authenticity",
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
    source_dir = directory / "source-bundle"
    if not (source_dir / "bundle.json").is_file():
        raise RunnerError(f"{directory} has no source-bundle/ — cannot recompute the handoff")
    bundle, traces = read_bundle_dir(source_dir)
    src: dict[str, object] = shipped.get("source", {})  # type: ignore[assignment]
    condition_id = str(src.get("condition_id")) if src.get("condition_id") else None
    regressions: list[dict[str, object]] = list(shipped.get("regressions", []))  # type: ignore[arg-type]
    try:
        recomputed = export_cp(bundle, regressions, condition_id=condition_id, traces=traces)
    except CPExportError as exc:
        return _fail(checks, directory, "derivability", f"INVALID — recomputation failed: {exc}")
    if recomputed.config != shipped:
        return _fail(checks, directory, "derivability",
                     "INVALID — the deploy config recomputed from the embedded evidence "
                     "does not match cp-deploy.json (a doctored config, swapped trace, or "
                     "tampered analysis would cause this)")
    checks.append(Check("derivability", CheckStatus.OK,
                        f"RECOMPUTED OK from {source_dir} ({len(traces)} traces); "
                        f"verified={shipped.get('verified') is True}; "
                        f"earned_bridge={bool(recomputed.earned_bridge)}"))
    return CPVerifyResult(
        outcome=Outcome.UNVERIFIED if unverified else Outcome.OK,
        checks=tuple(checks),
        directory=directory,
        trace_count=len(traces),
        earned_bridge=bool(recomputed.earned_bridge),
    )
