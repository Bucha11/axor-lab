"""Terminology lint (spec-lab.md §8, domain-model.md).

One word per concept in every user-facing surface: run modes are
ungoverned / governed / compare — never "undefended" or "bare" in Lab UI
(the word "undefended" survives only as AgentDojo's condition term, allowed
in adapter provenance/notes). "deterministic"/"bit-identical" attach only to
replayed verdicts, never to a live aggregate.

This lint scans the server-rendered HTML (the actual user-facing surface) and
the CLI help text.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server.html import render_catalog, render_evidence, render_publication
from lab_server.store import PublicationStore

REPO_ROOT = Path(__file__).resolve().parent.parent
_BANNED_RUNMODE_WORDS = ("undefended", "bare agent")


def _stored() -> tuple[PublicationStore, str, str]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=6, run_id="r_term",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate(
            "ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
            test=mcnemar_test(pairs, vs="ungoverned"),
        ),
    ]
    bundle = build_bundle(
        bundle_id="b_term", created="2026-07-19T12:00:00+00:00", scenarios=[scenario],
        conditions=conditions, tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials, aggregates=aggregates,
        traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    denied = next(
        tid for tid, t in traces.items()
        if any(e.get("type") == "gate_decision" and e["decision"]["verdict"] == "DENY"
               for e in t["events"])
    )
    tmp = tempfile.mkdtemp()
    store = PublicationStore(root=Path(tmp) / "store")
    stored = store.publish(bundle, traces, question="Does governance stop the exfil?")
    return store, str(stored.publication["publication_id"]), denied


class TestTerminology(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store, cls.pid, cls.denied = _stored()
        cls.pages = {
            "catalog": render_catalog(cls.store.catalog()),
            "publication": render_publication(cls.store.get(cls.pid)),
            "evidence": render_evidence(cls.store.get(cls.pid), cls.denied),
        }

    def test_pages_use_canonical_run_mode_vocab(self) -> None:
        for name, html in self.pages.items():
            lowered = html.lower()
            for banned in _BANNED_RUNMODE_WORDS:
                self.assertNotIn(banned, lowered, f"{name} page uses banned run-mode word {banned!r}")

    def test_pages_mention_governed_and_ungoverned(self) -> None:
        # the canonical vocabulary is actually present (catalog/publication)
        self.assertIn("ungoverned", self.pages["catalog"].lower())
        self.assertIn("governed", self.pages["publication"].lower())

    def test_statistical_claims_never_assert_determinism(self) -> None:
        # The statistical claim texts (the aggregate claims) must not describe
        # themselves as deterministic/exact/bit-identical. We scan the actual
        # claim divs, not the surrounding honest copy (which may *negate* these
        # words, e.g. "never bit-for-bit").
        import re

        page = self.pages["publication"]
        stat_claims = re.findall(r"<div class='claim stat'>(.*?)</div>", page)
        self.assertTrue(stat_claims)
        for claim in stat_claims:
            lowered = claim.lower()
            for banned in ("deterministic", "bit-for-bit", "bit-identical", "exact"):
                self.assertNotIn(banned, lowered, f"statistical claim asserts {banned!r}: {claim}")

    def test_exact_block_owns_the_determinism_language(self) -> None:
        # "deterministic" appears, but only in the exact-replay explanation.
        page = self.pages["publication"]
        exact_section = page.split("Statistically reproducible", 1)[0]
        self.assertIn("deterministic", exact_section.lower())

    def test_cli_help_avoids_banned_run_mode_words(self) -> None:
        help_text = subprocess.run(
            [sys.executable, "-m", "lab_runner", "--help"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        ).stdout.lower()
        for banned in _BANNED_RUNMODE_WORDS:
            self.assertNotIn(banned, help_text)


if __name__ == "__main__":
    unittest.main()
