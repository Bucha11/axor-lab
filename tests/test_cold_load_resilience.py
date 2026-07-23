"""One corrupt file must not sink the whole catalog on startup (review r10).

PublicationStore.__post_init__ iterated every directory and called _load with no
error isolation, and _load did unguarded json.loads on publication/bundle/traces/
receipt/reproductions plus trace["trace_id"]; rebuild_reproduction_log assumed
each entry was a dict and called .get() on it. So a single `{broken` file or a
non-object array element (`[42]`) could raise on startup and prevent the entire
store from loading. Now each directory is isolated and non-dict entries are
skipped.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_contracts import build_bundle
from lab_contracts.publication import rebuild_reproduction_log
from lab_runner import run_experiment_suite
from lab_server.store import PublicationStore

CREATED = "2026-07-19T12:00:00+00:00"


def _bundle():
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=4, run_id="r_clr",
    )
    bundle = build_bundle(
        bundle_id="b_clr", created=CREATED, scenarios=[scenario], conditions=support.conditions(),
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=[], traces=result.traces,
    )
    return bundle, {str(t["trace_id"]): t for t in result.traces.values()}


class TestColdLoadResilience(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "store"
        self.bundle, self.traces = _bundle()

    def test_a_corrupt_directory_does_not_prevent_loading_the_good_ones(self) -> None:
        store = PublicationStore(root=self.root)
        good = store.publish(self.bundle, self.traces, question="q", visibility="public")
        pid = str(good.publication["publication_id"])
        # plant a corrupt sibling directory
        bad = self.root / "e_corrupt"
        bad.mkdir()
        (bad / "publication.json").write_text("{broken json")
        # ...and corrupt the good publication's reproductions with a non-list
        (self.root / pid / "reproductions.json").write_text("{not a list")
        # restart must not raise, and must still serve the good publication
        reloaded = PublicationStore(root=self.root)
        self.assertEqual(str(reloaded.get(pid).publication["publication_id"]), pid)
        self.assertEqual(reloaded.reproductions_of(pid), [])  # corrupt log → empty

    def test_non_object_reproduction_entry_is_skipped_not_fatal(self) -> None:
        raw = (42, "nope", ["still", "not", "a", "dict"],
               {"schema_version": "attestation/v1", "publication_id": "e_1", "by": "@ok",
                "kind": "fresh_live", "created": CREATED})
        rebuilt = rebuild_reproduction_log(raw, expected_publication_id="e_1")
        self.assertEqual([str(a["by"]) for a in rebuilt], ["@ok"])

    def test_a_broken_receipt_degrades_to_hash_verified_without_crashing(self) -> None:
        store = PublicationStore(root=self.root)
        good = store.publish(self.bundle, self.traces, question="q", visibility="public")
        pid = str(good.publication["publication_id"])
        (self.root / pid / "receipt.json").write_text("{broken")
        # a corrupt receipt is treated as absent → the unsigned publication still
        # loads as hash_verified, and startup does not raise
        reloaded = PublicationStore(root=self.root)
        self.assertEqual(reloaded.get(pid).publication["integrity"], "hash_verified")


if __name__ == "__main__":
    unittest.main()
