"""The hosted face can hand evidence to someone.

It could not. Across the whole web app there was ONE download (a handoff file
map) and no `publish` anywhere in the client — so of the export paths the CLI
offers, the product reached the Control-Plane handoff and nothing else. An
artifact could be rendered on screen and not obtained; a publication, which is
the entire "give a reader something they can verify" story, had no hosted
surface at all and the publish server was a process the app never spoke to.

These pin the three doors that were missing, against a REAL dispatched run —
because the trace bodies a package needs live in the job store, and an endpoint
tested over a hand-built artifact would not touch that join.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import unittest
import urllib.error
import urllib.request

from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_server.screens import ScreenStore
from lab_suite import assign_suite, builtin_registry

HAS_WRAP = importlib.util.find_spec("axor_wrap") is not None
CREATED = "2026-09-13T00:00:00+00:00"
TOKEN = "ctl"


def _tools() -> dict:
    return {
        "read_txns": lambda: {"transactions": [{"description": "routine"}]},
        "send_money": lambda recipient, amount=1: {"ok": True},
    }


class _Face:
    """A running screen API with one COLLECTED run behind it."""

    def __init__(self) -> None:
        self.jobs = RuntimeJobStore()
        self.shelf = ScreenStore()
        self.server = make_runtime_server(
            port=0, control_token=TOKEN, store=self.jobs, screens=self.shelf)
        self.base = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def dispatch_and_collect(self) -> str:
        """Drive a real runtime through the protocol, then collect — which is
        what puts both the artifact and the trace bodies where an export
        endpoint has to join them."""
        from axor_wrap import LabRuntimeConnector, toolset_for_arm, trial_of

        from lab_server.runtime_jobs import _collect_and_persist

        runtime = LabRuntimeConnector(self.base, control_token=TOKEN)
        connected = runtime.connect(runtime_label="byo", agent_ref="acme/bot")
        assignment = assign_suite(
            builtin_registry().get("budget").manifest(),
            str(connected["runtime_ref"]), self.jobs,
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
            runtime.complete_trial(
                job_id, str(unit),
                toolset.trace(trial_of(str(unit), run_id=assignment.run_id),
                              scenario=scenarios[scenario_id]),
                metrics={"duration_ms": 1.0},
                runtime_config_hash=toolset.runtime_config_hash(kernel),
            )
        _collect_and_persist(self.jobs, self.shelf, assignment.run_id)
        return f"a_{assignment.run_id}"

    def call(self, method: str, path: str, body: object = None):  # noqa: ANN201
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {TOKEN}")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(HAS_WRAP, "axor-wrap not installed")
class TestTheArtifactCanBeObtained(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.face = _Face()
        cls.artifact_id = cls.face.dispatch_and_collect()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.face.close()

    def test_the_artifact_downloads_as_a_file(self) -> None:
        """Not a new document — the same `artifact/v1` the screen renders, so
        what a reader receives is what the screen showed. The difference is one
        header, and it is the difference between a screen and a deliverable."""
        status, headers, body = self.face.call("GET", f"/artifacts/{self.artifact_id}/download")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn(self.artifact_id, headers["Content-Disposition"])
        self.assertEqual(json.loads(body)["artifact_id"], self.artifact_id)

    def test_the_package_carries_the_trace_bodies(self) -> None:
        """The bundle alone cannot be replayed. The bodies live in the job
        store, so the endpoint joins them — which is the whole reason this test
        drives a real dispatch."""
        status, headers, body = self.face.call("GET", f"/artifacts/{self.artifact_id}/package")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        package = json.loads(body)
        self.assertEqual(sorted(package), ["bundle", "traces"])
        refs = {t["trace_ref"] for t in package["bundle"]["trials"]
                if t.get("trace_ref")}
        self.assertTrue(refs)
        self.assertEqual(len(package["traces"]), len(refs))

    def test_the_downloaded_package_verifies(self) -> None:
        """The point of handing it over at all. Bare `{bundle, traces}` claims
        integrity and replay and nothing more — `axor-reproduction-package/v1`
        is the server-issued shape whose proof objects are mandatory, and an
        unpublished artifact has none of them."""
        from lab_service import verify_package_document

        _, _, body = self.face.call("GET", f"/artifacts/{self.artifact_id}/package")
        package = json.loads(body)
        traces = {t["trace_id"]: t for t in package["traces"]}
        result = verify_package_document(
            package["bundle"], traces, package, allow_bare=True,
        )
        names = {c.name: c for c in result.checks}
        self.assertEqual(names["content hashes"].status.value, "ok")
        self.assertEqual(names["replay"].status.value, "ok")

    def test_an_artifact_whose_traces_this_server_lacks_is_refused(self) -> None:
        """Refused, not handed over empty: a package with no bodies cannot be
        replayed, and a reader would learn that only after downloading it."""
        orphan = {**self.face.shelf.get("artifact", self.artifact_id),
                  "artifact_id": "a_r_elsewhere"}
        self.face.shelf.put("artifact", orphan, id_field="artifact_id")
        status, _, body = self.face.call("GET", "/artifacts/a_r_elsewhere/package")
        self.assertEqual(status, 409)
        self.assertIn("does not hold", json.loads(body)["error"])


@unittest.skipUnless(HAS_WRAP, "axor-wrap not installed")
class TestTheArtifactCanBePublished(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.face = _Face()
        cls.artifact_id = cls.face.dispatch_and_collect()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.face.close()

    def test_a_local_publication_is_minted_and_listed(self) -> None:
        status, _, body = self.face.call(
            "POST", f"/artifacts/{self.artifact_id}/publish",
            {"question": "Does the budget suite stay inside its ceiling?"},
        )
        self.assertEqual(status, 201, body)
        minted = json.loads(body)
        self.assertEqual(minted["origin"], "local")
        self.assertTrue(minted["publication_id"])

        status, _, body = self.face.call("GET", "/publications")
        listed = json.loads(body)["publications"]
        self.assertIn(minted["publication_id"],
                      [p["publication_id"] for p in listed])
        status, _, body = self.face.call(
            "GET", f"/publications/{minted['publication_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["publication_id"], minted["publication_id"])

    def test_a_local_mint_does_not_claim_the_statistics(self) -> None:
        """It re-ran the verdicts, so it may assert replay. It did not
        independently recompute the aggregates, and a hand-edited bundle could
        carry a fabricated one — so the count of what is NOT claimed travels
        with the answer rather than being left for the reader to notice."""
        _, _, body = self.face.call(
            "POST", f"/artifacts/{self.artifact_id}/publish",
            {"question": "What does it answer?"},
        )
        minted = json.loads(body)
        self.assertIn("aggregates_not_claimed", minted)
        kinds = {c["kind"] for c in minted["publication"]["claims"]}
        self.assertNotIn("statistically_reproducible", kinds)

    def test_a_publication_needs_a_question(self) -> None:
        status, _, body = self.face.call(
            "POST", f"/artifacts/{self.artifact_id}/publish", {"question": "  "})
        self.assertEqual(status, 400)
        self.assertIn("question", json.loads(body)["error"])

    def test_the_export_routes_need_the_control_token(self) -> None:
        import urllib.request as request

        for method, path in (
            ("GET", f"/artifacts/{self.artifact_id}/download"),
            ("GET", f"/artifacts/{self.artifact_id}/package"),
            ("GET", "/publications"),
        ):
            with self.subTest(route=f"{method} {path}"):
                req = request.Request(self.face.base + path, method=method)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    request.urlopen(req, timeout=10)
                self.assertEqual(ctx.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
