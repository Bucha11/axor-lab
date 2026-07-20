"""Bundle directory I/O — the `axor-bundle-dir/v1` on-disk layout.

    <out>/bundle.json          the bundle/v1 document (content hashes inside)
    <out>/traces/<hash>.json   one trace/v1 per trial, named by content hash

Trace files are named by their content hash, not their `trace_id`: that is
collision-free even if two traces shared an id, and immune to a hostile
`trace_id` from an imported trace trying to escape the traces directory
(path traversal). Raw bodies never leave the trace previews; compression is
`none` in the reference layout (zstd is a packaging option, not a requirement).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from lab_contracts import content_hash, validate_artifact, verify_bundle

from .errors import RunnerError

PACKAGING: dict[str, object] = {
    "layout": "axor-bundle-dir/v1",
    "manifest": "bundle.json",
    "traces_dir": "traces/",
    "compression": "none",
}
_BUNDLE_FILE = "bundle.json"
_TRACES_DIR = "traces"


def _trace_filename(trace: dict[str, object]) -> str:
    """Content-hash filename — stable, collision-free, traversal-proof."""
    return content_hash(trace).replace("sha256:", "") + ".json"


def write_bundle_dir(
    directory: Path,
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    *,
    overwrite: bool = False,
) -> None:
    """Write a bundle directory atomically.

    Verifies integrity before writing anything, stages into a sibling temp
    directory, then swaps it in with a single rename so a crash mid-write never
    leaves a half-written bundle. Refuses to write into a non-empty directory
    unless ``overwrite=True`` — and when it does overwrite it replaces the whole
    directory, so stale traces from a prior run never linger and get republished.
    """
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()) and not overwrite:
        raise RunnerError(
            f"{directory} is not empty; pass overwrite=True to replace it"
        )
    # fail before touching the filesystem if the bundle is not self-consistent
    verify_bundle(bundle, traces)

    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bundle-staging-", dir=parent))
    try:
        traces_dir = staging / _TRACES_DIR
        traces_dir.mkdir(parents=True, exist_ok=True)
        (staging / _BUNDLE_FILE).write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False)
        )
        for trace in traces.values():
            (traces_dir / _trace_filename(trace)).write_text(
                json.dumps(trace, indent=2, ensure_ascii=False)
            )
        if directory.exists():
            shutil.rmtree(directory)
        staging.replace(directory)  # atomic on the same filesystem
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def write_superseded_attempts(
    directory: Path, superseded: list[dict[str, object]]
) -> Path | None:
    """Persist superseded retry attempts as a sidecar audit log, OUTSIDE the
    publishable bundle (they would orphan the bundle graph, review r8/r9). Call
    after write_bundle_dir so it lands in the final directory. Returns the path
    written, or None when there is nothing to record.

    The file is named `.unverified.json` and written atomically (temp + rename):
    it is an honest audit trail, NOT verified evidence — it carries no content
    hash and is not part of the signed/verified bundle spine, so its name says so
    rather than implying the same integrity as bundle.json (review r10). A
    content-hashed, schema-validated attempts graph inside the bundle contract is
    the stronger follow-up."""
    if not superseded:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "superseded_attempts.unverified.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(superseded, indent=2, ensure_ascii=False))
    tmp.replace(path)  # atomic on the same filesystem
    return path


def read_bundle_dir(directory: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Load, schema-validate, and hash-verify a bundle directory.

    Schema validation runs BEFORE integrity so a self-consistent but
    schema-invalid bundle (which would pass hashing and then crash a downstream
    consumer with a KeyError) is rejected here with a clear message.
    """
    directory = Path(directory)
    bundle_path = directory / _BUNDLE_FILE
    if not bundle_path.is_file():
        raise RunnerError(f"{directory} is not a bundle directory (no {_BUNDLE_FILE})")
    bundle: dict[str, object] = json.loads(bundle_path.read_text())

    traces: dict[str, dict[str, object]] = {}
    traces_dir = directory / _TRACES_DIR
    if traces_dir.is_dir():
        for path in sorted(traces_dir.glob("*.json")):
            trace: dict[str, object] = json.loads(path.read_text())
            trace_id = str(trace["trace_id"])
            if trace_id in traces:
                raise RunnerError(
                    f"duplicate trace_id {trace_id!r} in {traces_dir} — corrupt bundle"
                )
            traces[trace_id] = trace

    errors = validate_artifact(bundle, "bundle")
    for trace in traces.values():
        errors += [
            f"trace {trace['trace_id']}: {e}" for e in validate_artifact(trace, "trace")
        ]
    if errors:
        raise RunnerError(
            f"{directory} failed schema validation: " + "; ".join(errors[:10])
        )

    verify_bundle(bundle, traces)
    return bundle, traces
