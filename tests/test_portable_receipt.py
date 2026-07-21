"""Portable verification receipt (review r14).

A downloaded reproduction package must be verifiable OFFLINE, without trusting
the server that served it: the receipt pins the signed_ref (content of the
bytes a signature covers) and, when signed, the author/key_id/signature. The
`axor-lab verify` command runs that check standalone, and the server hands back
an acceptance receipt on publish.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, content_hash
from lab_contracts.signing import (
    SignatureInvalid,
    build_receipt,
    signed_ref,
    verify_receipt,
)
from lab_runner import run_experiment_suite
from lab_runner.bundle_io import read_bundle_package
from lab_server import make_server

_HAS_NACL = importlib.util.find_spec("nacl") is not None
CREATED = "2026-07-20T12:00:00+00:00"


def _publishable_bundle() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_receipt",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_receipt", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestReceiptPrimitives(unittest.TestCase):
    def test_hash_receipt_pins_the_bundle_and_verifies(self) -> None:
        bundle, _ = _publishable_bundle()
        receipt = build_receipt(bundle, integrity="hash_verified")
        self.assertEqual(receipt["signed_ref"], signed_ref(bundle))
        self.assertIsNone(receipt["signature"])
        verify_receipt(bundle, receipt)  # must NOT raise

    def test_tampered_bundle_fails_the_receipt(self) -> None:
        bundle, _ = _publishable_bundle()
        receipt = build_receipt(bundle, integrity="hash_verified")
        tampered = {**bundle, "created": "1999-01-01T00:00:00+00:00"}
        with self.assertRaises(SignatureInvalid):
            verify_receipt(tampered, receipt)

    def test_signed_ref_excludes_the_signature_field(self) -> None:
        bundle, _ = _publishable_bundle()
        ref = signed_ref(bundle)
        self.assertEqual(ref, signed_ref({**bundle, "signature": "deadbeef"}))
        # and it differs from the whole-bundle hash once a signature is present
        self.assertNotEqual(ref, content_hash({**bundle, "signature": "deadbeef"}))


class TestServerServesAndAccepts(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.tmp.name) / "store", host="127.0.0.1", port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.bundle, cls.traces = _publishable_bundle()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return json.loads(r.read())

    def test_publish_returns_an_acceptance_receipt(self) -> None:
        status, body = self._post("/api/publications", {
            "bundle": self.bundle, "traces": self.traces,
            "question": "does governance stop the exfil?", "visibility": "unlisted",
        })
        self.assertEqual(status, 201, body)
        acc = body["acceptance"]
        self.assertEqual(acc["publication_id"], body["publication_id"])
        self.assertEqual(acc["schema_version"], "axor-lab-acceptance/v1")
        self.assertIn("content_hashes", acc["semantic_report"]["verified"])
        self.assertIn("replay_bit_identical", acc["semantic_report"]["verified"])
        self.pid = body["publication_id"]

    def test_download_carries_a_portable_receipt_that_verifies(self) -> None:
        _, body = self._post("/api/publications", {
            "bundle": self.bundle, "traces": self.traces,
            "question": "q2", "visibility": "unlisted",
        })
        pid = body["publication_id"]
        pkg = self._get(f"/api/publications/{pid}/bundle")
        self.assertIn("receipt", pkg)
        receipt = pkg["receipt"]
        self.assertEqual(receipt["integrity"], "hash_verified")
        # the downloaded receipt verifies against the downloaded bundle, offline
        verify_receipt(pkg["bundle"], receipt)


class TestCliVerify(unittest.TestCase):
    def test_verify_a_downloaded_package_passes(self) -> None:
        from lab_runner.cli import main

        bundle, traces = _publishable_bundle()
        receipt = build_receipt(bundle, integrity="hash_verified")
        with tempfile.TemporaryDirectory() as tmp:
            pkg_path = Path(tmp) / "download.json"
            pkg_path.write_text(json.dumps({
                "bundle": bundle, "traces": list(traces.values()), "receipt": receipt,
            }))
            self.assertEqual(main(["verify", str(pkg_path)]), 0)

    def test_verify_detects_a_tampered_receipt(self) -> None:
        from lab_runner.cli import main

        bundle, traces = _publishable_bundle()
        bad = build_receipt(bundle, integrity="hash_verified")
        bad["signed_ref"] = "0" * 64  # does not match the bundle
        with tempfile.TemporaryDirectory() as tmp:
            pkg_path = Path(tmp) / "download.json"
            pkg_path.write_text(json.dumps({
                "bundle": bundle, "traces": list(traces.values()), "receipt": bad,
            }))
            self.assertEqual(main(["verify", str(pkg_path)]), 1)

    def test_malformed_package_is_a_clean_error_not_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "corrupt.json"
            bad.write_text("{ this is not json ]")
            with self.assertRaises(Exception) as ctx:  # noqa: B017 — assert it's RunnerError-typed
                read_bundle_package(bad)
            from lab_runner.errors import RunnerError
            self.assertIsInstance(ctx.exception, RunnerError)

    def test_traces_not_a_list_is_a_clean_error(self) -> None:
        from lab_runner.errors import RunnerError

        bundle, _ = _publishable_bundle()
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"bundle": bundle, "traces": {"not": "a list"}}))
            with self.assertRaises(RunnerError):
                read_bundle_package(bad)


@unittest.skipUnless(_HAS_NACL, "PyNaCl not installed")
class TestSignedReceipt(unittest.TestCase):
    def test_signed_receipt_verifies_with_the_author_key(self) -> None:
        from nacl.signing import SigningKey

        from lab_contracts.signing import sign_bundle

        bundle, _ = _publishable_bundle()
        sk = SigningKey.generate()
        priv_hex = bytes(sk).hex()
        pub_hex = bytes(sk.verify_key).hex()
        sig = sign_bundle(bundle, priv_hex)
        receipt = build_receipt(bundle, integrity="signed", author="acme",
                                key_id="acme", signature=sig)
        verify_receipt(bundle, receipt, pub_hex)  # must NOT raise
        with self.assertRaises(SignatureInvalid):
            verify_receipt(bundle, receipt, "11" * 32)  # wrong key


if __name__ == "__main__":
    unittest.main()
