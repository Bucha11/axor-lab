"""The simulator honors its Tool Manifest contract (review round 6, P0).

The host used to declare ANY side-effecting tool "simulated" and return a
success-like result (defaulting to a noop_stub adapter), ignoring
simulation.supported, the declared adapter, and args_schema. A benchmark could
then look like it exercised delete_repository when nothing was modelled.
"""

from __future__ import annotations

import copy
import unittest

from lab_runner import SimulatedToolHost
from lab_runner.errors import SimulationError
from tests import support


def _host(manifests) -> SimulatedToolHost:
    return SimulatedToolHost(manifests=manifests, fixtures={}, injection_text="x")


class TestSimulatorContract(unittest.TestCase):
    def setUp(self) -> None:
        self.manifests = copy.deepcopy(support.manifests())

    def test_supported_true_known_adapter_simulates(self) -> None:
        host = _host(self.manifests)
        result = host.execute("send_money", {"recipient": "a@b.com", "amount": 10})
        self.assertEqual(result["status"], "simulated")
        self.assertEqual(result["adapter"], "ledger_stub")

    def test_supported_false_is_not_faked(self) -> None:
        self.manifests["send_money"]["simulation"]["supported"] = False
        host = _host(self.manifests)
        with self.assertRaises(SimulationError) as ctx:
            host.execute("send_money", {"recipient": "a@b.com", "amount": 10})
        self.assertIn("supported=false", str(ctx.exception))

    def test_unknown_adapter_is_a_validation_error(self) -> None:
        self.manifests["send_money"]["simulation"]["adapter"] = "delete_everything_stub"
        host = _host(self.manifests)
        with self.assertRaises(SimulationError) as ctx:
            host.execute("send_money", {"recipient": "a@b.com", "amount": 10})
        self.assertIn("unknown simulation adapter", str(ctx.exception))

    def test_missing_simulation_block_is_rejected(self) -> None:
        self.manifests["send_money"].pop("simulation", None)
        host = _host(self.manifests)
        with self.assertRaises(SimulationError):
            host.execute("send_money", {"recipient": "a@b.com", "amount": 10})

    def test_args_violating_schema_are_rejected_not_coerced(self) -> None:
        host = _host(self.manifests)
        with self.assertRaises(SimulationError) as ctx:
            # recipient must be a string, amount a number
            host.execute("send_money", {"recipient": ["a@b.com"], "amount": "one thousand"})
        self.assertIn("args_schema", str(ctx.exception))


class TestFixtureResultValidation(unittest.TestCase):
    def test_fixture_result_must_match_result_schema(self) -> None:
        import json
        from pathlib import Path

        from lab_runner.errors import ExperimentFileError
        from lab_runner.experiment_file import resolve

        example = Path(__file__).resolve().parent.parent / "examples" / "banking-exfil-01.axl"
        doc = json.loads(example.read_text())
        # corrupt the read_txns fixture so its result violates result_schema
        scenario = doc["scenarios"][0]
        scenario["fixtures"]["read_txns"]["result"] = {"transactions": "not-an-array"}
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("result" in e for e in ctx.exception.errors))


if __name__ == "__main__":
    unittest.main()
