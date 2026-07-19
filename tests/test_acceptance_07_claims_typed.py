"""Acceptance test 7 — claims are typed.

The publication carries an exactly_replayable claim (verdict on a trace) and
a statistically_reproducible claim (aggregate); no behavioral delta can be
labeled exact — the boundary is structural, not prose (claims.md).
"""

from __future__ import annotations

import unittest

from lab_contracts import ClaimTypingError, build_publication, make_claim
from tests import support

TRACE_REFS = frozenset({"sha256:tr07"})
AGGREGATE_REFS = frozenset({"agg:ASR:governed", "agg:ASR-delta:governed-vs-ungoverned"})


class TestClaimTyping(unittest.TestCase):
    def test_exact_claim_requires_a_trace_support(self) -> None:
        claim = make_claim(
            "exactly_replayable",
            f"On trace t_7c31_07, {support.KERNEL_PINNED} returns DENY; recipient is untrusted_derived.",
            "sha256:tr07",
            trace_refs=TRACE_REFS, aggregate_refs=AGGREGATE_REFS,
        )
        self.assertEqual(claim["kind"], "exactly_replayable")

    def test_statistical_claim_requires_an_aggregate_support(self) -> None:
        claim = make_claim(
            "statistically_reproducible",
            "Governed ASR 0.0 [0, 0.12] over 30 live trials; ungoverned 0.60.",
            "agg:ASR:governed",
            trace_refs=TRACE_REFS, aggregate_refs=AGGREGATE_REFS,
        )
        self.assertEqual(claim["kind"], "statistically_reproducible")

    def test_behavioral_delta_can_never_be_exact(self) -> None:
        with self.assertRaises(ClaimTypingError) as ctx:
            make_claim(
                "exactly_replayable",
                "Governance lowered ASR by 60pp.",
                "agg:ASR-delta:governed-vs-ungoverned",
                trace_refs=TRACE_REFS, aggregate_refs=AGGREGATE_REFS,
            )
        self.assertIn("never exactly replayable", str(ctx.exception))

    def test_statistical_claim_cannot_point_at_a_trace(self) -> None:
        with self.assertRaises(ClaimTypingError):
            make_claim(
                "statistically_reproducible", "…", "sha256:tr07",
                trace_refs=TRACE_REFS, aggregate_refs=AGGREGATE_REFS,
            )

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(ClaimTypingError):
            make_claim(
                "reproducible", "…", "sha256:tr07",
                trace_refs=TRACE_REFS, aggregate_refs=AGGREGATE_REFS,
            )

    def test_publication_with_both_claims_is_schema_valid(self) -> None:
        claims = [
            make_claim(
                "exactly_replayable", "On trace t_7c31_07 the kernel returns DENY.",
                "sha256:tr07", trace_refs=TRACE_REFS, aggregate_refs=AGGREGATE_REFS,
            ),
            make_claim(
                "statistically_reproducible", "Governed ASR 0.0 [0, 0.12] over 30 trials.",
                "agg:ASR:governed", trace_refs=TRACE_REFS, aggregate_refs=AGGREGATE_REFS,
            ),
        ]
        publication = build_publication(
            publication_id="e_claims", bundle_ref="sha256:bundle",
            question="Does governance stop the banking exfil?",
            origin="local", integrity="hash_verified", claims=claims,
            license_id="CC-BY-4.0",
        )
        self.assertEqual(support.schema_errors(publication, "publication"), [])
        kinds = sorted(c["kind"] for c in publication["claims"])  # type: ignore[union-attr]
        self.assertEqual(kinds, ["exactly_replayable", "statistically_reproducible"])


if __name__ == "__main__":
    unittest.main()
