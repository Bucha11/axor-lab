"""Stable evidence lineage + retroactive takedown (review r15).

An evidence-level takedown must follow the EVIDENCE, not a packaging-sensitive
bundle hash: taking one publication down must retire every sibling that rests on
the same evidence (even a sibling published earlier under a different question),
survive cosmetic repackaging (new bundle_id/created), and hold across a restart
whose cold load must collect all lineage tombstones before loading publications.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, evidence_lineage_ref
from lab_runner import run_experiment_suite
from lab_server.errors import NotFound, PublishRejected
from lab_server.store import PublicationStore

CREATED = "2026-07-20T12:00:00+00:00"


def _evidence() -> tuple[list[dict], list[dict], dict, list[dict], list[dict], dict]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_lineage",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return [scenario], conditions, support.manifests(), result.trials, aggregates, traces


def _bundle(bundle_id: str = "b_lin", created: str = CREATED) -> tuple[dict, dict]:
    scenarios, conditions, manifests, trials, aggregates, traces = _evidence()
    bundle = build_bundle(
        bundle_id=bundle_id, created=created, scenarios=scenarios, conditions=conditions,
        tool_manifests=list(manifests.values()), environment=support.environment(),
        trials=trials, aggregates=aggregates, traces=traces,
    )
    return bundle, traces


class TestEvidenceLineage(unittest.TestCase):
    def test_lineage_ref_is_stable_across_repackaging(self) -> None:
        b1, _ = _bundle(bundle_id="b_one", created="2026-07-20T00:00:00+00:00")
        b2, _ = _bundle(bundle_id="b_two", created="2026-07-21T09:30:00+00:00")
        # different bundle_id/created → different content_hash, SAME lineage
        from lab_contracts import content_hash
        self.assertNotEqual(content_hash(b1), content_hash(b2))
        self.assertEqual(evidence_lineage_ref(b1), evidence_lineage_ref(b2))


class TestRetroactiveTakedown(unittest.TestCase):
    def _store(self) -> PublicationStore:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return PublicationStore(root=Path(self.tmp.name))

    def test_takedown_removes_all_existing_publications_for_lineage(self) -> None:
        store = self._store()
        bundle, traces = _bundle()
        a = store.publish(bundle, traces, question="Did Axor stop exfiltration?")
        b = store.publish(bundle, traces, question="Did Axor stop data theft?")
        self.assertNotEqual(a.publication["publication_id"], b.publication["publication_id"])
        # take down A → its sibling B (same evidence, different question) must go too
        store.takedown(str(a.publication["publication_id"]))
        with self.assertRaises(NotFound):
            store.get(str(a.publication["publication_id"]))
        with self.assertRaises(NotFound):
            store.get(str(b.publication["publication_id"]))

    def test_catalog_never_serves_an_evidence_tombstoned_sibling(self) -> None:
        store = self._store()
        bundle, traces = _bundle()
        a = store.publish(bundle, traces, question="q1", visibility="public")
        store.publish(bundle, traces, question="q2", visibility="public")
        self.assertEqual(len(store.catalog()), 2)
        store.takedown(str(a.publication["publication_id"]))
        self.assertEqual(store.catalog(), [])

    def test_takedown_lineage_survives_changed_bundle_id_and_created(self) -> None:
        store = self._store()
        bundle, traces = _bundle(bundle_id="b_orig", created="2026-07-20T00:00:00+00:00")
        a = store.publish(bundle, traces, question="q")
        store.takedown(str(a.publication["publication_id"]))
        # repackage the SAME evidence with a fresh bundle_id/created → a new
        # bundle_ref, but the SAME lineage; re-publish must be refused
        repacked, rt = _bundle(bundle_id="b_repacked", created="2026-07-22T10:00:00+00:00")
        with self.assertRaises(PublishRejected) as ctx:
            store.publish(repacked, rt, question="totally different question")
        self.assertEqual(ctx.exception.status, 409)

    def test_cold_load_collects_all_lineage_tombstones_before_publications(self) -> None:
        # simulate a takedown that CRASHED after tombstoning A but before removing
        # sibling B's files: B's publication.json still on disk, A tombstoned with
        # the lineage. A correct two-pass cold load must NOT serve B.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = PublicationStore(root=root)
        bundle, traces = _bundle()
        a = store.publish(bundle, traces, question="q1")
        b = store.publish(bundle, traces, question="q2")
        lineage = a.lineage_ref
        # hand-write A's tombstone (partial crash) and leave B intact on disk
        a_dir = root / str(a.publication["publication_id"])
        for name in ("publication.json", "bundle.json"):
            (a_dir / name).unlink(missing_ok=True)
        (a_dir / "tombstone.json").write_text(json.dumps({
            "publication_id": str(a.publication["publication_id"]),
            "status": "taken_down", "evidence_lineage_ref": lineage,
        }))
        # a fresh store over the same directory: B's files exist, but its lineage
        # is tombstoned, so the two-pass load must refuse to serve it
        reloaded = PublicationStore(root=root)
        with self.assertRaises(NotFound):
            reloaded.get(str(b.publication["publication_id"]))
        self.assertEqual(reloaded.catalog(), [])


if __name__ == "__main__":
    unittest.main()
