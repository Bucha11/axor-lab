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

        # 4) the runtime streams the trial's kernel events, then completes it with a trace
        status, ev = self._req(
            "POST", f"/runtime/jobs/{run_id}/trials/t0/events",
            {"events": [{"seq": 0, "type": "tool_call_intent"}]}, token=ingest_key)
        self.assertEqual(status, 200, ev)
        status, done = self._req(
            "POST", f"/runtime/jobs/{run_id}/trials/t0/complete",
            {"trace": {"schema_version": "trace/v1", "trial": {"trial_id": "t0"}}},
            token=ingest_key)
        self.assertEqual(status, 200, done)
        # the plan had exactly one trial → the run is now completed
        self.assertEqual(done["run_state"], "completed")

        # 5) Lab reads the collected results
        status, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        self.assertEqual(status, 200)
        self.assertEqual(results["state"], "completed")
        self.assertEqual(len(results["traces"]), 1)
        self.assertEqual(results["trials"][0]["status"], "completed")

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

    def test_results_carry_aggregates_and_events_sse(self) -> None:
        conn = self._connect()
        _, run = self._req("POST", "/runs", {
            "runtime_ref": conn["runtime_ref"], "experiment": {"id": "e"},
            "planned_trials": ["t0"],
        }, token="ctl")
        run_id = run["run_id"]
        self._req("POST", f"/runtime/jobs/{run_id}/claim", {}, token=conn["ingest_key"])
        self._req("POST", f"/runtime/jobs/{run_id}/trials/t0/complete",
                  {"trace": {"schema_version": "trace/v1", "trial": {"trial_id": "t0"}}},
                  token=conn["ingest_key"])
        # attach runner-computed aggregates (Lab renders, does not compute them)
        aggs = [{"metric": "ASR", "condition": "gov", "successes": 1, "n": 4}]
        status, out = self._req("POST", f"/runs/{run_id}/aggregates",
                                {"aggregates": aggs}, token="ctl")
        self.assertEqual(status, 200, out)
        _, results = self._req("GET", f"/runs/{run_id}/results", token="ctl")
        self.assertEqual(results["aggregates"], aggs)
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
        trace = {"schema_version": "trace/v1", "trial": {"trial_id": "t0"}}
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
        out = store.complete_trial(run["run_id"], "t0", conn["runtime_ref"], {"schema_version": "trace/v1"})
        self.assertEqual(out["run_state"], "analyzing")

    def test_trial_attempt_supersede_idempotency(self) -> None:
        # re-completing a trial with the SAME trace is idempotent; a DIFFERENT
        # trace supersedes the prior attempt (a retry), bumping attempt/superseded.
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], {"id": "e"}, planned=["t0"])
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        first = store.complete_trial(rid, "t0", ref, {"trace": 1})
        self.assertEqual((first["attempt"], first["superseded"]), (1, 0))
        # identical re-delivery → idempotent, no supersede
        dup = store.complete_trial(rid, "t0", ref, {"trace": 1})
        self.assertTrue(dup["idempotent"])
        self.assertEqual((dup["attempt"], dup["superseded"]), (1, 0))
        # a different trace → supersede
        redo = store.complete_trial(rid, "t0", ref, {"trace": 2})
        self.assertEqual((redo["attempt"], redo["superseded"]), (2, 1))
        results = store.results(rid)
        self.assertEqual(results["trials"][0]["attempt"], 2)
        self.assertEqual(results["trials"][0]["superseded"], 1)
        # only the latest trace is retained
        self.assertEqual(results["traces"], [{"trace": 2}])

    def test_streaming_events_after_complete_starts_new_attempt(self) -> None:
        store = RuntimeJobStore()
        conn = store.connect_runtime(model="x")
        run = store.create_run(conn["runtime_ref"], {"id": "e"}, planned=["t0"])
        rid, ref = run["run_id"], conn["runtime_ref"]
        store.claim(rid, ref)
        store.complete_trial(rid, "t0", ref, {"trace": 1})
        # a runtime re-runs the unit: streaming events resets it to a new attempt
        out = store.append_events(rid, "t0", ref, [{"seq": 0}])
        self.assertEqual(out["attempt"], 2)
        self.assertEqual(store.results(rid)["trials"][0]["status"], "pending")

    def test_plan_experiment_is_deterministic(self) -> None:
        exp = {"scenario_ids": ["a"], "conditions": ["gov", "ungov"], "repeats": 2}
        self.assertEqual(plan_experiment(exp), plan_experiment(exp))
        self.assertEqual(plan_experiment(exp)["estimate"]["trials"], 4)


if __name__ == "__main__":
    unittest.main()
