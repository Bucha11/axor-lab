"""Signed, persisted server acceptance receipt + publication in the package
(review r15).

The publish response and the download must carry a portable acceptance receipt —
the server's attestation of what it verified — that is persisted and, when the
server has a key, Ed25519-signed. The download package also carries the
publication body so an offline reader can verify the CLAIMS, not just the bytes.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, content_hash
from lab_runner import run_experiment_suite
from lab_server.store import PublicationStore

_HAS_NACL = importlib.util.find_spec("nacl") is not None
CREATED = "2026-07-20T12:00:00+00:00"


def _bundle_and_traces():
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_acc",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_acc", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestAcceptanceReceipt(unittest.TestCase):
    def _store(self, **kwargs) -> PublicationStore:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return PublicationStore(root=Path(self.tmp.name), **kwargs)

    def test_acceptance_content_addresses_the_semantic_report(self) -> None:
        store = self._store()
        bundle, traces = _bundle_and_traces()
        stored = store.publish(bundle, traces, question="q")
        acc = store.acceptance(stored)
        self.assertEqual(acc["schema_version"], "axor-lab-acceptance/v1")
        self.assertEqual(acc["semantic_report_ref"], content_hash(acc["semantic_report"]))
        self.assertEqual(acc["algorithm"], "unsigned")  # no server key configured

    def test_acceptance_is_persisted(self) -> None:
        store = self._store()
        bundle, traces = _bundle_and_traces()
        stored = store.publish(bundle, traces, question="q")
        pid = str(stored.publication["publication_id"])
        acc_file = Path(self.tmp.name) / pid / "acceptance.json"
        self.assertTrue(acc_file.is_file())
        self.assertEqual(json.loads(acc_file.read_text())["publication_id"], pid)

    def test_acceptance_is_deterministic(self) -> None:
        store = self._store()
        bundle, traces = _bundle_and_traces()
        stored = store.publish(bundle, traces, question="q")
        self.assertEqual(store.acceptance(stored), store.acceptance(stored))


@unittest.skipUnless(_HAS_NACL, "PyNaCl not installed")
class TestSignedAcceptance(unittest.TestCase):
    def test_signed_acceptance_verifies_with_the_server_key(self) -> None:
        from nacl.signing import SigningKey

        from lab_contracts.signing import verify_bundle_signature

        sk = SigningKey.generate()
        priv, pub = bytes(sk).hex(), bytes(sk.verify_key).hex()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = PublicationStore(
            root=Path(tmp.name), server_id="lab.example.com",
            server_key_id="lab-prod-2026", server_signing_key=priv,
        )
        bundle, traces = _bundle_and_traces()
        stored = store.publish(bundle, traces, question="q")
        acc = store.acceptance(stored)
        self.assertEqual(acc["algorithm"], "ed25519")
        self.assertEqual(acc["key_id"], "lab-prod-2026")
        # the signature verifies over the receipt minus its signature field
        verify_bundle_signature(acc, str(acc["signature"]), pub)  # must NOT raise


if __name__ == "__main__":
    unittest.main()
