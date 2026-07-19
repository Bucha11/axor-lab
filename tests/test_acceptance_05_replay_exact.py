"""Acceptance test 5 — replay is exact.

`replay` recomputes the DENY over the frozen trace, bit-identical, twice, in
two independent interpreter processes (standing in for two machines), with
the pinned kernel. Replay carries no CI — it is exact or it is a bug.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from tests import support
from lab_runner import ScriptedAgent, replay_trace, run_trial

REPO_ROOT = Path(__file__).resolve().parent.parent
ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)


class TestReplayExact(unittest.TestCase):
    def test_replay_recomputes_the_recorded_deny(self) -> None:
        scenario = support.banking_scenario()
        governed = support.conditions()[1]
        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        trace = run_trial(
            scenario, support.manifests(), governed, kernel,
            run_id="r_rp", seed="s007", repeat_index=7, agent=ATTACK_ALWAYS,
        ).trace
        recomputed, matches = replay_trace(
            trace, governed, kernel, support.manifests(), scenario["inputs"]  # type: ignore[arg-type]
        )
        self.assertTrue(matches)
        self.assertEqual([d["verdict"] for d in recomputed], ["DENY"])
        self.assertEqual(recomputed[0]["gate"], "taint_floor")

    def test_replay_is_deterministic_within_process(self) -> None:
        scenario = support.banking_scenario()
        governed = support.conditions()[1]
        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        trace = run_trial(
            scenario, support.manifests(), governed, kernel,
            run_id="r_rp", seed="s003", repeat_index=3, agent=ATTACK_ALWAYS,
        ).trace
        first, _ = replay_trace(trace, governed, kernel, support.manifests(), scenario["inputs"])  # type: ignore[arg-type]
        second, _ = replay_trace(trace, governed, kernel, support.manifests(), scenario["inputs"])  # type: ignore[arg-type]
        self.assertEqual(first, second)

    def test_replay_is_bit_identical_across_two_processes(self) -> None:
        digests = [
            subprocess.run(
                [sys.executable, str(REPO_ROOT / "tests" / "replay_worker.py")],
                capture_output=True, text=True, check=True, cwd=REPO_ROOT,
            ).stdout.strip()
            for _ in range(2)
        ]
        self.assertEqual(digests[0], digests[1])
        self.assertEqual(len(digests[0]), 64)

    def test_replay_carries_no_confidence_interval(self) -> None:
        scenario = support.banking_scenario()
        governed = support.conditions()[1]
        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        trace = run_trial(
            scenario, support.manifests(), governed, kernel,
            run_id="r_rp", seed="s001", repeat_index=1, agent=ATTACK_ALWAYS,
        ).trace
        recomputed, _ = replay_trace(trace, governed, kernel, support.manifests(), scenario["inputs"])  # type: ignore[arg-type]
        for decision in recomputed:
            self.assertNotIn("interval", decision)
            self.assertNotIn("ci", decision)


if __name__ == "__main__":
    unittest.main()
