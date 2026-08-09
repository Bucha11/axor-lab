"""`SSE /runs/{id}/events` is a live stream, not a snapshot.

It used to send two frames and close — `text/event-stream` wearing the shape of
a poll. A screen that subscribed got one picture of a run in progress and
nothing after it, so the Run screen could show progress only by being reloaded,
and Phase 4 shipped without live progress for that reason.

Three properties this pins, each of which fails silently:

  1. **No lost update.** `subscribe` delivers the current snapshot THROUGH the
     queue while holding the store's lock. Reading state first and subscribing
     second is the classic race: a run that finishes in that gap streams nothing
     and the screen waits forever on a run that is already done.
  2. **The snapshot arrives whole.** A terminal `state` frame closes the stream,
     so it is emitted AFTER `trials` — otherwise a client subscribing to a
     finished run gets the state, then `done`, and never the trial data queued
     behind it. That was a real defect, caught by an existing test.
  3. **The connection closes.** The stream carries no Content-Length and ends
     when the run does, so it must say `Connection: close`. On a keep-alive
     connection the client has no way to know the body is over and blocks on a
     response that will never grow.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_suite import builtin_registry, run_suite

CONTROL = "control-token-for-tests"


def _frames(text: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    name = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            parsed.append((name, json.loads(line[6:])))
            name = ""
    return parsed


class LiveEventsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = RuntimeJobStore()
        self.server = make_runtime_server(port=0, control_token=CONTROL, store=self.jobs)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        suite = builtin_registry().get("budget")
        local = run_suite(suite.manifest(), run_id="seed", suite=suite)
        self.trace = next(iter(local.traces.values()))
        coordinates = self.trace["trial"]
        self.unit = (
            f"{coordinates['scenario_id']}:{coordinates['condition_id']}:"
            f"{coordinates['repeat_index']}"
        )
        self.connection = self._post("/runtimes/connect", {"model": "m"})
        self.run_id = str(
            self._post("/runs", {
                "experiment": {
                    "schema_version": "experiment/v1", "id": "e", "type": "benchmark",
                    "scenario_ids": [coordinates["scenario_id"]], "repeats": 1,
                    "agent_ref": "scripted@0.6",
                },
                "runtime_ref": self.connection["runtime_ref"],
                "planned_trials": [self.unit],
            })["run_id"],
        )

    def _post(self, path: str, body: dict, token: str = CONTROL) -> dict:
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(body).encode(), method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return json.loads(response.read() or b"{}")

    def _listen(self, query: str = "") -> tuple[list[tuple[str, dict]], threading.Thread]:
        collected: list[tuple[str, dict]] = []

        def read() -> None:
            request = urllib.request.Request(f"{self.base}/runs/{self.run_id}/events{query}")
            if not query:
                request.add_header("Authorization", f"Bearer {CONTROL}")
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                collected.extend(_frames(response.read().decode()))

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        return collected, thread

    def _execute(self) -> None:
        key = self.connection["ingest_key"]
        self._post(f"/runtime/jobs/{self.run_id}/claim", {}, token=key)
        self._post(
            f"/runtime/jobs/{self.run_id}/trials/{self.unit}/complete",
            {"trace": self.trace, "status": "completed",
             "metrics": {"duration_ms": 1.5}},
            token=key,
        )


class TestTheStreamIsLive(LiveEventsTestCase):
    def test_it_pushes_transitions_as_they_happen(self) -> None:
        collected, thread = self._listen()
        time.sleep(0.4)          # subscribed, run untouched
        self._execute()
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive(), "the stream did not close")
        states = [payload["state"] for name, payload in collected if name == "state"]
        # the initial snapshot, then the claim, then the completion — a snapshot
        # endpoint would have produced only the first
        self.assertEqual(states, ["waiting_for_runtime", "running", "completed"])

    def test_it_reports_progress_against_the_plan(self) -> None:
        collected, thread = self._listen()
        time.sleep(0.4)
        self._execute()
        thread.join(timeout=15)
        progress = [payload for name, payload in collected if name == "trials"]
        self.assertEqual(progress[0]["completed"], 0)
        self.assertEqual(progress[-1]["completed"], 1)
        self.assertEqual(progress[-1]["planned"], 1)

    def test_it_ends_with_done_so_the_browser_stops_reconnecting(self) -> None:
        """`EventSource` reconnects any stream that closes. Without an explicit
        end, a finished run would be re-subscribed forever."""
        collected, thread = self._listen()
        time.sleep(0.3)
        self._execute()
        thread.join(timeout=15)
        self.assertEqual(collected[-1][0], "done")

    def test_a_terminal_state_is_marked_terminal(self) -> None:
        collected, thread = self._listen()
        time.sleep(0.3)
        self._execute()
        thread.join(timeout=15)
        final = [p for n, p in collected if n == "state"][-1]
        self.assertTrue(final["terminal"])


class TestSubscribingToAFinishedRun(LiveEventsTestCase):
    """The race the snapshot-through-the-queue design exists for: everything is
    already over by the time the screen asks."""

    def test_the_whole_snapshot_arrives_before_the_stream_closes(self) -> None:
        self._execute()
        collected, thread = self._listen()
        thread.join(timeout=15)
        names = [name for name, _ in collected]
        # `trials` BEFORE `state`: the terminal state closes the stream, so
        # emitting it first left the trial data queued and undelivered
        self.assertEqual(names, ["trials", "state", "done"])
        self.assertEqual(dict(collected)["trials"]["completed"], 1)

    def test_it_does_not_hang_waiting_for_a_run_that_is_over(self) -> None:
        self._execute()
        started = time.monotonic()
        _, thread = self._listen()
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 10)


class TestTheStreamIsGated(LiveEventsTestCase):
    def test_no_token_is_refused(self) -> None:
        request = urllib.request.Request(f"{self.base}/runs/{self.run_id}/events")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
        self.assertEqual(caught.exception.code, 401)

    def test_the_query_token_works_here_because_EventSource_cannot_set_a_header(
        self,
    ) -> None:
        """A token in a URL can land in a proxy log — a real cost, accepted for
        this ONE route because the alternative is an unauthenticated stream of
        run contents."""
        collected, thread = self._listen(query=f"?token={CONTROL}")
        time.sleep(0.3)
        self._execute()
        thread.join(timeout=15)
        self.assertTrue(collected)

    def test_a_wrong_query_token_is_refused(self) -> None:
        request = urllib.request.Request(f"{self.base}/runs/{self.run_id}/events?token=no")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
        self.assertEqual(caught.exception.code, 401)

    def test_the_query_token_is_not_accepted_on_other_routes(self) -> None:
        """The trade is scoped to the stream. Extending it to every endpoint
        would put a credential in every URL for no reason — the others have a
        header available."""
        request = urllib.request.Request(f"{self.base}/home?token={CONTROL}")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
        self.assertEqual(caught.exception.code, 401)


class TestRoutingIgnoresTheQueryString(LiveEventsTestCase):
    def test_a_decorated_path_reaches_the_same_route(self) -> None:
        """Routes were compared against the full path, so `GET /suites?x=1`
        matched nothing and fell through to a 404. A request that is merely
        decorated is not a different route."""
        request = urllib.request.Request(f"{self.base}/suites?cachebust=1")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            self.assertEqual(response.status, 200)
            self.assertIn("suites", json.loads(response.read()))


class TestRoutingDecodesThePath(LiveEventsTestCase):
    def test_a_percent_encoded_trial_link_reaches_the_trial(self) -> None:
        """The client percent-encodes every path segment, and a trial unit
        carries colons — so the app requests `/trials/name%3Aarm%3A0`. The
        routes were matched against the RAW path, which meant every trial link
        in the Run report 404'd in the browser while the same URL typed with
        literal colons worked."""
        import urllib.parse

        self._execute()
        encoded = urllib.parse.quote(self.unit, safe="")
        request = urllib.request.Request(f"{self.base}/runs/{self.run_id}/trials/{encoded}")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            payload = json.loads(response.read())
        self.assertEqual(payload["trial"]["trial_id"], self.unit)


class TestTheStoreRefusesAHostileRuntime(LiveEventsTestCase):
    """The runtime is untrusted input. These validations used to live only in
    collect_suite_run, which no HTTP route calls — so a hostile runtime
    corrupted live state and every raw-store screen, and collect merely failed
    later. The checks now run at ingest."""

    def _key(self) -> str:
        return str(self.connection["ingest_key"])

    def _complete(self, unit, body, token=None):
        return self.call_status(
            "POST", f"/runtime/jobs/{self.run_id}/trials/{unit}/complete", body,
            token=token or self._key())

    def call_status(self, method, path, body, token):
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(body).encode(), method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_completing_before_claim_is_refused(self) -> None:
        """An unclaimed run is not executing; completing a trial on it (or on an
        awaiting_confirmation run) bypassed the claim/confirm gate."""
        status = self._complete(self.unit, {"trace": self.trace, "status": "completed"})
        self.assertEqual(status, 409)

    def test_an_unplanned_trial_is_refused(self) -> None:
        self._post(f"/runtime/jobs/{self.run_id}/claim", {}, token=self._key())
        status = self._complete("ghost:cond:99", {"trace": self.trace, "status": "completed"})
        self.assertEqual(status, 409)

    def test_a_completed_trial_needs_a_valid_trace(self) -> None:
        self._post(f"/runtime/jobs/{self.run_id}/claim", {}, token=self._key())
        status = self._complete(self.unit, {"trace": {"schema_version": "trace/v1"},
                                            "status": "completed"})
        self.assertEqual(status, 422)

    def test_an_unknown_status_is_refused(self) -> None:
        self._post(f"/runtime/jobs/{self.run_id}/claim", {}, token=self._key())
        status = self._complete(self.unit, {"trace": self.trace, "status": "error"})
        self.assertEqual(status, 400)

    def test_a_runtime_cannot_report_a_lab_owned_verdict(self) -> None:
        """task_success is Lab's verdict, recomputed from the trace. A runtime
        that could set it on the raw store would grade its own homework on every
        UI surface that reads the store before a bundle is built."""
        key = self._key()
        self._post(f"/runtime/jobs/{self.run_id}/claim", {}, token=key)
        self._post(
            f"/runtime/jobs/{self.run_id}/trials/{self.unit}/complete",
            {"trace": self.trace, "status": "completed",
             "metrics": {"task_success": True, "duration_ms": 2.0}},
            token=key,
        )
        results = self._get(f"/runs/{self.run_id}/results")
        trial = next(t for t in results["trials"] if t["trial_id"] == self.unit)
        self.assertNotIn("task_success", trial["metrics"])
        self.assertIn("duration_ms", trial["metrics"])

    def test_a_stuck_run_can_be_cancelled_to_a_terminal_state(self) -> None:
        self._post(f"/runtime/jobs/{self.run_id}/claim", {}, token=self._key())
        cancelled = self._post(f"/runs/{self.run_id}/cancel", {})
        self.assertEqual(cancelled["state"], "cancelled")

    def test_finalizing_a_run_with_outstanding_trials_is_refused(self) -> None:
        """Attaching aggregates flipped a partial run straight to a green
        `completed`. A run with trials outstanding is closed by cancel, not by
        pretending its aggregates are final."""
        self._post(f"/runtime/jobs/{self.run_id}/claim", {}, token=self._key())
        status = self.call_status(
            "POST", f"/runs/{self.run_id}/aggregates", {"aggregates": []}, token=CONTROL)
        self.assertEqual(status, 409)

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(f"{self.base}{path}")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return json.loads(response.read())


class TestRunsAreAllListed(LiveEventsTestCase):
    def test_the_runs_endpoint_lists_every_run_not_just_five(self) -> None:
        """The Runs screen read home.recent_runs (capped at 5); a sixth run, or
        a stuck partial one, vanished from the only screen named Runs."""
        for _ in range(6):
            self._post("/runs", {
                "experiment": {"schema_version": "experiment/v1", "id": "e",
                               "type": "benchmark", "scenario_ids": ["s"], "repeats": 1,
                               "agent_ref": "scripted@0.6"},
                "runtime_ref": self.connection["runtime_ref"],
                "planned_trials": ["s:ungoverned:0"],
            })
        request = urllib.request.Request(f"{self.base}/runs")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            runs = json.loads(response.read())["runs"]
        self.assertGreaterEqual(len(runs), 7)  # the setUp run + 6 more


class TestManyListeners(LiveEventsTestCase):
    def test_two_screens_watching_one_run_both_see_it_finish(self) -> None:
        """One queue per subscriber, so a slow reader cannot stall the runtime
        thread that is publishing."""
        first, thread_one = self._listen()
        second, thread_two = self._listen()
        time.sleep(0.4)
        self._execute()
        thread_one.join(timeout=15)
        thread_two.join(timeout=15)
        for collected in (first, second):
            self.assertEqual(collected[-1][0], "done")


if __name__ == "__main__":
    unittest.main()
