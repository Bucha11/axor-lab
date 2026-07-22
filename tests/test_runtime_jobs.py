"""Runtime-jobs API — the connected-runtime execution contract (spec v0.3).

Lab assigns, the runtime executes: a runtime connects (gets an ingest_key), Lab
assigns an experiment as a job, the runtime claims it, streams a trial's kernel
events, and completes the trial by uploading its trace. Lab never executes the
agent — it only hands out assignments and collects the pushed traces.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_server import RuntimeJobStore, make_runtime_server, plan_experiment

_TRACE_CACHE: list[dict] = []


def conformant_trace() -> dict:
    """A real, schema- and semantics-conformant trace/v1 (Lab now rejects any
    non-conformant object on trial complete). Produced once by the runner."""
    if not _TRACE_CACHE:
        from tests import support
        from lab_runner import run_experiment_suite
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=1, run_id="rt_fixture",
        )
        _TRACE_CACHE.extend(result.traces.values())
    return json.loads(json.dumps(_TRACE_CACHE[0]))  # a fresh deep copy per call


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
        """Fetch a non-JSON response (e.g. SSE) — returns (status, content_type, text)."""
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, headers=headers, method=method)
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("Content-Type"), r.read().decode()


class TestRuntimeJobsFlow(_Base):
    def test_connect_assign_claim_stream_complete(self) -> None:
        # 1) a runtime connects and gets a scoped ingest_key
        status, conn = self._req("POST", "/runtimes/connect", {"model": "scripted"}, token="ctl")
        self.assertEqual(status, 201, conn)
        runtime_ref, ingest_key = conn["runtime_ref"], conn["ingest_key"]
        self.assertTrue(runtime_ref and ingest_key)

        # 2) Lab assigns an experiment to that runtime — one planned trial
        experiment = {"id": "exp1", "scenario_ids": ["banking-exfil-01"]}
        status, run = self._req("POST", "/runs", {
            "runtime_ref": runtime_ref, "experiment": experiment, "planned_trials": ["t0"],
        }, token="ctl")
        self.assertEqual(status, 201, run)
        run_id = run["run_id"]
        self.assertEqual(run["state"], "waiting_for_runtime")

        # 3) the runtime polls, sees the job, and claims it (with its ingest_key)
        status, listing = self._req("GET", "/runtime/jobs", token=ingest_key)
        self.assertEqual(status, 200)
        self.assertEqual([j["job_id"] for j in listing["jobs"]], [run_id])
        status, claim = self._req("POST", f"/runtime/jobs/{run_id}/claim", {}, token=ingest_key)
        self.assertEqual(status, 200, claim)
        self.assertEqual(claim["assignment"]["id"], "exp1")

        # 4) the runtime streams the trial's kernel events, then completes it with a
        # CONFORMANT trace/v1 (a non-conformant object is rejected — see below)
        status, ev = self._req(
            "POST", f"/runtime/jobs/{run_id}/trials/t0/events",
            {"events": [{"seq": 0, "type": "tool_call_intent"}]}, token=ingest_key)
        self.assertEqual(status, 200, ev)
        status, done = self._req(
            "POST", f"/runtime/jobs/{run_id}/trials/t0/complete",
            {"trace": conformant_trace()}, token=ingest_key)
        self.assertEqual(status, 200, done)
        self.assertTrue(done["trace_ref"])  # the attempt is frozen with its trace_ref
        # the plan had exactly one trial → the run is now completed
        self.assertEqual(done["run_state"], "completed")

        # 5) Lab reads the collected results — trace history, NO uploaded aggregates
        status, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        self.assertEqual(status, 200)
        self.assertEqual(results["state"], "completed")
        self.assertEqual(len(results["traces"]), 1)
        self.assertEqual(results["trials"][0]["status"], "completed")
        self.assertNotIn("aggregates", results)  # Lab computes aggregates, not the runtime

    def test_completing_with_a_nonconformant_trace_is_refused(self) -> None:
        _, conn = self._req("POST", "/runtimes/connect", {}, token="ctl")
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": {"id": "e"},
            "planned_trials": ["t0"],
        }, token="ctl")
        rid, key = run["run_id"], conn["ingest_key"]
        self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)
        # a runtime's summary object is NOT a trace — completion is refused (422)
        status, out = self._req("POST", f"/runtime/jobs/{rid}/trials/t0/complete",
                                {"trace": {"schema_version": "trace/v1", "trial": {}}}, token=key)
        self.assertEqual(status, 422, out)
        # and the run did NOT advance to completed on the strength of a fake trace
        _, results = self._req("GET", f"/runs/{rid}/results", token="ctl")
        self.assertNotEqual(results["state"], "completed")

    def test_unplanned_trial_is_rejected(self) -> None:
        _, conn = self._req("POST", "/runtimes/connect", {}, token="ctl")
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": {"id": "e"},
            "planned_trials": ["t0"],
        }, token="ctl")
        rid, key = run["run_id"], conn["ingest_key"]
        self._req("POST", f"/runtime/jobs/{rid}/claim", {}, token=key)
        # a trial id outside the plan cannot be driven (fail-closed)
        status, _ = self._req("POST", f"/runtime/jobs/{rid}/trials/rogue/events",
                              {"events": []}, token=key)
        self.assertEqual(status, 404)

    def test_runtime_endpoints_require_a_valid_ingest_key(self) -> None:
        self.assertEqual(self._req("GET", "/runtime/jobs")[0], 401)  # no key
        self.assertEqual(self._req("GET", "/runtime/jobs", token="bogus")[0], 401)

    def test_control_surface_requires_the_control_token(self) -> None:
        self.assertEqual(self._req("POST", "/runtimes/connect", {}, token=None)[0], 401)
        self.assertEqual(self._req("GET", "/runtimes", token="wrong")[0], 401)

    def test_a_runtime_cannot_claim_another_runtimes_job(self) -> None:
        _, a = self._req("POST", "/runtimes/connect", {"model": "a"}, token="ctl")
        _, b = self._req("POST", "/runtimes/connect", {"model": "b"}, token="ctl")
        _, run = self._req("POST", "/runs", {
            "runtime_ref": a["runtime_ref"], "experiment": {"id": "e"}, "planned_trials": ["t0"],
        }, token="ctl")
        # runtime B does not see A's job, and cannot claim it
        _, listing = self._req("GET", "/runtime/jobs", token=b["ingest_key"])
        self.assertEqual(listing["jobs"], [])
        status, _ = self._req("POST", f"/runtime/jobs/{run['run_id']}/claim", {},
                              token=b["ingest_key"])
        self.assertEqual(status, 403)

    def test_double_claim_is_rejected(self) -> None:
        _, c = self._req("POST", "/runtimes/connect", {}, token="ctl")
        _, run = self._req("POST", "/runs", {
            "runtime_ref": c["runtime_ref"], "experiment": {"id": "e"}, "planned_trials": ["t0"],
        }, token="ctl")
        self.assertEqual(self._req("POST", f"/runtime/jobs/{run['run_id']}/claim", {},
                                   token=c["ingest_key"])[0], 200)
        self.assertEqual(self._req("POST", f"/runtime/jobs/{run['run_id']}/claim", {},
                                   token=c["ingest_key"])[0], 409)

    def test_malformed_body_is_a_clean_400(self) -> None:
        _, c = self._req("POST", "/runtimes/connect", {}, token="ctl")
        # a non-object experiment is a 400, never a 500
        status, _ = self._req("POST", "/runs",
                              {"runtime_ref": c["runtime_ref"], "experiment": "nope"}, token="ctl")
        self.assertEqual(status, 400)


class TestUiFacingSurface(_Base):
    def _connect(self):
        _, conn = self._req("POST", "/runtimes/connect", {"model": "scripted"}, token="ctl")
        return conn

    def test_experiments_plan_expands_trials_and_estimate(self) -> None:
        status, plan = self._req("POST", "/experiments/plan", {"experiment": {
            "scenario_ids": ["s1", "s2"],
            "conditions": [{"condition_id": "gov"}, {"condition_id": "ungov"}],
            "repeats": 3,
        }}, token="ctl")
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan["estimate"]["trials"], 12)
        self.assertEqual(len(plan["trials"]), 12)
        self.assertIn("s1:gov:0", plan["trials"])
        # requires the control token
        self.assertEqual(self._req("POST", "/experiments/plan", {"experiment": {}})[0], 401)

    def test_scenarios_validate_reports_errors(self) -> None:
        # a well-formed scenario validates ok; a broken one returns ok:false + errors
        from tests import support
        good = support.banking_scenario()
        manifests = support.manifests()
        status, out = self._req("POST", "/scenarios/validate",
                                {"scenario": good, "manifests": manifests}, token="ctl")
        self.assertEqual(status, 200, out)
        self.assertTrue(out["ok"], out)

        broken = support.banking_scenario()
        broken["violation"]["tool"] = "nonexistent_tool"  # not a declared tool_id
        _, bad = self._req("POST", "/scenarios/validate",
                           {"scenario": broken, "manifests": manifests}, token="ctl")
        self.assertFalse(bad["ok"])
        self.assertTrue(bad["errors"])

    def test_awaiting_confirmation_gate_then_confirm(self) -> None:
        conn = self._connect()
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": {"id": "e"},
            "planned_trials": ["t0"], "require_confirmation": True,
            "estimate": {"trials": 1},
        }, token="ctl")
        self.assertEqual(run["state"], "awaiting_confirmation")
        self.assertEqual(run["estimate"], {"trials": 1})
        run_id = run["run_id"]
        # an unconfirmed run is NOT offered to the runtime yet
        _, listing = self._req("GET", "/runtime/jobs", token=conn["ingest_key"])
        self.assertEqual(listing["jobs"], [])
        # claiming before confirmation is refused (not claimable)
        self.assertEqual(self._req("POST", f"/runtime/jobs/{run_id}/claim", {},
                                   token=conn["ingest_key"])[0], 409)
        # confirm → becomes claimable
        status, conf = self._req("POST", f"/runs/{run_id}/confirm", {}, token="ctl")
        self.assertEqual(status, 200, conf)
        self.assertEqual(conf["state"], "waiting_for_runtime")
        _, listing2 = self._req("GET", "/runtime/jobs", token=conn["ingest_key"])
        self.assertEqual([j["job_id"] for j in listing2["jobs"]], [run_id])

    def test_results_have_no_uploaded_aggregates_and_stream_sse(self) -> None:
        conn = self._connect()
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": {"id": "e"},
            "planned_trials": ["t0"],
        }, token="ctl")
        run_id = run["run_id"]
        self._req("POST", f"/runtime/jobs/{run_id}/claim", {}, token=conn["ingest_key"])
        self._req("POST", f"/runtime/jobs/{run_id}/trials/t0/complete",
                  {"trace": conformant_trace()}, token=conn["ingest_key"])
        # there is NO endpoint to upload a result aggregate — Lab computes them from
        # traces at bundle/publish time; results carry trace history, not numbers
        self.assertEqual(self._req("POST", f"/runs/{run_id}/aggregates",
                                   {"aggregates": []}, token="ctl")[0], 404)
        _, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        self.assertNotIn("aggregates", results)
        self.assertEqual(len(results["traces"]), 1)
        # the SSE events endpoint streams state + trial progress frames
        status, ctype, text = self._raw("GET", f"/runs/{run_id}/events", token="ctl")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/event-stream"))
        self.assertIn("event: state", text)
        self.assertIn("event: trials", text)
        self.assertIn(run_id, text)

    def test_trial_trace_fetch(self) -> None:
        conn = self._connect()
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": {"id": "e"},
            "planned_trials": ["t0"],
        }, token="ctl")
        run_id = run["run_id"]
        self._req("POST", f"/runtime/jobs/{run_id}/claim", {}, token=conn["ingest_key"])
        trace = conformant_trace()
        self._req("POST", f"/runtime/jobs/{run_id}/trials/t0/complete",
                  {"trace": trace}, token=conn["ingest_key"])
        status, got = self._req("GET", f"/runs/{run_id}/trials/t0/trace", token="ctl")
        self.assertEqual(status, 200, got)
        self.assertEqual(got, trace)
        # a trace for an unknown trial is a clean 404
        self.assertEqual(self._req("GET", f"/runs/{run_id}/trials/nope/trace", token="ctl")[0], 404)


class TestStoreDirect(unittest.TestCase):
    def test_unplanned_job_goes_to_analyzing_on_trial_complete(self) -> None:
        # with no planned-trial set, a completed trial moves the run to analyzing
        # (the runtime signals overall completion out of band in this simple form)
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], {"id": "e"})
        store.claim(run["run_id"], conn["runtime_ref"])
        out = store.complete_trial(run["run_id"], "t0", conn["runtime_ref"], conformant_trace())
        self.assertEqual(out["run_state"], "analyzing")

    def test_finished_attempt_is_immutable_retry_supersedes_keeping_history(self) -> None:
        # a completed attempt is IMMUTABLE: re-completing or streaming into it is
        # refused. A retry opens a NEW attempt that supersedes the prior one, and
        # the prior attempt stays in the audit history (not destroyed).
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], {"id": "e"}, planned=["t0"])
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        t1, t2 = conformant_trace(), conformant_trace()
        t2["values"] = list(t2.get("values", []))  # a distinct object identity
        first = store.complete_trial(rid, "t0", ref, t1)
        first_att = first["attempt"]
        # re-completing the finished attempt is refused (immutable)
        with self.assertRaises(Exception) as ctx:
            store.complete_trial(rid, "t0", ref, t2)
        self.assertEqual(getattr(ctx.exception, "status", None), 409)
        # streaming into the finished attempt is likewise refused
        with self.assertRaises(Exception):
            store.append_events(rid, "t0", ref, [{"seq": 0}])
        # an explicit retry opens a superseding attempt; run returns to running
        redo = store.retry_trial(rid, "t0", ref)
        self.assertEqual(redo["supersedes"], first_att)
        self.assertEqual(redo["run_state"], "running")
        store.complete_trial(rid, "t0", ref, t2)
        results = store.results(rid)
        attempts = results["trials"][0]["attempts"]
        self.assertEqual(len(attempts), 2)                 # BOTH attempts retained
        self.assertEqual(attempts[0]["attempt_id"], first_att)
        self.assertEqual(attempts[1]["supersedes"], first_att)
        self.assertEqual(len(results["traces"]), 1)        # the active accepted trace

    def test_event_batches_are_idempotent(self) -> None:
        # a re-delivered batch (same batch_id) does not duplicate the ledger
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], {"id": "e"}, planned=["t0"])
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        a = store.append_events(rid, "t0", ref, [{"seq": 0}, {"seq": 1}], batch_id="b1")
        self.assertEqual(a["events"], 2)
        b = store.append_events(rid, "t0", ref, [{"seq": 0}, {"seq": 1}], batch_id="b1")
        self.assertTrue(b["idempotent"])
        self.assertEqual(b["events"], 2)  # not 4 — the retry was a no-op

    def test_terminal_run_rejects_further_ingest(self) -> None:
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], {"id": "e"}, planned=["t0"])
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        store.complete_trial(rid, "t0", ref, conformant_trace())
        self.assertEqual(store.results(rid)["state"], "completed")  # terminal
        with self.assertRaises(Exception) as ctx:
            store.append_events(rid, "t0", ref, [{"seq": 0}])
        self.assertEqual(getattr(ctx.exception, "status", None), 409)

    def test_failed_trial_requires_typed_failure(self) -> None:
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], {"id": "e"}, planned=["t0"])
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        with self.assertRaises(Exception):  # no failure details
            store.complete_trial(rid, "t0", ref, None, status="failed")
        out = store.complete_trial(rid, "t0", ref, None, status="failed",
                                   failure={"kind": "timeout", "detail": "no response"})
        self.assertEqual(out["status"], "failed")
        self.assertEqual(store.results(rid)["state"], "failed")

    def test_trace_unit_binding_is_enforced(self) -> None:
        # when the plan names a TrialUnit coordinate, the uploaded trace's trial
        # block must match it — a trace for a different unit is rejected
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        trace = conformant_trace()
        unit = trace["trial"]
        run = store.create_run(conn["runtime_ref"], {"id": "e"},
                               planned=[{"trial_id": "u0", "trial": unit}])
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        wrong = conformant_trace()
        wrong["trial"] = {**unit, "seed": "s999"}  # a different unit
        with self.assertRaises(Exception) as ctx:
            store.complete_trial(rid, "u0", ref, wrong)
        self.assertEqual(getattr(ctx.exception, "status", None), 422)
        # the matching trace is accepted
        out = store.complete_trial(rid, "u0", ref, conformant_trace())
        self.assertEqual(out["status"], "completed")

    def test_plan_experiment_is_deterministic(self) -> None:
        exp = {"scenario_ids": ["a"], "conditions": ["gov", "ungov"], "repeats": 2}
        self.assertEqual(plan_experiment(exp), plan_experiment(exp))
        self.assertEqual(plan_experiment(exp)["estimate"]["trials"], 4)

    def test_plan_experiment_fails_closed_on_bad_matrix(self) -> None:
        # no convenience-defaulting: a malformed experiment is rejected, not turned
        # into a plausible plan of fictional units
        for bad in ({"conditions": ["gov"]},               # no scenarios
                    {"scenario_ids": ["a"]},               # no conditions
                    {"scenario_ids": ["a"], "conditions": ["gov"], "repeats": 0}):
            with self.assertRaises(Exception) as ctx:
                plan_experiment(bad)
            self.assertEqual(getattr(ctx.exception, "status", None), 400)


if __name__ == "__main__":
    unittest.main()
