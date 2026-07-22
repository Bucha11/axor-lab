"""Runtime-jobs API — the connected-runtime execution contract (spec v0.3).

Lab assigns, the runtime executes: a runtime connects (gets an ingest_key), Lab
assigns a SERVER-OWNED plan as a job, the runtime claims it, streams a trial's
kernel events, and completes each trial by uploading a CONFORMANT trace bound to
its assigned TrialUnit. Lab never executes the agent, never trusts a runtime's
summary — it collects traces and BUILDS Results (aggregates) itself.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_server import (
    InMemoryRuntimeRegistry,
    LabTraceStore,
    RuntimeJobStore,
    make_runtime_server,
    plan_experiment,
)

_TRACE_CACHE: list[dict] = []
# a minimal server-plannable experiment: one scenario × one condition × one repeat
EXP = {"scenario_ids": ["s1"], "conditions": ["c1"], "repeats": 1}


def _fixture_trace() -> dict:
    if not _TRACE_CACHE:
        from tests import support
        from lab_runner import run_experiment_suite
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=1, run_id="rt_fixture",
        )
        _TRACE_CACHE.extend(result.traces.values())
    return json.loads(json.dumps(_TRACE_CACHE[0]))


def trace_for(coordinate: dict) -> dict:
    """A real, schema- and semantics-conformant trace/v1 whose `trial` block is the
    assigned unit coordinate (a runtime stamps the assignment it claimed)."""
    t = _fixture_trace()
    t["trial"] = {k: v for k, v in coordinate.items() if k != "trial_id"}
    return t


def unit(run_id: str, scenario: str = "s1", condition: str = "c1", i: int = 0) -> dict:
    """The coordinate the server deterministically assigns for EXP's trial."""
    return {"run_id": run_id, "scenario_id": scenario, "condition_id": condition,
            "seed": f"s{i:03d}", "repeat_index": i}


class _Base(unittest.TestCase):
    control_token: str | None = "ctl"

    def setUp(self) -> None:
        self.server: ThreadingHTTPServer = make_runtime_server(
            host="127.0.0.1", port=0, control_token=self.control_token,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _req(self, method: str, path: str, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _raw(self, method: str, path: str, token=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, headers=headers, method=method)
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("Content-Type"), r.read().decode()

    def _run(self, experiment=None):
        """connect a runtime + create a run; returns (conn, run, trial_id)."""
        _, conn = self._req("POST", "/runtimes/connect", {"model": "scripted"}, token="ctl")
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": experiment or EXP,
        }, token="ctl")
        return conn, run, run["planned_trials"][0]


class TestRuntimeJobsFlow(_Base):
    def test_connect_assign_claim_stream_complete(self) -> None:
        _, conn = self._req("POST", "/runtimes/connect", {"model": "scripted"}, token="ctl")
        runtime_ref, ingest_key = conn["runtime_ref"], conn["ingest_key"]

        # Lab assigns a SERVER-OWNED plan (the client sends no trial ids)
        status, run = self._req("POST", "/runs", {
            "runtime_ref": runtime_ref, "experiment": EXP,
        }, token="ctl")
        self.assertEqual(status, 201, run)
        run_id, tid = run["run_id"], run["planned_trials"][0]
        self.assertEqual(run["state"], "waiting_for_runtime")

        # the runtime polls, sees the job, claims it, and receives the assigned units
        status, listing = self._req("GET", "/runtime/jobs", token=ingest_key)
        self.assertEqual([j["job_id"] for j in listing["jobs"]], [run_id])
        status, claim = self._req("POST", f"/runtime/jobs/{run_id}/claim", {}, token=ingest_key)
        self.assertEqual(status, 200, claim)
        coord = claim["units"][0]
        self.assertEqual(coord["run_id"], run_id)  # the run stamped its own run_id

        # streams events, then completes the trial with a trace bound to the unit
        self._req("POST", f"/runtime/jobs/{run_id}/trials/{tid}/events",
                  {"events": [{"seq": 0}]}, token=ingest_key)
        status, done = self._req(
            "POST", f"/runtime/jobs/{run_id}/trials/{tid}/complete",
            {"trace": trace_for(coord)}, token=ingest_key)
        self.assertEqual(status, 200, done)
        self.assertTrue(done["trace_ref"])
        self.assertEqual(done["run_state"], "completed")

        # Lab reads Results — trace history + LAB-COMPUTED aggregates (empty here:
        # EXP carries no scenario defs, so ASR is not computed — but the key exists
        # and the numbers are never runtime-supplied)
        status, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        self.assertEqual(results["state"], "completed")
        self.assertEqual(len(results["traces"]), 1)
        self.assertEqual(results["trials"][0]["status"], "completed")
        self.assertIn("aggregates", results)
        self.assertEqual(self._req("POST", f"/runs/{run_id}/aggregates",
                                   {"aggregates": []}, token="ctl")[0], 404)  # no upload path

    def test_completing_with_a_nonconformant_trace_is_refused(self) -> None:
        conn, run, tid = self._run()
        rid, key = run["run_id"], conn["ingest_key"]
        self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)
        status, out = self._req("POST", f"/runtime/jobs/{rid}/trials/{tid}/complete",
                                {"trace": {"schema_version": "trace/v1", "trial": {}}}, token=key)
        self.assertEqual(status, 422, out)
        _, results = self._req("GET", f"/runs/{rid}/results", token="ctl")
        self.assertNotEqual(results["state"], "completed")

    def test_trace_for_the_wrong_unit_is_refused_on_the_main_flow(self) -> None:
        # the ordinary browser path (server plan → run → complete) binds too: a
        # schema-valid trace for a DIFFERENT coordinate is rejected (review v0.3-bind)
        conn, run, tid = self._run()
        rid, key = run["run_id"], conn["ingest_key"]
        _, claim = self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)
        wrong = trace_for({**claim["units"][0], "seed": "s999"})
        status, _ = self._req("POST", f"/runtime/jobs/{rid}/trials/{tid}/complete",
                              {"trace": wrong}, token=key)
        self.assertEqual(status, 422)

    def test_unplanned_trial_is_rejected(self) -> None:
        conn, run, _ = self._run()
        rid, key = run["run_id"], conn["ingest_key"]
        self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)
        status, _ = self._req("POST", f"/runtime/jobs/{rid}/trials/rogue/events",
                              {"events": []}, token=key)
        self.assertEqual(status, 404)

    def test_runtime_endpoints_require_a_valid_ingest_key(self) -> None:
        self.assertEqual(self._req("GET", "/runtime/jobs")[0], 401)
        self.assertEqual(self._req("GET", "/runtime/jobs", token="bogus")[0], 401)

    def test_control_surface_requires_the_control_token(self) -> None:
        self.assertEqual(self._req("POST", "/runtimes/connect", {}, token=None)[0], 401)
        self.assertEqual(self._req("GET", "/runtimes", token="wrong")[0], 401)

    def test_a_runtime_cannot_claim_another_runtimes_job(self) -> None:
        _, a = self._req("POST", "/runtimes/connect", {"model": "a"}, token="ctl")
        _, b = self._req("POST", "/runtimes/connect", {"model": "b"}, token="ctl")
        _, run = self._req("POST", "/runs", {
            "runtime_ref": a["runtime_ref"], "experiment": EXP,
        }, token="ctl")
        _, listing = self._req("GET", "/runtime/jobs", token=b["ingest_key"])
        self.assertEqual(listing["jobs"], [])
        status, _ = self._req("POST", f"/runtime/jobs/{run['run_id']}/claim", {},
                              token=b["ingest_key"])
        self.assertEqual(status, 403)

    def test_double_claim_is_rejected(self) -> None:
        conn, run, _ = self._run()
        rid, key = run["run_id"], conn["ingest_key"]
        self.assertEqual(self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)[0], 200)
        self.assertEqual(self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)[0], 409)

    def test_malformed_body_is_a_clean_400(self) -> None:
        _, c = self._req("POST", "/runtimes/connect", {}, token="ctl")
        status, _ = self._req("POST", "/runs",
                              {"runtime_ref": c["runtime_ref"], "experiment": "nope"}, token="ctl")
        self.assertEqual(status, 400)

    def test_retry_is_control_only_runtime_can_only_request(self) -> None:
        # a completed trial: the runtime CANNOT reopen it (control-only retry), it
        # may only record a retry request; the control token grants the retry.
        conn, run, tid = self._run()
        rid, key = run["run_id"], conn["ingest_key"]
        _, claim = self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)
        self._req("POST", f"/runtime/jobs/{rid}/trials/{tid}/complete",
                  {"trace": trace_for(claim["units"][0])}, token=key)
        # runtime retry with its ingest key → 401 (control-only)
        self.assertEqual(self._req("POST", f"/runs/{rid}/trials/{tid}/retry", {}, token=key)[0], 401)
        # runtime may only REQUEST a retry (advisory, no state change)
        status, rr = self._req("POST", f"/runtime/jobs/{rid}/trials/{tid}/retry-request",
                               {"reason": "flaky"}, token=key)
        self.assertEqual(status, 200, rr)
        self.assertTrue(rr["retry_requested"])
        self.assertEqual(self._req("GET", f"/runs/{rid}", token="ctl")[1]["state"], "completed")
        # the control token grants the retry → a new attempt, run back to running
        status, granted = self._req("POST", f"/runs/{rid}/trials/{tid}/retry", {}, token="ctl")
        self.assertEqual(status, 200, granted)
        self.assertEqual(granted["run_state"], "running")


class TestUiFacingSurface(_Base):
    def test_experiments_plan_is_server_owned_with_a_plan_ref(self) -> None:
        status, plan = self._req("POST", "/experiments/plan", {"experiment": {
            "scenario_ids": ["s1", "s2"],
            "conditions": [{"condition_id": "gov"}, {"condition_id": "ungov"}],
            "repeats": 3,
        }}, token="ctl")
        self.assertEqual(status, 200, plan)
        self.assertTrue(plan["plan_ref"])
        self.assertEqual(plan["estimate"]["trials"], 12)
        self.assertEqual(len(plan["units"]), 12)
        self.assertIn("s1:gov:0", plan["trials"])
        # a run consumes the plan_ref (not a client trial list)
        _, conn = self._req("POST", "/runtimes/connect", {}, token="ctl")
        status, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "plan_ref": plan["plan_ref"],
        }, token="ctl")
        self.assertEqual(status, 201, run)
        self.assertEqual(len(run["planned_trials"]), 12)
        self.assertEqual(self._req("POST", "/experiments/plan", {"experiment": {}})[0], 401)

    def test_scenarios_validate_reports_errors(self) -> None:
        from tests import support
        good, manifests = support.banking_scenario(), support.manifests()
        status, out = self._req("POST", "/scenarios/validate",
                                {"scenario": good, "manifests": manifests}, token="ctl")
        self.assertEqual(status, 200, out)
        self.assertTrue(out["ok"], out)
        broken = support.banking_scenario()
        broken["violation"]["tool"] = "nonexistent_tool"
        _, bad = self._req("POST", "/scenarios/validate",
                           {"scenario": broken, "manifests": manifests}, token="ctl")
        self.assertFalse(bad["ok"])
        self.assertTrue(bad["errors"])

    def test_awaiting_confirmation_gate_then_confirm(self) -> None:
        _, conn = self._req("POST", "/runtimes/connect", {}, token="ctl")
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": EXP,
            "require_confirmation": True, "estimate": {"trials": 1},
        }, token="ctl")
        self.assertEqual(run["state"], "awaiting_confirmation")
        run_id = run["run_id"]
        _, listing = self._req("GET", "/runtime/jobs", token=conn["ingest_key"])
        self.assertEqual(listing["jobs"], [])
        self.assertEqual(self._req("POST", f"/runtime/jobs/{run_id}/claim", {},
                                   token=conn["ingest_key"])[0], 409)
        status, conf = self._req("POST", f"/runs/{run_id}/confirm", {}, token="ctl")
        self.assertEqual(conf["state"], "waiting_for_runtime")
        _, listing2 = self._req("GET", "/runtime/jobs", token=conn["ingest_key"])
        self.assertEqual([j["job_id"] for j in listing2["jobs"]], [run_id])

    def test_results_stream_sse(self) -> None:
        conn, run, tid = self._run()
        run_id, key = run["run_id"], conn["ingest_key"]
        _, claim = self._req("POST", f"/runtime/jobs/{run_id}/claim", {}, token=key)
        self._req("POST", f"/runtime/jobs/{run_id}/trials/{tid}/complete",
                  {"trace": trace_for(claim["units"][0])}, token=key)
        status, ctype, text = self._raw("GET", f"/runs/{run_id}/events", token="ctl")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/event-stream"))
        self.assertIn("event: state", text)
        self.assertIn("event: trials", text)

    def test_trial_trace_fetch(self) -> None:
        conn, run, tid = self._run()
        run_id, key = run["run_id"], conn["ingest_key"]
        _, claim = self._req("POST", f"/runtime/jobs/{run_id}/claim", {}, token=key)
        trace = trace_for(claim["units"][0])
        self._req("POST", f"/runtime/jobs/{run_id}/trials/{tid}/complete",
                  {"trace": trace}, token=key)
        status, got = self._req("GET", f"/runs/{run_id}/trials/{tid}/trace", token="ctl")
        self.assertEqual(status, 200, got)
        self.assertEqual(got, trace)
        self.assertEqual(self._req("GET", f"/runs/{run_id}/trials/nope/trace", token="ctl")[0], 404)


class TestStoreDirect(unittest.TestCase):
    def _started(self, store, experiment=None):
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], experiment or EXP)
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        return rid, ref, run["planned_trials"][0]

    def test_finished_attempt_is_immutable_retry_supersedes_keeping_history(self) -> None:
        store = RuntimeJobStore()
        rid, ref, tid = self._started(store)
        first = store.complete_trial(rid, tid, ref, trace_for(unit(rid)))
        first_att = first["attempt"]
        # a DIFFERENT result (here: a failed status) re-completing the finished
        # attempt is refused — it is a mutation, not an idempotent re-delivery
        with self.assertRaises(Exception) as ctx:
            store.complete_trial(rid, tid, ref, None, status="failed", failure={"kind": "x"})
        self.assertEqual(getattr(ctx.exception, "status", None), 409)
        # retry is CONTROL-owned (no runtime_ref) → new superseding attempt
        redo = store.retry_trial(rid, tid)
        self.assertEqual(redo["supersedes"], first_att)
        self.assertEqual(redo["run_state"], "running")
        # streaming into the fresh (running) attempt is now allowed
        store.append_events(rid, tid, ref, [{"seq": 0}])
        store.complete_trial(rid, tid, ref, trace_for(unit(rid)))
        attempts = store.results(rid)["trials"][0]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["attempt_id"], first_att)
        self.assertEqual(attempts[1]["supersedes"], first_att)

    def test_completion_is_idempotent(self) -> None:
        # a re-delivered identical completion (a lost HTTP response) returns the
        # prior success — not a 409, not a mutation (review v0.3-idempotency)
        store = RuntimeJobStore()
        rid, ref, tid = self._started(store)
        tr = trace_for(unit(rid))
        first = store.complete_trial(rid, tid, ref, tr)
        again = store.complete_trial(rid, tid, ref, tr)
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["trace_ref"], first["trace_ref"])
        self.assertEqual(len(store.results(rid)["trials"][0]["attempts"]), 1)

    def test_retry_is_control_owned(self) -> None:
        store = RuntimeJobStore()
        rid, ref, tid = self._started(store)
        store.complete_trial(rid, tid, ref, trace_for(unit(rid)))
        # a runtime can only REQUEST (advisory); it cannot reopen the run
        req = store.request_retry(rid, tid, ref, reason="flaky")
        self.assertTrue(req["retry_requested"])
        self.assertEqual(store.results(rid)["state"], "completed")
        # control grants → new attempt, run back to running
        store.retry_trial(rid, tid)
        self.assertEqual(store.results(rid)["state"], "running")

    def test_event_batches_are_idempotent(self) -> None:
        store = RuntimeJobStore()
        rid, ref, tid = self._started(store)
        a = store.append_events(rid, tid, ref, [{"seq": 0}, {"seq": 1}], batch_id="b1")
        self.assertEqual(a["events"], 2)
        b = store.append_events(rid, tid, ref, [{"seq": 0}, {"seq": 1}], batch_id="b1")
        self.assertTrue(b["idempotent"])
        self.assertEqual(b["events"], 2)

    def test_terminal_run_rejects_further_ingest(self) -> None:
        store = RuntimeJobStore()
        rid, ref, tid = self._started(store)
        store.complete_trial(rid, tid, ref, trace_for(unit(rid)))
        self.assertEqual(store.results(rid)["state"], "completed")
        with self.assertRaises(Exception) as ctx:
            store.append_events(rid, tid, ref, [{"seq": 0}])
        self.assertEqual(getattr(ctx.exception, "status", None), 409)

    def test_failed_trial_requires_typed_failure(self) -> None:
        store = RuntimeJobStore()
        rid, ref, tid = self._started(store)
        with self.assertRaises(Exception):
            store.complete_trial(rid, tid, ref, None, status="failed")
        out = store.complete_trial(rid, tid, ref, None, status="failed",
                                   failure={"kind": "timeout"})
        self.assertEqual(out["status"], "failed")
        self.assertEqual(store.results(rid)["state"], "failed")

    def test_duplicate_trial_ids_in_a_plan_are_rejected(self) -> None:
        # the plan is server-owned; a plan that (somehow) carries a duplicate unit is
        # rejected rather than letting one silently overwrite the other
        store = RuntimeJobStore()
        conn = store.connect_runtime()
        dup_plan = store._plans  # noqa: SLF001 — construct a bad plan directly
        ref_id = "plan_dup"
        dup_plan[ref_id] = [
            {"trial_id": "x", "scenario_id": "s", "condition_id": "c", "seed": "s000", "repeat_index": 0},
            {"trial_id": "x", "scenario_id": "s", "condition_id": "c", "seed": "s000", "repeat_index": 0},
        ]
        with self.assertRaises(Exception) as ctx:
            store.create_run(conn["runtime_ref"], plan_ref=ref_id)
        self.assertEqual(getattr(ctx.exception, "status", None), 400)

    def test_lab_computes_aggregates_from_traces(self) -> None:
        # the analyzing phase: Lab evaluates each scenario's violation predicate on
        # the collected traces and builds ASR aggregates ITSELF (review v0.3-results)
        from tests import support
        scen = support.banking_scenario()  # name: banking-exfil-01
        experiment = {"scenario_ids": ["banking-exfil-01"], "conditions": ["ungoverned"],
                      "repeats": 1, "scenarios": [scen]}
        store = RuntimeJobStore()
        rid, ref, tid = self._started(store, experiment)
        coord = unit(rid, scenario="banking-exfil-01", condition="ungoverned")
        store.complete_trial(rid, tid, ref, trace_for(coord))
        res = store.results(rid)
        self.assertEqual(res["state"], "completed")
        self.assertEqual(len(res["aggregates"]), 1)
        self.assertEqual(res["aggregates"][0]["condition_id"], "ungoverned")
        self.assertEqual(res["aggregates"][0]["n"], 1)
        self.assertEqual(res["analysis"]["trials_analyzed"], 1)

    def test_shared_registry_lets_one_runtime_serve_two_job_stores(self) -> None:
        registry = InMemoryRuntimeRegistry()
        conn = registry.connect(model="scripted")
        ref, key = conn["runtime_ref"], conn["ingest_key"]
        store_a, store_b = RuntimeJobStore(registry=registry), RuntimeJobStore(registry=registry)
        self.assertEqual(store_a.runtime_for_key(key), ref)
        self.assertEqual(store_b.runtime_for_key(key), ref)
        run = store_b.create_run(ref, EXP)
        self.assertEqual(run["state"], "waiting_for_runtime")
        other = RuntimeJobStore()
        with self.assertRaises(Exception) as ctx:
            other.create_run(ref, EXP)
        self.assertEqual(getattr(ctx.exception, "status", None), 404)

    def test_injected_trace_store_owns_addressing(self) -> None:
        # the domain does not know which TraceStore is wired: an injected custom
        # store receives + addresses every accepted trace (review v0.3-tracestore)
        class RecordingStore(LabTraceStore):
            def __init__(self) -> None:
                super().__init__()
                self.refs: list[str] = []

            def put(self, trace: dict) -> str:
                ref = super().put(trace)
                self.refs.append(ref)
                return ref

        ts = RecordingStore()
        store = RuntimeJobStore(trace_store=ts)
        rid, ref, tid = self._started(store)
        out = store.complete_trial(rid, tid, ref, trace_for(unit(rid)))
        self.assertEqual(ts.refs, [out["trace_ref"]])
        self.assertIsNotNone(ts.get(out["trace_ref"]))
        self.assertEqual(len(store.results(rid)["traces"]), 1)

    def test_plan_experiment_is_deterministic(self) -> None:
        exp = {"scenario_ids": ["a"], "conditions": ["gov", "ungov"], "repeats": 2}
        self.assertEqual(plan_experiment(exp), plan_experiment(exp))
        self.assertEqual(plan_experiment(exp)["estimate"]["trials"], 4)

    def test_plan_experiment_fails_closed_on_bad_matrix(self) -> None:
        for bad in ({"conditions": ["gov"]}, {"scenario_ids": ["a"]},
                    {"scenario_ids": ["a"], "conditions": ["gov"], "repeats": 0}):
            with self.assertRaises(Exception) as ctx:
                plan_experiment(bad)
            self.assertEqual(getattr(ctx.exception, "status", None), 400)


class TestTraceStore(unittest.TestCase):
    def test_labtracestore_owns_hash_and_immutability(self) -> None:
        from lab_server.providers import TraceStoreIntegrityError
        ts = LabTraceStore()
        trace = trace_for(unit("run_x"))
        ref = ts.put(trace)
        self.assertTrue(ref)
        # a returned trace is a fresh copy — mutating it does not corrupt the store
        got = ts.get(ref)
        got["trial"]["seed"] = "TAMPERED"
        self.assertNotEqual(ts.get(ref)["trial"]["seed"], "TAMPERED")
        # same bytes → idempotent (same ref); a different body cannot collide the ref
        self.assertEqual(ts.put(trace_for(unit("run_x"))), ref)
        ts._by_ref[ref] = json.dumps({"different": True})  # force a collision  # noqa: SLF001
        with self.assertRaises(TraceStoreIntegrityError):
            ts.put(trace_for(unit("run_x")))


if __name__ == "__main__":
    unittest.main()
