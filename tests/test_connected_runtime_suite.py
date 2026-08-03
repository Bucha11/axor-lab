"""Bring your own agent: Lab assigns, the runtime executes.

The entry point the architecture boundary mandates. The user wraps their agent
with axor-wrap, it runs on THEIR machine against THEIR tools, and pushes traces
outward; Lab hands out assignments and reads what comes back.

The runtime here stands in for axor-wrap's `LabRuntimeConnector`, which already
speaks this protocol (poll → claim → post events → complete with a trace). What
was missing was the Lab side: a SUITE could not be handed out as an assignment.

The collection tests matter most. The traces now come from a machine Lab does
not control, so they are treated as untrusted input.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_contracts import content_hash, validate_artifact
from lab_runner.loop import ScriptedProgram, ToolCall, Finish, run_loop_trial
from lab_server.runtime_jobs import RuntimeJobStore, plan_experiment
from lab_suite import (
    DispatchError,
    assign_suite,
    build_assignment,
    builtin_registry,
    collect_suite_run,
)

CREATED = "2026-08-03T00:00:00+00:00"
ENVIRONMENT = {"model": {"provider": "byo", "id": "wrapped-agent"}}


def _store_with_runtime() -> tuple[RuntimeJobStore, str]:
    store = RuntimeJobStore()
    connected = store.connect_runtime(model="gpt-4o", agent_ref="acme/support-bot")
    return store, str(connected["runtime_ref"])


class _WrappedRuntime:
    """Stands in for a user's agent wrapped by axor-wrap.

    Runs each assigned trial LOCALLY — exactly what the real connector does —
    and pushes finished traces back. Lab never calls into it.
    """

    def __init__(self, store: RuntimeJobStore, runtime_ref: str, *, skip: set[str] | None = None):
        self.store = store
        self.runtime_ref = runtime_ref
        self.skip = skip or set()

    def work(self) -> int:
        done = 0
        for job in self.store.list_jobs(self.runtime_ref):
            claimed = self.store.claim(str(job["job_id"]), self.runtime_ref)
            assignment: dict[str, object] = claimed["assignment"]  # type: ignore[assignment]
            scenarios = {str(s["name"]): s for s in assignment["scenarios"]}  # type: ignore[union-attr,index]
            manifests = {str(m["id"]): m for m in assignment["tool_manifests"]}  # type: ignore[union-attr,index]
            for unit in claimed["planned_trials"]:  # type: ignore[union-attr]
                if str(unit) in self.skip:
                    continue
                scenario_id, condition_id, index = str(unit).rsplit(":", 2)
                trace, metrics = self._run(
                    scenarios[scenario_id], manifests, condition_id, int(index))
                self.store.complete_trial(
                    str(job["job_id"]), str(unit), self.runtime_ref, trace,
                    metrics=metrics,
                )
                done += 1
        return done

    def _run(self, scenario, manifests, condition_id, index):  # noqa: ANN001, ANN202
        condition = {"schema_version": "condition/v1", "id": condition_id,
                     "enforcement": "off"}
        outcome = run_loop_trial(
            scenario, manifests, condition, None,
            "runtime", f"s{index:03d}", index,
            ScriptedProgram([ToolCall("read_txns", {}), Finish("done")]),
        )
        trace = dict(outcome.trace)
        trace["trial"] = {**trace["trial"], "scenario_id": str(scenario["name"]),
                          "condition_id": condition_id, "repeat_index": index}
        # metrics travel BESIDE the trace, not inside it
        return trace, outcome.metrics


class TestAssignment(unittest.TestCase):
    def test_a_suite_becomes_a_runtime_assignment(self) -> None:
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get("budget")
        assignment = assign_suite(suite.manifest(), runtime_ref, store)
        self.assertTrue(assignment.run_id)
        self.assertEqual(len(assignment.planned), 5)
        self.assertEqual(store.run_state(assignment.run_id), "waiting_for_runtime")

    def test_the_assignment_carries_resolved_bodies_not_references(self) -> None:
        """The runtime has no access to Lab's registries, and freezing the
        bodies here is what stops a later registry edit from changing what a
        finished run claims to have executed."""
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get("budget")
        built = build_assignment(suite.manifest(), runtime_ref)
        self.assertTrue(built.assignment["scenarios"])
        self.assertTrue(built.assignment["tool_manifests"])
        self.assertNotIn("scenario_refs", built.assignment)

    def test_the_plan_matches_the_runtime_jobs_vocabulary(self) -> None:
        """A runtime already speaking runtime-jobs needs no new vocabulary."""
        suite = builtin_registry().get("agentdojo")
        built = build_assignment(suite.manifest(), "rt_x")
        self.assertEqual(
            list(built.planned), plan_experiment(built.assignment)["trials"],
        )

    def test_a_governed_suite_plans_both_arms_distinctly(self) -> None:
        """Guards the bug this path had: condition/v1 names the arm `id`, and
        reading only `condition_id` collapsed every arm to "None" — so a 2-arm
        plan produced two PAIRS of identical trial ids and the runtime's second
        arm silently overwrote the first."""
        suite = builtin_registry().get("agentdojo")
        built = build_assignment(suite.manifest(), "rt_x")
        self.assertEqual(len(set(built.planned)), len(built.planned))
        arms = {unit.rsplit(":", 2)[1] for unit in built.planned}
        self.assertEqual(arms, {"ungoverned", "governed"})
        self.assertNotIn("None", arms)


class TestCollection(unittest.TestCase):
    def _dispatch_and_run(self, suite_id: str = "budget", skip=None):
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get(suite_id)
        assignment = assign_suite(suite.manifest(), runtime_ref, store)
        runtime = _WrappedRuntime(store, runtime_ref, skip=skip)
        runtime.work()
        return store, assignment

    def test_the_full_round_trip_produces_an_artifact(self) -> None:
        store, assignment = self._dispatch_and_run()
        run = collect_suite_run(assignment, store)
        self.assertEqual(len(run.trials), 5)
        self.assertEqual({str(t["status"]) for t in run.trials}, {"completed"})
        artifact = run.artifact("art_rt", CREATED, ENVIRONMENT)
        self.assertEqual(validate_artifact(artifact, "artifact"), [])

    def test_lab_recomputes_the_aggregates(self) -> None:
        """A runtime-supplied aggregate is never adopted: a result nobody can
        recheck is not a result. The store's own aggregates field is ignored."""
        store, assignment = self._dispatch_and_run()
        store.attach_aggregates(assignment.run_id, [
            {"metric": "task_success", "condition_id": "observe", "estimate": 1.0,
             "interval": {"method": "wilson", "low": 1.0, "high": 1.0},
             "n": 999, "unit_of_analysis": "trial"},
        ])
        run = collect_suite_run(assignment, store)
        for aggregate in run.aggregates:
            self.assertNotEqual(int(aggregate["n"]), 999)

    def test_a_missing_trial_is_recorded_not_dropped(self) -> None:
        """Dropping it would shrink the denominator and flatter the result."""
        store, assignment = self._dispatch_and_run(
            skip={"budget-spend-summary-01:observe:2"})
        run = collect_suite_run(assignment, store)
        self.assertEqual(len(run.trials), 5)
        statuses = [str(t["status"]) for t in run.trials]
        self.assertEqual(statuses.count("failed"), 1)
        failed = next(t for t in run.trials if t["status"] == "failed")
        self.assertIn("no trace", str(failed["failure_reason"]))

    def test_a_trace_outside_the_plan_is_refused(self) -> None:
        """The runtime is untrusted input: it may only return trials Lab
        assigned. Absorbing an extra one would let a runtime inject units the
        experiment never planned."""
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get("budget")
        assignment = assign_suite(suite.manifest(), runtime_ref, store)
        runtime = _WrappedRuntime(store, runtime_ref)
        runtime.work()
        rogue = dict(next(iter(store.results(assignment.run_id)["traces"])))  # type: ignore[arg-type]
        rogue["trial"] = {**rogue["trial"], "scenario_id": "not-in-the-suite"}
        store.complete_trial(assignment.run_id, "rogue", runtime_ref, rogue)
        with self.assertRaises(DispatchError) as ctx:
            collect_suite_run(assignment, store)
        self.assertIn("not in the plan", str(ctx.exception))

    def test_an_invalid_trace_is_refused(self) -> None:
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get("budget")
        assignment = assign_suite(suite.manifest(), runtime_ref, store)
        broken = {"schema_version": "trace/v1", "trace_id": "t",
                  "trial": {"run_id": "r", "scenario_id": "budget-spend-summary-01",
                            "condition_id": "observe", "seed": "s000",
                            "repeat_index": 0}}  # no producer, no events
        store.complete_trial(assignment.run_id, "budget-spend-summary-01:observe:0",
                             runtime_ref, broken)
        with self.assertRaises(DispatchError) as ctx:
            collect_suite_run(assignment, store)
        self.assertIn("invalid trace", str(ctx.exception))

    def test_runtime_reported_metrics_are_kept_and_unmeasured_ones_are_not(self) -> None:
        """Lab did not execute the trial so it cannot time it; what it can do is
        refuse to invent numbers."""
        store, assignment = self._dispatch_and_run()
        run = collect_suite_run(assignment, store)
        metrics: dict[str, object] = run.trials[0]["metrics"]  # type: ignore[assignment]
        self.assertIn("duration_ms", metrics)
        self.assertNotIn("cost_usd", metrics)

    def test_the_suites_invariants_are_evaluated_by_lab(self) -> None:
        store, assignment = self._dispatch_and_run()
        run = collect_suite_run(assignment, store)
        by_id = {
            str(regression["id"]): result
            for regression, result in zip(assignment.resolved.regressions, run.invariants)
        }
        self.assertEqual(by_id["RG-budget-latency"].status, "passed")
        self.assertEqual(by_id["RG-budget-cost"].status, "error")

    def test_trace_refs_are_content_hashes_lab_computed(self) -> None:
        store, assignment = self._dispatch_and_run()
        run = collect_suite_run(assignment, store)
        for trial in run.trials:
            if trial.get("status") != "completed":
                continue
            ref = str(trial["trace_ref"])
            self.assertEqual(ref, content_hash(run.traces[ref]))


class TestLabNeverExecutes(unittest.TestCase):
    def test_dispatch_does_not_run_anything(self) -> None:
        """assign_suite hands out work; it must not execute a single trial.
        That is the whole architecture boundary in one assertion."""
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get("budget")
        assignment = assign_suite(suite.manifest(), runtime_ref, store)
        results = store.results(assignment.run_id)
        self.assertEqual(results["traces"], [])
        run = collect_suite_run(assignment, store)
        self.assertEqual(
            {str(t["status"]) for t in run.trials}, {"failed"},
            "nothing ran, so every planned trial is missing — not quietly absent",
        )


if __name__ == "__main__":
    unittest.main()
