"""Run/trial/trace identity includes the actual agent (review round 3, Patch 11).

run_id was hash(experiment) only, and trial_id_for omitted run_id — so two runs
of the same experiment with different --agent produced identical run/trial/trace
ids. Distinct executions then looked like retries of one trial. Identity now
carries the agent fingerprint (run_id) and trial ids are scoped to the run.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_runner import trial_id_for

REPO = Path(__file__).resolve().parent.parent
CREATED = "2026-07-19T12:00:00+00:00"


class TestTrialIdIsRunScoped(unittest.TestCase):
    def test_same_coordinate_different_run_is_a_different_trial_id(self) -> None:
        a = trial_id_for("rA", "banking-exfil-01", "governed", "s000", 0)
        b = trial_id_for("rB", "banking-exfil-01", "governed", "s000", 0)
        self.assertNotEqual(a, b)

    def test_same_run_same_coordinate_is_stable(self) -> None:
        a = trial_id_for("rA", "banking-exfil-01", "governed", "s000", 0)
        b = trial_id_for("rA", "banking-exfil-01", "governed", "s000", 0)
        self.assertEqual(a, b)


class TestDifferentAgentsAreDifferentRuns(unittest.TestCase):
    def _run_with_cassette(self, root: Path, name: str, recipient: str) -> dict:
        cassette = root / f"{name}.json"
        cassette.write_text(json.dumps(
            [{"tool": "send_money", "args": {"recipient": recipient, "amount": 1200}}]
        ))
        axl = root / f"{name}.axl"
        document = json.loads((REPO / "examples" / "banking-exfil-01.axl").read_text())
        document["experiment"]["repeats"] = 4
        axl.write_text(json.dumps(document))
        out = root / f"bundle_{name}"
        run = subprocess.run(
            [sys.executable, "-m", "lab_runner", "run", str(axl), "--out", str(out),
             "--yes", "--created", CREATED, "--agent", f"cassette:{cassette}"],
            capture_output=True, text=True, cwd=REPO, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads((out / "bundle.json").read_text())

    def test_two_agents_produce_disjoint_trial_and_trace_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self._run_with_cassette(root, "attacker", support.ATTACKER_IBAN)
            b = self._run_with_cassette(root, "faithful", support.LANDLORD_IBAN)

            trials_a = {t["trial_id"] for t in a["trials"]}
            trials_b = {t["trial_id"] for t in b["trials"]}
            self.assertTrue(trials_a.isdisjoint(trials_b), "different agents share trial ids")

            run_ids_a = {t["run_id"] for t in _bundle_traces(root, "attacker")}
            run_ids_b = {t["run_id"] for t in _bundle_traces(root, "faithful")}
            self.assertTrue(run_ids_a.isdisjoint(run_ids_b), "different agents share run ids")


def _bundle_traces(root: Path, name: str) -> list[dict]:
    out = []
    for path in (root / f"bundle_{name}" / "traces").glob("*.json"):
        trace = json.loads(path.read_text())
        out.append(trace["trial"])
    return out


if __name__ == "__main__":
    unittest.main()
