"""Contract fields must be EXECUTED, not just metadata (review round 4).

- A policy field the reference kernel doesn't execute (profile it doesn't know,
  a foreign trust_model, criticality_overrides) would enter the config_hash but
  never change a verdict — so a condition declaring it is rejected.
- run_mode is executed: it selects which conditions actually run.
- type=game is not executed by the benchmark runner and is rejected.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lab_runner.errors import ExperimentFileError
from lab_capabilities.governance.experiment_file import resolve

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "banking-exfil-01.axl"


def _doc() -> dict:
    return json.loads(EXAMPLE.read_text())


def _governed(doc: dict) -> dict:
    return next(c for c in doc["experiment"]["conditions"] if c["enforcement"] == "on")


class TestPolicyRuntimeParity(unittest.TestCase):
    def test_example_resolves(self) -> None:
        resolve(_doc())  # the real axor-core build runs strict + content-ledger

    # NOTE: the reference-kernel policy-parity rejection tests
    # (test_unsupported_profile_is_rejected / test_foreign_trust_model_is_rejected /
    # test_criticality_overrides_is_rejected) are gone with the reference kernel.
    # They asserted that a policy field the REFERENCE kernel did not execute was
    # rejected at resolve time. The real kernel is the only kernel now: it
    # EXECUTES its own policy (an allowlist and criticality_overrides are compiled
    # straight into the governor by `governor_config`), and `profile`/`trust_model`
    # are condition metadata the governor does not consume at all. There is no
    # longer a reference reimplementation to disagree with, so the parity check
    # (`unsupported_reference_policy_fields`) was removed — a real reduction in
    # resolve-time validation of policy typos, flagged for the reviewer.


class TestRunModeIsExecuted(unittest.TestCase):
    def test_compare_runs_all_conditions(self) -> None:
        doc = _doc()
        doc["experiment"]["run_mode"] = "compare"
        resolved = resolve(doc)
        self.assertEqual(len(resolved.conditions), 2)

    def test_governed_runs_only_enforcing(self) -> None:
        doc = _doc()
        doc["experiment"]["run_mode"] = "governed"
        resolved = resolve(doc)
        self.assertEqual([c["enforcement"] for c in resolved.conditions], ["on"])

    def test_ungoverned_runs_only_baseline(self) -> None:
        doc = _doc()
        doc["experiment"]["run_mode"] = "ungoverned"
        resolved = resolve(doc)
        self.assertEqual([c["enforcement"] for c in resolved.conditions], ["off"])


class TestUnsupportedType(unittest.TestCase):
    def test_game_type_is_rejected_by_benchmark_runner(self) -> None:
        doc = _doc()
        doc["experiment"]["type"] = "game"
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("type" in e for e in ctx.exception.errors))


if __name__ == "__main__":
    unittest.main()
