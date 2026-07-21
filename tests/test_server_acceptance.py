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

    def test_persisted_acceptance_is_restored_on_load_not_reminted(self) -> None:
        # publish under server identity A, persisting acceptance.json...
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle_and_traces()
        store_a = PublicationStore(root=root, server_id="axor-lab-server-A")
        stored = store_a.publish(bundle, traces, question="q")
        pid = str(stored.publication["publication_id"])
        original = json.loads((root / pid / "acceptance.json").read_text())
        self.assertEqual(original["server_id"], "axor-lab-server-A")

        # ...reload with a DIFFERENT server identity B on the same dir. The served
        # acceptance must be the ORIGINAL (server_id A), not re-minted under B —
        # otherwise the historical attestation silently changes (review r16)
        store_b = PublicationStore(root=root, server_id="axor-lab-server-B")
        reloaded = store_b.get(pid)
        acc = store_b.acceptance(reloaded)
        self.assertEqual(acc["server_id"], "axor-lab-server-A")
        self.assertEqual(acc, original)

    def test_tampered_acceptance_is_dropped_and_reminted(self) -> None:
        # a hand-edited acceptance.json (semantic_report_ref no longer binds its
        # report) must NOT be restored; the server re-mints a clean one on load
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle_and_traces()
        store = PublicationStore(root=root, server_id="axor-lab-server-A")
        stored = store.publish(bundle, traces, question="q")
        pid = str(stored.publication["publication_id"])
        acc_file = root / pid / "acceptance.json"
        tampered = json.loads(acc_file.read_text())
        tampered["semantic_report"]["verified"].append("FABRICATED_CHECK")
        acc_file.write_text(json.dumps(tampered))  # ref no longer matches report

        store2 = PublicationStore(root=root, server_id="axor-lab-server-A")
        reloaded = store2.get(pid)
        self.assertIsNone(reloaded.acceptance)  # tampered file was dropped on load
        served = store2.acceptance(reloaded)     # re-minted clean from the evidence
        self.assertNotIn("FABRICATED_CHECK", served["semantic_report"]["verified"])
        self.assertEqual(served["semantic_report_ref"], content_hash(served["semantic_report"]))


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

    def test_signed_acceptance_survives_server_key_rotation(self) -> None:
        # publish under key/key_id A, then reload the server with a ROTATED key B.
        # The served acceptance must still carry key_id A and verify under pubkey A
        # — the historical attestation is NOT re-signed with B (review r16 P1)
        from nacl.signing import SigningKey

        from lab_contracts.signing import verify_bundle_signature

        sk_a = SigningKey.generate()
        priv_a, pub_a = bytes(sk_a).hex(), bytes(sk_a.verify_key).hex()
        priv_b = bytes(SigningKey.generate()).hex()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle_and_traces()
        store_a = PublicationStore(
            root=root, server_id="lab.example.com",
            server_key_id="lab-prod-2026", server_signing_key=priv_a,
        )
        stored = store_a.publish(bundle, traces, question="q")
        pid = str(stored.publication["publication_id"])

        rotated = PublicationStore(
            root=root, server_id="lab.example.com",
            server_key_id="lab-prod-2027", server_signing_key=priv_b,  # rotated
        )
        acc = rotated.acceptance(rotated.get(pid))
        self.assertEqual(acc["key_id"], "lab-prod-2026")   # the ORIGINAL key_id
        verify_bundle_signature(acc, str(acc["signature"]), pub_a)  # verifies under A, not B


if __name__ == "__main__":
    unittest.main()
