"""Subprocess worker for the replay bit-identity check (acceptance test 5).

Runs the slice experiment, replays the bundle, and prints the sha256 of the
canonical replay report — so two INDEPENDENT interpreter processes can be
compared byte-for-byte, standing in for "two machines"."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import support  # noqa: E402
from lab_ref import replay_bundle, run_experiment  # noqa: E402
from lab_ref.bundle import build_bundle  # noqa: E402

REPEATS = 12
CREATED = "2026-07-19T00:00:00Z"


def main() -> None:
    scenario = support.banking_scenario()
    result = run_experiment(
        scenario, support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=REPEATS, run_id="r_replay",
    )
    bundle = build_bundle(
        bundle_id="b_replay", created=CREATED, scenarios=[scenario],
        conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials, aggregates=[],
        traces=result.traces,
    )
    kernels = {k.version: k for k in support.kernel_registry().kernels}
    report = replay_bundle(bundle, result.traces, kernels)
    assert report.bit_identical, "recomputed verdicts differ from recorded"
    print(hashlib.sha256(report.canonical().encode("utf-8")).hexdigest())


if __name__ == "__main__":
    main()
