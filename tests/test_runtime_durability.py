"""Runtime-jobs durability: a run's results survive a process restart.

The store is in-memory by default (a fresh store knows nothing); given a root it
persists each run's assignment + collected traces/aggregates and reloads them on
construction, so a completed run stays queryable after a bounce. Runtimes are not
persisted — a runtime reconnects for a fresh key; the valuable data is the run.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lab_server.runtime_jobs import RuntimeJobStore, RuntimeJobsError


class TestRuntimeDurability(unittest.TestCase):
    def test_completed_run_survives_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runtime-jobs"
            s1 = RuntimeJobStore(root=root)
            rt = s1.connect_runtime(model="scripted")
            ref = str(rt["runtime_ref"])
            rid = str(s1.create_run(ref, {"experiment": "x"}, planned=["t1"])["run_id"])
            s1.claim(rid, ref)
            s1.complete_trial(rid, "t1", ref, {"trace_id": "tr1", "events": []}, status="completed")
            s1.attach_aggregates(rid, [{"metric": "asr"}])
            self.assertEqual(s1.run_state(rid), "completed")

            # a fresh store from the same root reloads the run + its results
            s2 = RuntimeJobStore(root=root)
            self.assertEqual(s2.run_state(rid), "completed")
            res = s2.results(rid)
            self.assertEqual(len(res["traces"]), 1)
            self.assertEqual(res["aggregates"], [{"metric": "asr"}])

            # a new run gets a fresh, non-colliding id past the reloaded one
            rt2 = s2.connect_runtime()
            rid2 = str(s2.create_run(str(rt2["runtime_ref"]), {}, planned=["t1"])["run_id"])
            self.assertNotEqual(rid2, rid)

    def test_no_root_stays_in_memory(self) -> None:
        s1 = RuntimeJobStore()  # no root → nothing persisted
        rt = s1.connect_runtime()
        rid = str(s1.create_run(str(rt["runtime_ref"]), {}, planned=["t1"])["run_id"])
        s2 = RuntimeJobStore()
        with self.assertRaises(RuntimeJobsError):
            s2.run_state(rid)


if __name__ == "__main__":
    unittest.main()
