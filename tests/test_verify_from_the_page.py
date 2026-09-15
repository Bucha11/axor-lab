"""A reader with a link — and nothing else — can check the evidence.

The product's claim is that a published result is checkable by someone who does
not trust the publisher. The publication page's answer to that was
`axor-lab verify <package>.json`, which costs the reader a Python install
before they can begin, so in practice a published number was read by many and
checked by nobody.

These tests hold two things:

  * **the verification is real.** `verify_package_document` used to APPEND
    "content hashes: OK" without computing any: the CLI loader hashed the
    package before calling it, so correctness depended on call order, and the
    hosted face — which hands it a request body — reported a clean pass over a
    tampered bundle. A check that reports what it did not test is worse than no
    check, because it is the reader's whole reason to believe the page.
  * **it is reachable without a token, an account, or an install**, from the
    page and from the API, for this publication and for a file the reader
    brought with them.
"""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lab_server import make_server
from lab_service import Outcome, verify_package_document
from lab_service.packages import PackageMalformed, normalise_traces
from tests.test_server_e2e import _bundle_with_xss_question


class TestTheHashCheckIsReal(unittest.TestCase):
    """Straight at the function both faces share, with no loader in front."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle, cls.traces, _ = _bundle_with_xss_question("r_verify")

    def _verify(self, bundle: dict[str, object], traces: dict[str, dict[str, object]]):
        return verify_package_document(bundle, traces, None, from_directory=True)

    def test_an_untouched_package_passes(self) -> None:
        self.assertEqual(self._verify(self.bundle, self.traces).outcome, Outcome.OK)

    def test_a_tampered_trace_is_caught(self) -> None:
        traces = copy.deepcopy(self.traces)
        tid = sorted(traces)[0]
        # schema-VALID: the producer block stays well-formed, one string differs.
        # A schema check alone would wave this through; only the hash catches it.
        traces[tid]["producer"]["runtime"] = "axor-wrap@9.9.9"
        result = self._verify(self.bundle, traces)
        self.assertEqual(result.outcome, Outcome.FAILURE)
        self.assertIn("content hash mismatch", result.checks[-1].message)

    def test_a_tampered_headline_number_is_caught(self) -> None:
        """The number a paper would quote is the one worth altering."""
        bundle = copy.deepcopy(self.bundle)
        bundle["aggregates"][0]["estimate"] = 0.0
        result = self._verify(bundle, self.traces)
        self.assertEqual(result.outcome, Outcome.FAILURE)
        self.assertIn("aggregates", result.checks[-1].message)

    def test_the_hash_check_says_it_recomputed(self) -> None:
        # the wording is the claim: "OK" read as "someone checked this"
        (first, *_) = self._verify(self.bundle, self.traces).checks
        self.assertEqual(first.name, "content hashes")
        self.assertIn("recomputed", first.message)

    def test_junk_is_a_verdict_not_a_crash(self) -> None:
        # a public route hands this whatever a stranger posted
        for junk in ({}, {"conditions": []}, {"bundle": 1, "aggregates": None}):
            with self.subTest(junk=junk):
                self.assertEqual(self._verify(junk, {}).outcome, Outcome.VALIDATION)


class TestEitherPackageShape(unittest.TestCase):
    """The download emits a LIST; everything that verifies wanted a map."""

    def test_a_list_becomes_a_map(self) -> None:
        traces = [{"trace_id": "t1"}, {"trace_id": "t2"}]
        self.assertEqual(sorted(normalise_traces(traces)), ["t1", "t2"])

    def test_a_map_is_left_alone(self) -> None:
        self.assertEqual(sorted(normalise_traces({"t1": {"trace_id": "t1"}})), ["t1"])

    def test_a_duplicate_trace_id_is_refused(self) -> None:
        with self.assertRaises(PackageMalformed):
            normalise_traces([{"trace_id": "t1"}, {"trace_id": "t1"}])

    def test_a_non_trace_is_refused(self) -> None:
        for bad in ("nope", [1], [{"no_id": 1}]):
            with self.subTest(bad=bad):
                with self.assertRaises(PackageMalformed):
                    normalise_traces(bad)


class TestAReaderWithOnlyALink(unittest.TestCase):
    """Over real HTTP, with no Authorization header anywhere."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        # a WRITE TOKEN is set: publishing needs it, verifying must not
        cls.server = make_server(
            Path(cls.tmp.name) / "store", host="127.0.0.1", port=0,
            write_token="secret-write-token",
        )
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        bundle, traces, _ = _bundle_with_xss_question("r_page")
        status, body = cls._post(
            "/api/publications",
            # the LIST shape, exactly as the download route emits it
            {"bundle": bundle, "traces": list(traces.values()),
             "question": "Does governance stop it?", "visibility": "public"},
            token="secret-write-token",
        )
        assert status == 201, (status, body)
        cls.pid = str(body["publication_id"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.tmp.cleanup()

    @classmethod
    def _post(cls, path: str, payload: object, token: str | None = None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            cls.base + path, data=json.dumps(payload).encode(),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _get(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(self.base + path) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    # -- publish took the download's own shape ---------------------------

    def test_publishing_accepted_the_list_shape(self) -> None:
        """Set up by setUpClass: a package taken off one server must be
        publishable to another, and the two halves disagreed about the shape."""
        self.assertTrue(self.pid.startswith("e_"))

    # -- the page ---------------------------------------------------------

    def test_the_page_offers_verification_before_it_offers_an_install(self) -> None:
        status, html = self._get(f"/e/{self.pid}")
        self.assertEqual(status, 200)
        self.assertIn(f"/e/{self.pid}/verify", html)
        # and it is above the claims, not below the trial table
        self.assertLess(html.index("<h2>Verify</h2>"), html.index("Exactly replayable"))
        # the honest limit travels with the offer
        self.assertIn("cannot prove itself honest", html)

    def test_the_verify_page_renders_every_check(self) -> None:
        status, html = self._get(f"/e/{self.pid}/verify")
        self.assertEqual(status, 200)
        for name in ("content hashes", "replay", "receipt", "publication"):
            with self.subTest(check=name):
                self.assertIn(name, html)
        self.assertIn("bit-identical", html)

    def test_the_verify_page_needs_no_token(self) -> None:
        # the GET above carried no Authorization header at all, and the server
        # was built WITH a write token — so this is the reviewer's real position
        status, _ = self._get(f"/e/{self.pid}/verify")
        self.assertEqual(status, 200)

    def test_a_private_publication_is_not_verifiable_either(self) -> None:
        status, _ = self._get("/e/e_does_not_exist/verify")
        self.assertEqual(status, 404)

    # -- the API ----------------------------------------------------------

    def test_verifying_this_publication_over_the_api(self) -> None:
        status, report = self._post(f"/api/publications/{self.pid}/verify", {})
        self.assertEqual(status, 200)
        self.assertIn(report["outcome"], ("ok", "unverified"))
        self.assertTrue(report["bit_identical"])
        # the caveat is in the payload, not only in the page
        self.assertIn("this server", report["verified_by"])

    def test_the_downloaded_file_verifies_verbatim(self) -> None:
        """What a reader actually has is the file the download button produced."""
        status, package = self._get(f"/api/publications/{self.pid}/bundle")
        self.assertEqual(status, 200)
        status, report = self._post("/api/verify", json.loads(package))
        self.assertEqual(status, 200)
        self.assertIn(report["outcome"], ("ok", "unverified"))

    def test_an_uploaded_tampered_package_fails(self) -> None:
        _, package = self._get(f"/api/publications/{self.pid}/bundle")
        doctored = json.loads(package)
        doctored["bundle"]["aggregates"][0]["estimate"] = 0.0
        status, report = self._post("/api/verify", doctored)
        self.assertEqual(status, 200)
        self.assertEqual(report["outcome"], "failure")

    def test_a_stripped_proof_fails_rather_than_downgrading(self) -> None:
        _, package = self._get(f"/api/publications/{self.pid}/bundle")
        stripped = json.loads(package)
        del stripped["receipt"]
        status, report = self._post("/api/verify", stripped)
        self.assertEqual(status, 200)
        self.assertEqual(report["outcome"], "failure")

    def test_junk_is_a_400_never_a_500(self) -> None:
        """This route takes anything a stranger posts."""
        for junk in ({}, {"bundle": "x"}, {"bundle": {}, "traces": "nope"},
                     {"bundle": {}, "traces": [1]}, {"package": 7}):
            with self.subTest(junk=junk):
                status, _ = self._post("/api/verify", junk)
                self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
