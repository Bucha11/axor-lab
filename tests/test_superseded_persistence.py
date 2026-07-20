"""Superseded retry attempts are persisted for audit (review r9, P2).

The retry model (r8) moves a prior attempt + its trace into result.superseded,
but the CLI only ever wrote result.trials / result.traces / aggregates — so
after the process exited the audit history was gone, and "both attempts are
preserved" was true only for the in-memory object. The run command now writes a
superseded_attempts.json sidecar (kept OUT of the publishable bundle so it can't
orphan the graph).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_contracts import content_hash
from lab_runner import run_experiment, ScriptedAgent
from lab_runner.bundle_io import write_superseded_attempts

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)


class TestSupersededPersistence(unittest.TestCase):
    def test_empty_superseded_writes_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(write_superseded_attempts(Path(tmp), []))
            self.assertFalse((Path(tmp) / "superseded_attempts.json").exists())

    def test_superseded_attempts_are_written_and_survive(self) -> None:
        cond = support.conditions()[1]
        result = run_experiment(
            support.banking_scenario(), support.manifests(), [cond],
            support.kernel_registry(), repeats=1, run_id="r_sup", agent=ATTACK_ALWAYS,
        )
        # merge a genuinely different retry at the same key → supersession
        faithful = run_experiment(
            support.banking_scenario(), support.manifests(), [cond],
            support.kernel_registry(), repeats=1, run_id="r_sup", agent=ScriptedAgent(attack_rate=0.0),
        )
        key = list(faithful.outcomes)[0]
        first_ref = result.trials[0]["trace_ref"]
        result.add(key, {**result.trials[0], "trace_ref": content_hash(faithful.outcomes[key].trace)},
                   faithful.outcomes[key])
        self.assertEqual(len(result.superseded), 1)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            out.mkdir()
            path = write_superseded_attempts(out, result.superseded)
            self.assertIsNotNone(path)
            # the audit log persists the prior attempt AND its trace on disk
            persisted = json.loads(path.read_text())
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]["trace_ref"], first_ref)
            self.assertIn("trace", persisted[0])  # the superseded evidence is kept


if __name__ == "__main__":
    unittest.main()
