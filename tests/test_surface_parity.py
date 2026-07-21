"""Surface parity (review r17): a valid artifact must survive EVERY surface.

A mixed-kernel publication renders (not a 400 KeyError); the acceptance report
lists only the checks that actually ran; and a durable tombstone fsyncs the file
CONTENTS before the rename, not just the directory entry.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from lab_analysis import binary_aggregate
from lab_contracts import build_bundle, condition_config_hash, content_hash
from lab_runner import run_experiment_suite
from lab_runner.kernel import Kernel, KernelRegistry
from lab_server import store as store_mod
from lab_server.html import render_publication
from lab_server.store import PublicationStore

CREATED = "2026-07-20T12:00:00+00:00"

# two DISTINCT reference version strings that behave identically (both taint_floor
# on), so a mixed-kernel bundle round-trips through the server's default_registry
KERNEL_A = support.KERNEL_PINNED                      # reference_taint_floor_kernel
KERNEL_B = "reference_taint_floor_kernel@alt"         # same behavior, different id


def _mixed_kernel_bundle():
    scenario = support.banking_scenario()
    conditions = support.conditions()
    # both conditions enforce/observe under the SAME taint-floor behavior but pin
    # DIFFERENT kernel version strings → a genuinely mixed-kernel bundle whose
    # environment omits the singular kernel_version and carries kernel_versions
    conditions[0] = {**conditions[0], "kernel": KERNEL_A,
                     "config_hash": condition_config_hash(KERNEL_A, None)}
    conditions[1] = {**conditions[1], "kernel": KERNEL_B,
                     "config_hash": condition_config_hash(KERNEL_B, conditions[1]["policy"])}
    registry = KernelRegistry(kernels=(Kernel(version=KERNEL_A), Kernel(version=KERNEL_B)))
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, registry, repeats=8, run_id="r_mixed",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs)),
    ]
    env = {
        "model": {"provider": "scripted", "id": "labref-scripted-agent"},
        "kernel_versions": sorted({KERNEL_A, KERNEL_B}),
    }
    bundle = build_bundle(
        bundle_id="b_mixed", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=env,
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


class TestMixedKernelPage(unittest.TestCase):
    def test_mixed_kernel_publication_page_renders(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = PublicationStore(root=Path(tmp.name))
        bundle, traces = _mixed_kernel_bundle()
        self.assertNotIn("kernel_version", bundle["environment"])  # genuinely mixed
        stored = store.publish(bundle, traces, question="mixed kernels?")
        html = render_publication(stored)  # must NOT raise KeyError
        # both kernels are shown in the methodology
        self.assertIn(KERNEL_A, html)
        self.assertIn(KERNEL_B, html)


class TestDynamicAcceptanceReport(unittest.TestCase):
    def _bundle_no_aggregates(self):
        scenario = support.banking_scenario()
        conditions = support.conditions()
        result = run_experiment_suite(
            [scenario], support.manifests(), conditions, support.kernel_registry(),
            repeats=6, run_id="r_noagg",
        )
        bundle = build_bundle(
            bundle_id="b_noagg", created=CREATED, scenarios=[scenario], conditions=conditions,
            tool_manifests=list(support.manifests().values()), environment=support.environment(),
            trials=result.trials, aggregates=[], traces=result.traces,
        )
        traces = {str(t["trace_id"]): t for t in result.traces.values()}
        return bundle, traces

    def test_acceptance_without_aggregates_does_not_claim_statistics_recomputed(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = PublicationStore(root=Path(tmp.name))
        bundle, traces = self._bundle_no_aggregates()
        stored = store.publish(bundle, traces, question="no stats")
        acc = store.acceptance(stored)
        verified = acc["semantic_report"]["verified"]
        self.assertNotIn("statistics_recomputed", verified)  # nothing to recompute
        self.assertIn("statistics_not_applicable", verified)
        # still content-addressed and self-consistent
        self.assertEqual(acc["semantic_report_ref"], content_hash(acc["semantic_report"]))

    def test_acceptance_with_aggregates_claims_statistics_recomputed(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = PublicationStore(root=Path(tmp.name))
        bundle, traces = _mixed_kernel_bundle()  # has aggregates
        stored = store.publish(bundle, traces, question="with stats")
        verified = store.acceptance(stored)["semantic_report"]["verified"]
        self.assertIn("statistics_recomputed", verified)


class TestTombstoneDurability(unittest.TestCase):
    def test_tombstone_file_data_is_fsynced_before_success(self) -> None:
        # _write_atomic(durable=True) must fsync the file CONTENTS before the
        # rename (and the directory after) — a directory fsync alone makes the
        # rename durable but not the bytes (review r17)
        events: list[str] = []
        real_fsync = store_mod.os.fsync
        real_replace = store_mod.os.replace

        def rec_fsync(fd):
            events.append("fsync")
            return real_fsync(fd)

        def rec_replace(a, b):
            events.append("replace")
            return real_replace(a, b)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "tomb.json"
            with mock.patch.object(store_mod.os, "fsync", rec_fsync), \
                 mock.patch.object(store_mod.os, "replace", rec_replace):
                store_mod._write_atomic(path, json.dumps({"x": 1}), durable=True)
            # the CONTENTS were fsync'd BEFORE the rename made the file visible
            self.assertEqual(events[0], "fsync")
            self.assertIn("replace", events)
            self.assertLess(events.index("fsync"), events.index("replace"))
            # and the bytes are actually there
            self.assertEqual(json.loads(path.read_text()), {"x": 1})

    def test_takedown_writes_a_durable_lineage_tombstone(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = PublicationStore(root=root)
        bundle, traces = _mixed_kernel_bundle()
        stored = store.publish(bundle, traces, question="q")
        store.takedown(str(stored.publication["publication_id"]))
        lin_dir = root / "_lineage_tombstones"
        files = list(lin_dir.glob("*.json"))
        self.assertTrue(files)  # a tombstone file exists with real content
        self.assertIn("evidence_lineage_ref", json.loads(files[0].read_text()))


if __name__ == "__main__":
    unittest.main()
