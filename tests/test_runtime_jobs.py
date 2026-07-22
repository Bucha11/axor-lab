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

from lab_server import RuntimeJobStore, make_runtime_server


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


if __name__ == "__main__":
    unittest.main()
