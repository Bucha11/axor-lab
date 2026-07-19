"""Bundle directory I/O — the `axor-bundle-dir/v1` on-disk layout.

    <out>/bundle.json        the bundle/v1 document (content hashes inside)
    <out>/traces/<id>.json   one trace/v1 per trial

Raw bodies never leave the trace previews; compression is `none` in the
reference layout (zstd is a packaging option, not a requirement).
"""

from __future__ import annotations

import json
from pathlib import Path

from lab_contracts import verify_bundle

from .errors import RunnerError

PACKAGING: dict[str, object] = {
    "layout": "axor-bundle-dir/v1",
    "manifest": "bundle.json",
    "traces_dir": "traces/",
    "compression": "none",
}
_BUNDLE_FILE = "bundle.json"
_TRACES_DIR = "traces"


def write_bundle_dir(
    directory: Path, bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> None:
    traces_dir = directory / _TRACES_DIR
    traces_dir.mkdir(parents=True, exist_ok=True)
    (directory / _BUNDLE_FILE).write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
    for trace in traces.values():
        (traces_dir / f"{trace['trace_id']}.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False)
        )


def read_bundle_dir(directory: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Load and hash-verify a bundle directory; raises on any mismatch."""
    bundle_path = directory / _BUNDLE_FILE
    if not bundle_path.is_file():
        raise RunnerError(f"{directory} is not a bundle directory (no {_BUNDLE_FILE})")
    bundle: dict[str, object] = json.loads(bundle_path.read_text())
    traces: dict[str, dict[str, object]] = {}
    traces_dir = directory / _TRACES_DIR
    if traces_dir.is_dir():
        for path in sorted(traces_dir.glob("*.json")):
            trace: dict[str, object] = json.loads(path.read_text())
            traces[str(trace["trace_id"])] = trace
    verify_bundle(bundle, traces)
    return bundle, traces
