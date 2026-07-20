"""B4 — production hardening slices.

Covers the code-level DoD: the signed-integrity path (a known author key
upgrades hash_verified → signed, an unknown key is refused, origin never
changes), and takedown (a publication leaves the catalog while its append-only
attestation record is preserved). Postgres/OAuth/object-storage are infra
deferred; these are the behaviors that must hold regardless of backing store.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server.errors import NotFound, PublishRejected
from lab_server.store import PublicationStore

_HAS_NACL = importlib.util.find_spec("nacl") is not None
CREATED = "2026-07-19T12:00:00+00:00"


def _bundle_and_traces() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=6, run_id="r_hard",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", 0, len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_hard", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestTakedown(unittest.TestCase):
    def test_takedown_removes_from_catalog_but_preserves_attestations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            stored = store.publish(bundle, traces, question="Q?")
            pid = str(stored.publication["publication_id"])
            store.add_attestation(pid, {
                "schema_version": "attestation/v1", "publication_id": pid,
                "by": "@ext", "kind": "fresh_live", "created": "2026-07-20T00:00:00Z",
                "result": {"estimate": 0.0},
            })

            store.takedown(pid)
            self.assertTrue(store.is_taken_down(pid))
            self.assertEqual(store.catalog(), [])
            with self.assertRaises(NotFound):
                store.get(pid)
            # the append-only attestation record survives the takedown
            self.assertEqual(len(store.reproductions_of(pid)), 1)

    def test_takedown_survives_a_store_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            pid = str(store.publish(bundle, traces, question="Q?").publication["publication_id"])
            store.add_attestation(pid, {
                "schema_version": "attestation/v1", "publication_id": pid,
                "by": "@ext", "kind": "exact_replay", "created": "2026-07-20T00:00:00Z",
                "result": {"estimate": 0.0},
            })
            store.takedown(pid)

            reloaded = PublicationStore(root=Path(tmp))
            self.assertTrue(reloaded.is_taken_down(pid))
            self.assertEqual(reloaded.catalog(), [])
            self.assertEqual(len(reloaded.reproductions_of(pid)), 1)

    def test_takedown_follows_the_evidence_not_just_the_exact_id(self) -> None:
        # takedown removes the EVIDENCE: the same bundle re-published under a
        # different question/visibility mints a different publication_id, but it
        # must NOT bring the taken-down evidence back (review r14)
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            pid_a = str(store.publish(bundle, traces, question="Did Axor stop exfiltration?",
                                      visibility="public").publication["publication_id"])
            store.takedown(pid_a)

            with self.assertRaises(PublishRejected) as ctx:
                # SAME bundle, DIFFERENT wording + visibility → different id
                store.publish(bundle, traces, question="Did Axor stop data theft?",
                              visibility="private")
            self.assertEqual(ctx.exception.status, 409)
            self.assertIn("evidence", str(ctx.exception).lower())
            self.assertEqual(store.catalog(), [])

    def test_evidence_takedown_survives_a_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            pid = str(store.publish(bundle, traces, question="Q1").publication["publication_id"])
            store.takedown(pid)

            reloaded = PublicationStore(root=Path(tmp))
            with self.assertRaises(PublishRejected) as ctx:
                reloaded.publish(bundle, traces, question="a different question")
            self.assertEqual(ctx.exception.status, 409)

    def test_write_token_cannot_resurrect_a_taken_down_publication(self) -> None:
        # takedown is an ADMIN action; because the id content-addresses the body,
        # a lesser write-token holder who has the bytes could re-derive the same
        # id and re-publish — putting the taken-down record back in the catalog
        # until the next restart. publish() must refuse a tombstoned id (r13).
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            pid = str(store.publish(bundle, traces, question="Q?").publication["publication_id"])
            store.takedown(pid)

            with self.assertRaises(PublishRejected) as ctx:
                store.publish(bundle, traces, question="Q?")  # same body → same id
            self.assertEqual(ctx.exception.status, 409)
            self.assertIn("taken down", str(ctx.exception))
            # still gone from the catalog and still tombstoned
            self.assertEqual(store.catalog(), [])
            self.assertTrue(store.is_taken_down(pid))
            with self.assertRaises(NotFound):
                store.get(pid)


class TestSignedIntegrity(unittest.TestCase):
    def test_unknown_author_key_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp), known_keys={})
            bundle, traces = _bundle_and_traces()
            with self.assertRaises(PublishRejected) as ctx:
                store.publish(bundle, traces, question="Q?",
                              signature="deadbeef", author="@unknown")
            self.assertIn("unknown author key", str(ctx.exception))

    def test_no_signature_stays_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            stored = store.publish(bundle, traces, question="Q?")
            self.assertEqual(stored.publication["integrity"], "hash_verified")
            self.assertEqual(stored.publication["origin"], "local")

    @unittest.skipUnless(_HAS_NACL, "PyNaCl not installed (optional crypto)")
    def test_known_author_signature_upgrades_to_signed_without_changing_origin(self) -> None:
        from nacl.signing import SigningKey

        from lab_contracts.signing import sign_bundle

        key = SigningKey.generate()
        pub = bytes(key.verify_key).hex()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp), known_keys={"@acme": pub})
            bundle, traces = _bundle_and_traces()
            signature = sign_bundle(bundle, bytes(key).hex())
            stored = store.publish(bundle, traces, question="Q?",
                                   signature=signature, author="@acme")
            self.assertEqual(stored.publication["integrity"], "signed")
            self.assertEqual(stored.publication["origin"], "local")  # origin unchanged

    @unittest.skipUnless(_HAS_NACL, "PyNaCl not installed (optional crypto)")
    def test_bad_signature_is_rejected(self) -> None:
        from nacl.signing import SigningKey

        key = SigningKey.generate()
        other = SigningKey.generate()
        pub = bytes(key.verify_key).hex()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp), known_keys={"@acme": pub})
            bundle, traces = _bundle_and_traces()
            from lab_contracts.signing import sign_bundle

            wrong_sig = sign_bundle(bundle, bytes(other).hex())  # signed by the wrong key
            with self.assertRaises(PublishRejected):
                store.publish(bundle, traces, question="Q?",
                              signature=wrong_sig, author="@acme")


class TestFullBundleIntegrity(unittest.TestCase):
    """Review P0.3 — the integrity spine and signature cover EVERY field."""

    def test_tampering_environment_fails_verify(self) -> None:
        from lab_contracts import BundleIntegrityError, verify_bundle
        bundle, traces = _bundle_and_traces()
        bundle["environment"]["model"]["id"] = "some-other-model"  # post-hoc edit
        with self.assertRaises(BundleIntegrityError):
            verify_bundle(bundle, traces)

    def test_tampering_trials_fails_verify(self) -> None:
        from lab_contracts import BundleIntegrityError, verify_bundle
        bundle, traces = _bundle_and_traces()
        bundle["trials"][0]["status"] = "failed"
        with self.assertRaises(BundleIntegrityError):
            verify_bundle(bundle, traces)

    def test_tampering_created_fails_verify(self) -> None:
        from lab_contracts import BundleIntegrityError, verify_bundle
        bundle, traces = _bundle_and_traces()
        bundle["created"] = "1999-01-01T00:00:00Z"
        with self.assertRaises(BundleIntegrityError):
            verify_bundle(bundle, traces)

    @unittest.skipUnless(_HAS_NACL, "PyNaCl not installed (optional crypto)")
    def test_signature_covers_environment_and_trials(self) -> None:
        from nacl.signing import SigningKey
        from lab_contracts.signing import (
            SignatureInvalid,
            sign_bundle,
            verify_bundle_signature,
        )
        key = SigningKey.generate()
        pub = bytes(key.verify_key).hex()
        bundle, _ = _bundle_and_traces()
        sig = sign_bundle(bundle, bytes(key).hex())
        verify_bundle_signature(bundle, sig, pub)  # ok as-is
        # edit a field the OLD content_hashes-only signature would have missed
        bundle["environment"]["model"]["provider"] = "forged"
        with self.assertRaises(SignatureInvalid):
            verify_bundle_signature(bundle, sig, pub)


class TestPublicationIdAndAttestations(unittest.TestCase):
    """§6.3/§6.4 — 128-bit ids + verified, de-duplicated attestations."""

    def test_publication_id_is_128_bit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            pid = str(store.publish(bundle, traces, question="q").publication["publication_id"])
            self.assertEqual(len(pid), len("e_") + 32)  # 32 hex = 128 bits

    def test_duplicate_attestation_does_not_inflate_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            pid = str(store.publish(bundle, traces, question="q").publication["publication_id"])
            att = {"schema_version": "attestation/v1", "publication_id": pid, "by": "@ext",
                   "kind": "fresh_live", "created": "2026-07-20T00:00:00Z", "result": {"estimate": 0.0}}
            store.add_attestation(pid, att)
            store.add_attestation(pid, att)  # same (by, kind, publication) → deduped
            self.assertEqual(len(store.reproductions_of(pid)), 1)

    def test_re_publishing_same_bundle_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            bundle, traces = _bundle_and_traces()
            a = store.publish(bundle, traces, question="q")
            b = store.publish(bundle, traces, question="q")
            self.assertEqual(a.publication["publication_id"], b.publication["publication_id"])

    @unittest.skipUnless(_HAS_NACL, "PyNaCl not installed (optional crypto)")
    def test_signed_attestation_is_verified_and_marked(self) -> None:
        from nacl.signing import SigningKey
        from lab_contracts.publication import add_reproduction
        from lab_contracts.signing import sign_bundle

        key = SigningKey.generate()
        pub = bytes(key.verify_key).hex()
        body = {"schema_version": "attestation/v1", "publication_id": "e_x", "by": "@mit",
                "kind": "fresh_live", "created": "2026-07-20T00:00:00Z", "result": {"estimate": 0.0}}
        sig = sign_bundle({"content_hashes": body}, bytes(key).hex())
        log = add_reproduction((), {**body, "signature": sig}, known_keys={"@mit": pub})
        self.assertTrue(log[0]["verified"])

    def test_attestation_signed_by_unknown_key_is_rejected(self) -> None:
        from lab_contracts.publication import add_reproduction
        from lab_contracts.errors import ClaimTypingError

        body = {"schema_version": "attestation/v1", "publication_id": "e_x", "by": "@stranger",
                "kind": "fresh_live", "created": "2026-07-20T00:00:00Z", "result": {"estimate": 0.0},
                "signature": "deadbeef"}
        with self.assertRaises(ClaimTypingError):
            add_reproduction((), body, known_keys={})
