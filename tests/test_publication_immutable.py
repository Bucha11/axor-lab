"""A publication is immutable and content-addressed (review round 7, P0).

The id content-addresses the WHOLE publication body, so:
  - re-publishing byte-identical inputs is idempotent (same id, reproductions
    preserved — a re-publish never silently wipes the attestation log);
  - the same evidence answering a DIFFERENT question is a genuinely different
    publication (different id), not a mutation of the first;
  - a publication.json hand-edited on disk (visibility flipped to public,
    integrity forged to signed, question/claims rewritten) no longer matches
    its own id and is DROPPED on the next server restart, never trusted.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server.store import PublicationStore

CREATED = "2026-07-19T12:00:00+00:00"


def _bundle():
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=6, run_id="r_im",
    )
    bundle = build_bundle(
        bundle_id="b_im", created=CREATED, scenarios=[scenario], conditions=support.conditions(),
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=[], traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestPublicationImmutable(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "store"
        self.bundle, self.traces = _bundle()

    def _store(self) -> PublicationStore:
        return PublicationStore(root=self.root)

    def _attest(self, store: PublicationStore, pid: str, by: str) -> None:
        store.add_attestation(pid, {
            "schema_version": "attestation/v1", "publication_id": pid,
            "by": by, "kind": "fresh_live", "created": CREATED, "result": {"estimate": 0.0},
        })

    def test_identical_republish_is_idempotent_and_preserves_reproductions(self) -> None:
        store = self._store()
        first = store.publish(self.bundle, self.traces, question="q", visibility="public")
        pid = str(first.publication["publication_id"])
        self._attest(store, pid, by="@ext")
        # re-publish the SAME bundle + question: same id, and the appended
        # attestation is NOT wiped by the re-publish
        again = store.publish(self.bundle, self.traces, question="q", visibility="public")
        self.assertEqual(str(again.publication["publication_id"]), pid)
        self.assertEqual(len(store.reproductions_of(pid)), 1)

    def test_same_evidence_different_question_is_a_distinct_publication(self) -> None:
        store = self._store()
        a = store.publish(self.bundle, self.traces, question="qa", visibility="public")
        b = store.publish(self.bundle, self.traces, question="qb", visibility="public")
        self.assertNotEqual(a.publication["publication_id"], b.publication["publication_id"])
        # both share the same evidence bundle
        self.assertEqual(a.publication["bundle_ref"], b.publication["bundle_ref"])

    def test_visibility_is_part_of_identity(self) -> None:
        store = self._store()
        pub = store.publish(self.bundle, self.traces, question="q", visibility="public")
        priv = store.publish(self.bundle, self.traces, question="q", visibility="private")
        self.assertNotEqual(pub.publication["publication_id"], priv.publication["publication_id"])

    def test_tampered_visibility_is_dropped_on_restart(self) -> None:
        store = self._store()
        priv = store.publish(self.bundle, self.traces, question="q", visibility="private")
        pid = str(priv.publication["publication_id"])
        # an attacker with disk access flips the private publication to public
        pub_file = self.root / pid / "publication.json"
        body = json.loads(pub_file.read_text())
        body["visibility"] = "public"
        pub_file.write_text(json.dumps(body))
        # on restart the forged body no longer matches its content-addressed id
        reloaded = self._store()
        with self.assertRaises(Exception):
            reloaded.get(pid)  # NotFound: the tampered record is not trusted
        self.assertEqual(reloaded.catalog(), [])  # and it never reaches the public catalog

    def test_tampered_integrity_badge_is_dropped_on_restart(self) -> None:
        store = self._store()
        pub = store.publish(self.bundle, self.traces, question="q", visibility="public")
        pid = str(pub.publication["publication_id"])
        pub_file = self.root / pid / "publication.json"
        body = json.loads(pub_file.read_text())
        body["integrity"] = "signed"  # forge a trust badge the server never minted
        pub_file.write_text(json.dumps(body))
        reloaded = self._store()
        with self.assertRaises(Exception):
            reloaded.get(pid)

    def test_untampered_publication_survives_restart(self) -> None:
        store = self._store()
        pub = store.publish(self.bundle, self.traces, question="q", visibility="public")
        pid = str(pub.publication["publication_id"])
        self._attest(store, pid, by="@ext")
        reloaded = self._store()
        self.assertEqual(str(reloaded.get(pid).publication["publication_id"]), pid)
        self.assertEqual(len(reloaded.reproductions_of(pid)), 1)  # attestations persist

    def test_forged_claims_publication_is_rejected_on_restart(self) -> None:
        # a from-scratch publication that NEVER passed the handshake: fabricate a
        # claim, recompute a matching content-addressed id, name the dir that id,
        # and confirm the load path (replay + recompute + re-mint) refuses it —
        # the content-address alone would accept a self-consistent forgery.
        store = self._store()
        pub = store.publish(self.bundle, self.traces, question="q", visibility="public")
        pid = str(pub.publication["publication_id"])
        body = json.loads((self.root / pid / "publication.json").read_text())
        body["claims"] = [{"kind": "statistically_reproducible",
                           "assertion": "ASR under governed: 0.00 [0.00, 0.00] over 999 trials",
                           "evidence_ref": "agg:ASR:governed", "trace_refs": [], "aggregate_refs": []}]
        # recompute the id so the naive content-address check would pass
        from lab_server.store import PublicationStore as _PS
        new_id = _PS._derive_id(body)
        body["publication_id"] = new_id
        body["reproductions_ref"] = f"attlog:{new_id}"
        forged_dir = self.root / new_id
        forged_dir.mkdir()
        (forged_dir / "traces").mkdir()
        (forged_dir / "publication.json").write_text(json.dumps(body))
        (forged_dir / "bundle.json").write_text(json.dumps(self.bundle))
        for t in self.traces.values():
            from lab_contracts import content_hash
            name = content_hash(t).removeprefix("sha256:")
            (forged_dir / "traces" / f"{name}.json").write_text(json.dumps(t))
        reloaded = self._store()
        with self.assertRaises(Exception):
            reloaded.get(new_id)  # re-mint mismatch (fabricated claim) → not trusted


if __name__ == "__main__":
    unittest.main()
