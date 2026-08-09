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

import copy
import unittest

from tests import support
from lab_contracts import content_hash, validate_artifact
from lab_runner.loop import ScriptedProgram, ToolCall, Finish, run_loop_trial
from lab_capabilities.governance import gate_for_condition
from lab_server.runtime_jobs import RuntimeJobStore, plan_experiment
from lab_suite import (
    DispatchError,
    assign_suite,
    build_assignment,
    builtin_registry,
    collect_suite_run,
    run_suite,
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
                arm = next((c for c in assignment.get("conditions", [])  # type: ignore[union-attr]
                            if str(c["id"]) == condition_id), None)
                trace, metrics = self._run(
                    scenarios[scenario_id], manifests, condition_id, int(index), arm)
                self.store.complete_trial(
                    str(job["job_id"]), str(unit), self.runtime_ref, trace,
                    metrics=metrics,
                )
                done += 1
        return done

    def _run(self, scenario, manifests, condition_id, index, condition=None):  # noqa: ANN001, ANN202
        # a wrapped runtime gates through the kernel even when enforcement is
        # OFF — that is what "ungoverned, not unwrapped" means, and it is why
        # the trace carries verdicts and a value ledger at all
        condition = condition or {"schema_version": "condition/v1", "id": condition_id,
                                  "enforcement": "off"}
        gate = gate_for_condition(
            condition, manifests, scenario.get("inputs", {}), support.kernel_registry(),
        )
        outcome = run_loop_trial(
            scenario, manifests, condition, gate,
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
            {"metric": "task_success", "condition_id": "ungoverned", "estimate": 1.0,
             "interval": {"method": "wilson", "low": 1.0, "high": 1.0},
             "n": 999, "unit_of_analysis": "trial"},
        ])
        run = collect_suite_run(assignment, store)
        for aggregate in run.aggregates:
            self.assertNotEqual(int(aggregate["n"]), 999)

    def test_a_missing_trial_is_recorded_not_dropped(self) -> None:
        """Dropping it would shrink the denominator and flatter the result."""
        store, assignment = self._dispatch_and_run(
            skip={"budget-spend-summary-01:ungoverned:2"})
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
        # run ONE real trial so a valid trace exists to clone, then claim keeps
        # the run active for the rogue ingest
        runtime = _WrappedRuntime(store, runtime_ref)
        for job in store.list_jobs(runtime_ref):
            claimed = store.claim(str(job["job_id"]), runtime_ref)
            scenarios = {str(s["name"]): s for s in claimed["assignment"]["scenarios"]}
            manifests = {str(m["id"]): m for m in claimed["assignment"]["tool_manifests"]}
            unit = str(claimed["planned_trials"][0])
            sid, cid, idx = unit.rsplit(":", 2)
            trace, _ = runtime._run(scenarios[sid], manifests, cid, int(idx))
            store.complete_trial(str(job["job_id"]), unit, runtime_ref, trace)
        rogue = dict(trace)
        rogue["trial"] = {**rogue["trial"], "scenario_id": "not-in-the-suite"}
        # the store now refuses an unplanned unit at ingest — the same rule
        # collect enforces, moved to the moment of arrival so poison never lands
        from lab_server.runtime_jobs import RuntimeJobsError

        with self.assertRaises(RuntimeJobsError) as ctx:
            store.complete_trial(assignment.run_id, "rogue", runtime_ref, rogue)
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("not in the plan", str(ctx.exception))

    def test_an_invalid_trace_is_refused(self) -> None:
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get("budget")
        assignment = assign_suite(suite.manifest(), runtime_ref, store)
        unit = "budget-spend-summary-01:ungoverned:0"
        store.claim(assignment.run_id, runtime_ref)  # a run only accepts trials while active
        broken = {"schema_version": "trace/v1", "trace_id": "t",
                  "trial": {"run_id": "r", "scenario_id": "budget-spend-summary-01",
                            "condition_id": "ungoverned", "seed": "s000",
                            "repeat_index": 0}}  # no producer, no events
        # a completed trial must carry a schema-valid trace; the store now
        # refuses the invalid one at ingest rather than letting it reach
        # `completed` and only failing later at collect
        from lab_server.runtime_jobs import RuntimeJobsError

        with self.assertRaises(RuntimeJobsError) as ctx:
            store.complete_trial(assignment.run_id, unit, runtime_ref, broken)
        self.assertEqual(ctx.exception.status, 422)

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
        # the suite's own metric, evaluated by LAB over the trials the runtime
        # reported — a suite-defined metric has to survive the wire, not just
        # the in-process path
        self.assertEqual(by_id["RG-budget-reads"].status, "passed")

    def test_a_suite_defined_metric_survives_the_wire(self) -> None:
        """`reads` is Budget's own metric, computed by its `metrics_for` hook
        from the trace.

        The hook ran only on the in-process path. Over the wire Lab merged the
        runtime's measurements with its own predicate evaluation and never asked
        the suite for anything — so a metric that existed locally silently
        vanished the moment the same suite ran on a real agent, and any
        regression over it went from `passed` to `error`. A metric derived from
        the trace does not depend on who executed the trial."""
        store, assignment = self._dispatch_and_run()
        run = collect_suite_run(assignment, store)
        completed = [t for t in run.trials if t["status"] == "completed"]
        self.assertTrue(completed)
        for trial in completed:
            self.assertIn("reads", trial["metrics"])  # type: ignore[operator]

    def test_lab_evaluation_still_wins_over_a_suites_own_number(self) -> None:
        """Precedence has to hold: a suite hook must not be able to overwrite
        Lab's verdict about success or breach, which is the claim the artifact
        publishes and the one thing that must be recomputable from the trace."""
        from lab_suite.dispatch import _collected_metrics
        from lab_suite.sdk import BaseSuite

        class _Liar(BaseSuite):
            id = "budget"

            def metrics_for(self, outcome, scenario):  # type: ignore[no-untyped-def]
                return {"task_success": True, "reads": 99}

        store, assignment = self._dispatch_and_run()
        run = collect_suite_run(assignment, store)
        trace = next(iter(run.traces.values()))
        scenario = assignment.resolved.scenarios[0]
        metrics = _collected_metrics(
            _Liar(), scenario, trace, {"metrics": {"duration_ms": 1.0}},
        )
        self.assertEqual(metrics["reads"], 99)          # its own metric: kept
        self.assertEqual(metrics["duration_ms"], 1.0)   # the runtime's: kept
        # Lab's evaluation of the scenario predicate: not overridable
        self.assertEqual(
            metrics["task_success"],
            _evaluated_task_success(scenario, trace),
        )

    def test_trace_refs_are_content_hashes_lab_computed(self) -> None:
        store, assignment = self._dispatch_and_run()
        run = collect_suite_run(assignment, store)
        for trial in run.trials:
            if trial.get("status") != "completed":
                continue
            ref = str(trial["trace_ref"])
            self.assertEqual(ref, content_hash(run.traces[ref]))


class TestCollectionParity(unittest.TestCase):
    """The dispatched path must produce what the in-process path does — a suite
    that behaves differently by execution mode is two platforms.

    These push REAL suite traces (produced by `run_suite` with the actual
    built-in) back through the store, the way the refund-desk story's mock
    runtime does — the fixed `_WrappedRuntime` runs a budget-shaped scripted
    program that never triggers agentdojo's attack.
    """

    def _dispatch_execute(self, manifest, suite_id, *, fail_unit=None):
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get(suite_id)
        assignment = assign_suite(manifest, runtime_ref, store)
        local = run_suite(manifest, run_id=assignment.run_id, suite=suite)
        by_unit = {
            f"{t['scenario_id']}:{t['condition_id']}:{t['repeat_index']}":
                (local.traces[str(t["trace_ref"])], t["metrics"])
            for t in local.trials if t.get("status") == "completed"
        }
        for job in store.list_jobs(runtime_ref):
            claimed = store.claim(str(job["job_id"]), runtime_ref)
            for unit in claimed["planned_trials"]:
                trace, metrics = by_unit[str(unit)]
                store.complete_trial(
                    str(job["job_id"]), str(unit), runtime_ref, trace,
                    status="failed" if str(unit) == fail_unit else "completed",
                    metrics=dict(metrics),
                )
        return store, assignment

    def test_evidence_cases_are_built_on_the_dispatched_path(self) -> None:
        """`evidence_for` ran only in-process; over the wire the breach cases a
        suite raises silently vanished — the metrics_for parity bug, left open
        for evidence."""
        manifest = builtin_registry().get("agentdojo").manifest()
        store, assignment = self._dispatch_execute(manifest, "agentdojo")
        run = collect_suite_run(assignment, store)
        breaches = [c for c in run.evidence_cases if c.get("kind") == "prompt_injection"]
        self.assertTrue(breaches, "no evidence cases collected from a run with breaches")

    def test_a_failed_trial_with_a_trace_stays_failed_and_is_excluded(self) -> None:
        """A runtime can complete a trial `failed` while still returning a trace.
        Admitting it as completed because a trace exists lets it into the
        aggregate denominator, flattering the result."""
        manifest = builtin_registry().get("budget").manifest()
        fail_unit = "budget-spend-summary-01:ungoverned:0"
        store, assignment = self._dispatch_execute(manifest, "budget", fail_unit=fail_unit)
        run = collect_suite_run(assignment, store)
        failed = [t for t in run.trials if t["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        unit = (f"{failed[0]['scenario_id']}:{failed[0]['condition_id']}:"
                f"{failed[0]['repeat_index']}")
        self.assertEqual(unit, fail_unit)
        self.assertIsNotNone(failed[0].get("trace_ref"))  # trace WAS returned
        for aggregate in run.aggregates:
            self.assertLessEqual(int(aggregate["n"]), 4)

    def test_a_declared_mcnemar_test_is_computed_over_the_arms(self) -> None:
        """agentdojo declares `test: mcnemar` on ASR; the suite pipeline never
        computed it — the flagship governed-vs-ungoverned comparison had no test
        on the path the Builder and dispatch use."""
        import copy

        manifest = copy.deepcopy(builtin_registry().get("agentdojo").manifest())
        manifest["execution"]["repeats"] = 30
        store, assignment = self._dispatch_execute(manifest, "agentdojo")
        run = collect_suite_run(assignment, store)
        governed_asr = next(
            a for a in run.aggregates
            if a["metric"] == "ASR" and a["condition_id"] == "governed"
        )
        self.assertIn("test", governed_asr)
        self.assertEqual(governed_asr["test"]["name"], "mcnemar")
        self.assertEqual(governed_asr["test"]["vs"], "ungoverned")


class TestMultiAgentDispatchIsRefused(unittest.TestCase):
    def test_a_multi_agent_suite_cannot_be_dispatched(self) -> None:
        """A topology no runtime can execute must not be handed out — the
        runtime would run a single agent and report it as the topology."""
        import copy

        manifest = copy.deepcopy(builtin_registry().get("budget").manifest())
        manifest["agents"] = [{"ref": "planner", "role": "planner"},
                              {"ref": "worker", "role": "worker"}]
        manifest["topology"] = {"kind": "planner_workers"}
        with self.assertRaises(DispatchError) as ctx:
            build_assignment(manifest, "rt_x")
        self.assertIn("planner_workers", str(ctx.exception))


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


class TestUngovernedStillGoesThroughTheCore(unittest.TestCase):
    """A wrapped runtime runs under the kernel whether or not gates enforce.

    condition/v1 says it outright: "off = observe-only (proxy records, enforces
    nothing). Observation is always on regardless." An ungoverned arm that
    bypassed the core would mean the agent was never wrapped — and then turning
    governance on later is a re-integration rather than flipping `enforcement`,
    and an ungoverned/governed comparison contrasts two different machines
    instead of one machine under two policies.
    """

    def test_a_conditionless_suite_is_dispatched_ungoverned_not_unwrapped(self) -> None:
        built = build_assignment(builtin_registry().get("budget").manifest(), "rt_x")
        self.assertEqual(len(built.conditions), 1)
        arm = built.conditions[0]
        self.assertEqual(str(arm["id"]), "ungoverned")
        self.assertEqual(str(arm["enforcement"]), "off")
        self.assertIn("kernel", arm, "an ungoverned arm still runs under the kernel")

    def test_the_arm_reaches_the_runtime_so_it_knows_what_to_load(self) -> None:
        """The runtime cannot wrap under a kernel the assignment never names."""
        built = build_assignment(builtin_registry().get("budget").manifest(), "rt_x")
        self.assertIn("conditions", built.assignment)
        self.assertEqual(
            [str(c["id"]) for c in built.assignment["conditions"]],  # type: ignore[union-attr]
            ["ungoverned"],
        )

    def test_switching_governance_on_changes_only_enforcement(self) -> None:
        """The payoff. Going from ungoverned to governed must not change the
        kernel, the tools or the scenarios — only the enforcement flag."""
        registry = builtin_registry()
        ungoverned = build_assignment(registry.get("budget").manifest(), "rt_x")
        governed_manifest = copy.deepcopy(registry.get("budget").manifest())
        governed_manifest["capabilities"] = ["governance"]
        arm = dict(ungoverned.conditions[0])
        arm.update({"id": "governed", "label": "governed", "enforcement": "on"})
        governed_manifest["execution"]["conditions"] = [arm]  # type: ignore[index]
        governed = build_assignment(governed_manifest, "rt_x")

        before, after = ungoverned.conditions[0], governed.conditions[0]
        self.assertEqual(before.get("kernel"), after.get("kernel"))
        self.assertNotEqual(before["enforcement"], after["enforcement"])
        self.assertEqual(
            ungoverned.assignment["tool_manifests"], governed.assignment["tool_manifests"],
        )
        self.assertEqual(ungoverned.assignment["scenarios"], governed.assignment["scenarios"])

    def test_the_local_path_defaults_the_same_way(self) -> None:
        """One default everywhere. The local path used to synthesize a
        kernel-free arm, which closed no user story and forfeited the ledger,
        the verdicts and exact replay — locally the reference kernel is stdlib
        and always resolvable, so observing costs nothing."""
        from lab_suite import run_suite
        suite = builtin_registry().get("budget")
        run = run_suite(suite.manifest(), run_id="r_local", suite=suite)
        self.assertEqual(str(run.conditions[0]["id"]), "ungoverned")
        self.assertEqual(str(run.conditions[0]["enforcement"]), "off")
        self.assertIn("kernel", run.conditions[0])

    def test_an_unwrapped_trace_is_refused_for_a_kernel_bearing_arm(self) -> None:
        """The enforcement behind the rule. If a runtime returns a trace naming
        no kernel for an arm that declares one, the agent ran unwrapped — and an
        unobserved run must not be reported as one governance merely permitted."""
        store, runtime_ref = _store_with_runtime()
        suite = builtin_registry().get("budget")
        assignment = assign_suite(suite.manifest(), runtime_ref, store)
        self.assertIn("kernel", assignment.conditions[0])

        scenario = assignment.resolved.scenarios[0]
        unwrapped = run_loop_trial(
            scenario, assignment.resolved.manifests,
            {"schema_version": "condition/v1", "id": "ungoverned", "enforcement": "off"},
            None, "runtime", "s000", 0,
            ScriptedProgram([ToolCall("read_txns", {}), Finish("done")]),
        ).trace
        unwrapped["trial"] = {**unwrapped["trial"],
                              "scenario_id": str(scenario["name"]),
                              "condition_id": "ungoverned", "repeat_index": 0}
        store.claim(assignment.run_id, runtime_ref)  # a run only accepts trials while active
        # the trace is schema-VALID (so it passes ingest) but names no kernel —
        # the unwrapped-agent rule is a collect-time integrity check, not schema
        store.complete_trial(assignment.run_id,
                             f"{scenario['name']}:ungoverned:0", runtime_ref, unwrapped)
        with self.assertRaises(DispatchError) as ctx:
            collect_suite_run(assignment, store)
        self.assertIn("ran unwrapped", str(ctx.exception))


def _evaluated_task_success(scenario, trace):
    from lab_suite.dispatch import _evaluated

    return _evaluated(scenario, trace)["task_success"]
