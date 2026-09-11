"""An artifact from the dispatch path must actually verify.

`collect_suite_run` accepted traces, recomputed aggregates and packaged an
artifact — and the bundle inside it failed `verify_bundle` with "ran under
condition X with a kernel but records no runtime_config_hash". So the primary
entry point produced evidence that could be collected and published but never
checked, and nothing noticed because nothing on that path called the verifier.

The fix is not to relax the check. Lab RECOMPUTES the hash from the assignment
it issued — the frozen manifests and the arm's policy with the scenario's
`$inputs` expanded — which is the same "recompute, never adopt" rule this
module already applies to aggregates. What the runtime reports is COMPARED, not
accepted: a runtime that governed a different contract than the one it was
handed must not have its run reported as evidence for the assigned config.
"""

from __future__ import annotations

import importlib.util
import threading
import unittest

from lab_contracts import verify_bundle
from lab_contracts.bundle import config_provenance
from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_suite import DispatchError, assign_suite, builtin_registry, collect_suite_run

HAS_WRAP = importlib.util.find_spec("axor_wrap") is not None

CREATED = "2026-08-04T00:00:00+00:00"


def _tools():
    return {
        "read_txns": lambda: {"transactions": [{"description": "routine"}]},
        "send_money": lambda recipient, amount=1: {"ok": True},
    }


def _drive(report_hash):
    """Run a real dispatch cycle over HTTP; `report_hash(toolset, kernel)` is
    what the runtime claims it governed under."""
    from axor_wrap import LabRuntimeConnector, toolset_for_arm, trial_of

    store = RuntimeJobStore()
    server = make_runtime_server(port=0, control_token="c", store=store)
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runtime = LabRuntimeConnector(base, control_token="c")
        connected = runtime.connect(runtime_label="byo", agent_ref="acme/bot")
        assignment = assign_suite(
            builtin_registry().get("budget").manifest(),
            str(connected["runtime_ref"]), store,
        )
        job_id = str(runtime.poll_jobs()[0]["job_id"])
        job = runtime.claim(job_id)
        plan = job["assignment"]
        scenarios = {str(s["name"]): s for s in plan["scenarios"]}
        kernel = str(plan["conditions"][0]["kernel"])
        for unit in job["planned_trials"]:
            scenario_id, _, _ = str(unit).rsplit(":", 2)
            toolset = toolset_for_arm(_tools(), plan, str(unit))
            toolset.call("read_txns", {})
            trace = toolset.trace(
                trial_of(str(unit), run_id=assignment.run_id),
                scenario=scenarios[scenario_id],
            )
            runtime.complete_trial(
                job_id, str(unit), trace, metrics={"duration_ms": 1.0},
                runtime_config_hash=report_hash(toolset, kernel),
            )
        return collect_suite_run(assignment, store)
    finally:
        server.shutdown()


@unittest.skipUnless(HAS_WRAP, "axor-wrap not installed")
class TestTheArtifactVerifies(unittest.TestCase):
    def setUp(self) -> None:
        self.run = _drive(lambda toolset, kernel: toolset.runtime_config_hash(kernel))
        self.artifact = self.run.artifact(
            "a1", CREATED, {"model": {"provider": "byo", "id": "acme/bot"}},
        )

    def test_every_planned_trial_completed(self) -> None:
        self.assertTrue(self.run.trials)
        self.assertEqual({str(t["status"]) for t in self.run.trials}, {"completed"})

    def test_the_bundle_passes_verify_bundle(self) -> None:
        """The whole point. It did not, and nothing on this path checked."""
        verify_bundle(self.artifact["bundle"], self.run.traces)

    def test_a_completed_trial_records_the_config_it_ran_under(self) -> None:
        for trial in self.run.trials:
            with self.subTest(trial=trial["trial_id"]):
                self.assertTrue(trial.get("runtime_config_hash"))
                self.assertEqual(trial["runtime_provenance"], "recorded_at_execution")

    def test_the_recorded_hash_is_the_one_the_assignment_compiles_to(self) -> None:
        bundle = self.artifact["bundle"]
        provenance = config_provenance(
            bundle["scenarios"], bundle["conditions"],
            bundle["tool_manifests"], bundle["trials"],
        )
        self.assertIsNotNone(provenance)


@unittest.skipUnless(HAS_WRAP, "axor-wrap not installed")
class TestTheRuntimeIsCheckedNotTrusted(unittest.TestCase):
    def test_a_runtime_that_governed_a_different_config_is_refused(self) -> None:
        """Reporting the hash is what makes `recorded_at_execution` true. It is
        only worth anything if a wrong one is caught."""
        with self.assertRaises(DispatchError) as ctx:
            _drive(lambda toolset, kernel: "sha256:" + "0" * 64)
        self.assertIn("governed a different contract", str(ctx.exception))

    def test_a_silent_runtime_gets_the_hash_without_the_claim(self) -> None:
        """Lab can still derive the config from its own plan, but nothing proves
        that was the config in force at execution — so the trial does not claim
        it, and an evidence-backed export may refuse it on that ground."""
        run = _drive(lambda toolset, kernel: None)
        for trial in run.trials:
            with self.subTest(trial=trial["trial_id"]):
                self.assertTrue(trial.get("runtime_config_hash"))
                self.assertEqual(trial["runtime_provenance"], "reconstructed_legacy")


if __name__ == "__main__":
    unittest.main()
