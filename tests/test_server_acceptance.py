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

    def test_invalid_historical_acceptance_is_not_silently_reminted(self) -> None:
        # a hand-edited acceptance.json (semantic_report_ref no longer binds its
        # report) must NOT be silently dropped and replaced by a clean
        # acceptance/v1 that impersonates the publish-time record (review r18).
        # Instead the damaged original is QUARANTINED and the server re-attests
        # with a DISTINCT reacceptance/v1 event.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle_and_traces()
        store = PublicationStore(root=root, server_id="axor-lab-server-A",
                                 clock=lambda: "2020-01-01T00:00:00Z")
        stored = store.publish(bundle, traces, question="q")
        pid = str(stored.publication["publication_id"])
        acc_file = root / pid / "acceptance.json"
        tampered = json.loads(acc_file.read_text())
        tampered["semantic_report"]["verified"].append("FABRICATED_CHECK")
        acc_file.write_text(json.dumps(tampered))  # ref no longer matches report

        store2 = PublicationStore(root=root, server_id="axor-lab-server-A",
                                  clock=lambda: "2020-01-01T00:00:00Z")
        reloaded = store2.get(pid)
        served = store2.acceptance(reloaded)
        # NOT a clean acceptance/v1 masquerading as the original — a re-attestation
        self.assertEqual(served["schema_version"], "axor-lab-reacceptance/v1")
        self.assertNotIn("FABRICATED_CHECK", served["semantic_report"]["verified"])
        self.assertEqual(served["semantic_report_ref"], content_hash(served["semantic_report"]))
        # the damaged original was preserved, not erased
        quarantine = root / pid / "acceptance.invalid.json"
        self.assertTrue(quarantine.is_file())
        self.assertEqual(json.loads(quarantine.read_text()), tampered)

    def test_reacceptance_is_a_distinct_timestamped_event(self) -> None:
        # the re-attestation is a NEW, timestamped event — not the publish-time
        # acceptance under a different guise (review r18)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle_and_traces()
        original = PublicationStore(root=root, server_id="axor-lab-server-A")
        stored = original.publish(bundle, traces, question="q")
        pid = str(stored.publication["publication_id"])
        acc_file = root / pid / "acceptance.json"
        tampered = json.loads(acc_file.read_text())
        tampered["semantic_report"]["verified"].append("FABRICATED_CHECK")
        acc_file.write_text(json.dumps(tampered))

        reloaded = PublicationStore(
            root=root, server_id="axor-lab-server-A",
            clock=lambda: "2026-07-21T12:00:00Z",
        ).get(pid)
        self.assertEqual(reloaded.acceptance["schema_version"], "axor-lab-reacceptance/v1")
        self.assertEqual(reloaded.acceptance["reaccepted_at"], "2026-07-21T12:00:00Z")
        # the original acceptance/v1 carried no such timestamp field
        self.assertNotIn("reaccepted_at", stored.acceptance)

    def test_reacceptance_links_to_invalid_previous_receipt(self) -> None:
        # the re-attestation NAMES the invalid receipt it replaces, by content hash
        # (review r18): the history is auditable, not a silent swap
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
        acc_file.write_text(json.dumps(tampered))

        reloaded = PublicationStore(root=root, server_id="axor-lab-server-A").get(pid)
        supersedes = reloaded.acceptance["supersedes"]
        self.assertEqual(supersedes["previous_ref"], content_hash(tampered))
        self.assertEqual(supersedes["previous_schema_version"], "axor-lab-acceptance/v1")

    def test_reacceptance_is_stable_across_reloads(self) -> None:
        # once re-attested, a second cold load reads the persisted reacceptance/v1,
        # verifies it, and restores it VERBATIM — it does not re-quarantine or
        # re-stamp a fresh timestamp every load (review r18)
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
        acc_file.write_text(json.dumps(tampered))

        first = PublicationStore(
            root=root, server_id="axor-lab-server-A",
            clock=lambda: "2026-07-21T12:00:00Z",
        ).get(pid).acceptance
        second = PublicationStore(
            root=root, server_id="axor-lab-server-A",
            clock=lambda: "2099-01-01T00:00:00Z",  # would differ if re-stamped
        ).get(pid).acceptance
        self.assertEqual(first, second)
        self.assertEqual(second["reaccepted_at"], "2026-07-21T12:00:00Z")

    def test_missing_historical_key_does_not_reissue_acceptance_silently(self) -> None:
        # a SIGNED acceptance whose signing key is not in the keyring (rotated out
        # and not retained) is kept as an OPAQUE historical record — preserved with
        # its original key_id/signature, never silently re-issued under a new key
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle_and_traces()
        store = PublicationStore(root=root, server_id="axor-lab-server-A")
        stored = store.publish(bundle, traces, question="q")
        pid = str(stored.publication["publication_id"])
        acc_file = root / pid / "acceptance.json"
        historical = json.loads(acc_file.read_text())
        # make it a signed receipt from a key we no longer hold (binding intact)
        historical["algorithm"] = "ed25519"
        historical["key_id"] = "rotated-out-2024"
        historical["signature"] = "ab" * 32
        acc_file.write_text(json.dumps(historical))

        # reload with NO key for "rotated-out-2024" in the keyring
        reloaded = PublicationStore(root=root, server_id="axor-lab-server-B").get(pid)
        served = PublicationStore(root=root, server_id="axor-lab-server-B").acceptance(reloaded)
        self.assertEqual(served["key_id"], "rotated-out-2024")   # preserved
        self.assertEqual(served["signature"], "ab" * 32)          # not re-signed
        self.assertEqual(served, historical)                      # opaque, untouched


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

    def test_historical_keyring_verifies_rotated_acceptance(self) -> None:
        # publish under key A, reload with rotated key B BUT with A retained in the
        # historical keyring → the signature is verified (not just binding-checked)
        # and the original receipt is served
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
            server_key_id="key-A", server_signing_key=priv_a,
        )
        pid = str(store_a.publish(bundle, traces, question="q").publication["publication_id"])

        rotated = PublicationStore(
            root=root, server_id="lab.example.com",
            server_key_id="key-B", server_signing_key=priv_b,
            known_server_keys={"key-A": pub_a},  # retain the old key
        )
        acc = rotated.acceptance(rotated.get(pid))
        self.assertEqual(acc["key_id"], "key-A")
        verify_bundle_signature(acc, str(acc["signature"]), pub_a)

    def test_forged_signed_acceptance_under_known_key_is_quarantined_and_reaccepted(self) -> None:
        # tamper the semantic report AND recompute its ref (binding-consistent) so
        # only the SIGNATURE is now wrong. With the signing key in the keyring the
        # forgery is detected on load — and rather than silently dropped-then-
        # reminted as a clean acceptance/v1, it is QUARANTINED and re-attested with
        # a distinct, signed reacceptance/v1 linking to the forged original (r18).
        from nacl.signing import SigningKey

        sk = SigningKey.generate()
        priv, pub = bytes(sk).hex(), bytes(sk.verify_key).hex()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle_and_traces()
        store = PublicationStore(
            root=root, server_id="lab.example.com",
            server_key_id="key-A", server_signing_key=priv,
        )
        pid = str(store.publish(bundle, traces, question="q").publication["publication_id"])
        acc_file = root / pid / "acceptance.json"
        forged = json.loads(acc_file.read_text())
        forged["semantic_report"]["verified"].append("FABRICATED_CHECK")
        forged["semantic_report_ref"] = content_hash(forged["semantic_report"])  # binding-consistent
        acc_file.write_text(json.dumps(forged))  # signature no longer matches the body

        served = PublicationStore(
            root=root, server_id="lab.example.com",
            server_key_id="key-A", server_signing_key=priv,
            known_server_keys={"key-A": pub},
        ).get(pid).acceptance
        # a distinct, SIGNED re-attestation — not a clean acceptance/v1 masquerade
        self.assertEqual(served["schema_version"], "axor-lab-reacceptance/v1")
        self.assertEqual(served["algorithm"], "ed25519")
        self.assertNotIn("FABRICATED_CHECK", served["semantic_report"]["verified"])
        # it links to the forged original, and the damaged bytes are preserved
        self.assertEqual(served["supersedes"]["previous_ref"], content_hash(forged))
        self.assertEqual(json.loads((root / pid / "acceptance.invalid.json").read_text()), forged)
        # and the signed reacceptance verifies against the current key
        from lab_contracts.signing import verify_reacceptance

        publication = json.loads((root / pid / "publication.json").read_text())
        verify_reacceptance(served, publication, server_pubkey_hex=pub)


if __name__ == "__main__":
    unittest.main()
