"""Durable, crash-safe, order-independent lineage tombstones (review r16).

A repeated takedown must not erase the stable lineage; a crash after the lineage
tombstone (but before body deletion) must still be final; a legacy round-14
bundle_ref tombstone must still block re-publish; and the lineage identity must
not depend on manifest/scenario/condition array order.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle, content_hash, evidence_lineage_ref
from lab_runner import run_experiment_suite
from lab_server.errors import NotFound, PublishRejected
from lab_server.store import PublicationStore

CREATED = "2026-07-20T12:00:00+00:00"


def _bundle(bundle_id: str = "b_lin", *, manifests_reversed: bool = False):
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_lindur",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    manifests = list(support.manifests().values())
    if manifests_reversed:
        manifests = list(reversed(manifests))
    bundle = build_bundle(
        bundle_id=bundle_id, created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=manifests, environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestLineageOrderIndependence(unittest.TestCase):
    def test_lineage_is_manifest_order_independent(self) -> None:
        a, _ = _bundle("b_a", manifests_reversed=False)
        b, _ = _bundle("b_b", manifests_reversed=True)
        self.assertNotEqual(content_hash(a), content_hash(b))  # arrays differ
        self.assertEqual(evidence_lineage_ref(a), evidence_lineage_ref(b))  # lineage same


class TestTakedownDurability(unittest.TestCase):
    def test_repeated_takedown_preserves_lineage_after_restart(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = PublicationStore(root=root)
        bundle, traces = _bundle()
        a = store.publish(bundle, traces, question="q1")
        lineage = a.lineage_ref
        store.takedown(str(a.publication["publication_id"]))
        # a REPEAT takedown of the same (now tombstoned) id must be a no-op — it
        # must NOT overwrite the tombstone with an empty lineage
        store.takedown(str(a.publication["publication_id"]))
        # restart: a fresh store on the same dir must still know the lineage
        reloaded = PublicationStore(root=root)
        repacked, rt = _bundle(bundle_id="b_repacked")  # same evidence, new packaging
        with self.assertRaises(PublishRejected):
            reloaded.publish(repacked, rt, question="totally different question")

    def test_crash_after_lineage_tombstone_before_deletion_is_final(self) -> None:
        # simulate a crash: the durable lineage tombstone was written, but the
        # publication body was NOT deleted. A restart must still block re-publish
        # AND refuse to serve the orphaned body.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = PublicationStore(root=root)
        bundle, traces = _bundle()
        a = store.publish(bundle, traces, question="q1")
        lineage = a.lineage_ref
        # hand-write ONLY the durable lineage tombstone (crash before body sweep)
        lin_dir = root / "_lineage_tombstones"
        lin_dir.mkdir(exist_ok=True)
        (lin_dir / (lineage.removeprefix("sha256:") + ".json")).write_text(
            json.dumps({"evidence_lineage_ref": lineage})
        )
        reloaded = PublicationStore(root=root)
        # the orphaned body is not served (lineage guard), and re-publish is blocked
        with self.assertRaises(NotFound):
            reloaded.get(str(a.publication["publication_id"]))
        repacked, rt = _bundle(bundle_id="b_repacked2")
        with self.assertRaises(PublishRejected):
            reloaded.publish(repacked, rt, question="q2")


class TestLegacyTombstoneCompat(unittest.TestCase):
    def test_legacy_bundle_ref_tombstone_still_blocks_republish(self) -> None:
        # a round-14 tombstone stored only a bundle_ref (content_hash of the whole
        # bundle). A restart must still block re-publishing that exact bundle.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bundle, traces = _bundle()
        # hand-write a legacy tombstone dir (bundle_ref only, no lineage)
        legacy_dir = root / "e_legacy00000000000000000000000000"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "tombstone.json").write_text(json.dumps({
            "publication_id": "e_legacy00000000000000000000000000",
            "status": "taken_down", "bundle_ref": content_hash(bundle),
        }))
        store = PublicationStore(root=root)
        with self.assertRaises(PublishRejected):
            store.publish(bundle, traces, question="re-publish the exact bundle")


if __name__ == "__main__":
    unittest.main()
