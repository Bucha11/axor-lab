"""The shareable artifact: the headline panel, the catalog, and link previews.

These pages are the distribution unit — a publication is meant to be pasted into
a thread or a due-diligence packet. So the things tested here are the ones that
would mislead a reader who never opens the bundle: which direction a delta reads
as good, whether the evidence table shows both arms, and whether a run whose
ungoverned rate is a PARAMETER says so on its face.
"""

from __future__ import annotations

import unittest

from lab_server import html


class TestDeltaDirection(unittest.TestCase):
    """A regression painted green is worse than no chart."""

    def test_a_fall_in_attack_success_is_better(self) -> None:
        chip = html._delta_chip("ASR", 0.63, 0.0)
        self.assertIn("chip good", chip)
        self.assertIn("63.0 pp", chip)
        self.assertIn("better", chip)

    def test_a_rise_in_attack_success_is_worse(self) -> None:
        chip = html._delta_chip("ASR", 0.10, 0.40)
        self.assertIn("chip bad", chip)
        self.assertIn("worse", chip)

    def test_direction_flips_for_a_metric_where_up_is_good(self) -> None:
        # the same arithmetic sign means the opposite thing for utility
        self.assertIn("chip bad", html._delta_chip("task_success_rate", 0.45, 0.10))
        self.assertIn("chip good", html._delta_chip("task_success_rate", 0.10, 0.45))

    def test_no_change_is_neither(self) -> None:
        chip = html._delta_chip("task_success_rate", 0.45, 0.45)
        self.assertIn("unchanged", chip)
        self.assertNotIn("chip good", chip)
        self.assertNotIn("chip bad", chip)

    def test_the_word_carries_the_meaning_not_the_colour(self) -> None:
        # a status colour never stands alone: every chip ships an icon + a word
        for chip in (html._delta_chip("ASR", 0.6, 0.0), html._delta_chip("ASR", 0.0, 0.6)):
            self.assertTrue("&darr;" in chip or "&uarr;" in chip, chip)
            self.assertTrue("better" in chip or "worse" in chip, chip)


class TestTraceSample(unittest.TestCase):
    def _traces(self, condition: str, count: int) -> list[dict[str, object]]:
        return [
            {"trace_id": f"t_r_hash_scn_{condition}_s{i:03d}_r{i}",
             "trial": {"condition_id": condition}}
            for i in range(count)
        ]

    def test_every_condition_appears(self) -> None:
        """Sorting by trace_id and slicing filled the table with one arm."""
        traces = self._traces("governed", 30) + self._traces("ungoverned", 30)
        sample = html._trace_sample(traces, 12)
        conditions = {str(t["trial"]["condition_id"]) for t in sample}  # type: ignore[index]
        self.assertEqual(conditions, {"governed", "ungoverned"})
        self.assertEqual(len(sample), 12)

    def test_a_short_run_is_not_padded_or_truncated(self) -> None:
        sample = html._trace_sample(self._traces("governed", 3), 12)
        self.assertEqual(len(sample), 3)

    def test_an_uneven_split_still_shows_the_rare_arm(self) -> None:
        traces = self._traces("governed", 40) + self._traces("ungoverned", 2)
        sample = html._trace_sample(traces, 12)
        rare = [t for t in sample if t["trial"]["condition_id"] == "ungoverned"]  # type: ignore[index]
        self.assertEqual(len(rare), 2)


class TestShortTraceId(unittest.TestCase):
    def test_the_run_hash_is_dropped_and_the_coordinate_kept(self) -> None:
        short = html._short_trace_id("t_r_e138c0aa51b_agentdojo-banking-leak-iban_governed_s002_r2")
        self.assertEqual(short, "&hellip;agentdojo-banking-leak-iban_governed_s002_r2")

    def test_the_cut_does_not_shift_between_conditions(self) -> None:
        """A fixed-width cut rendered scenario names as plausible garbage."""
        governed = html._short_trace_id("t_r_e138c0aa51b_scn-leak-iban_governed_s002_r2")
        ungoverned = html._short_trace_id("t_r_e138c0aa51b_scn-leak-iban_ungoverned_s002_r2")
        self.assertIn("scn-leak-iban", governed)
        self.assertIn("scn-leak-iban", ungoverned)

    def test_an_id_that_is_not_the_known_shape_is_left_alone(self) -> None:
        self.assertEqual(html._short_trace_id("imported-incident-42"), "imported-incident-42")

    def test_the_display_form_is_escaped(self) -> None:
        short = html._short_trace_id("t_r_hash_<script>_governed_s0_r0")
        self.assertNotIn("<script>", short)


class TestHoistSharedCaveat(unittest.TestCase):
    """Move the repeated caveat; never drop it."""

    SHARED = (
        "matched-pairs design is UPLOADER-DECLARED, not attested: the recompute "
        "verifies the arithmetic over the stored pairing, not that the observations "
        "are genuinely paired (no signed execution receipt)"
    )

    def test_a_caveat_every_claim_repeats_is_hoisted_once(self) -> None:
        texts = [f"ASR under {arm}: 0.5 over 30 trials. {self.SHARED}"
                 for arm in ("ungoverned", "governed")]
        trimmed, shared = html._hoist_shared_caveat(texts)
        self.assertEqual(shared, self.SHARED)
        for text in trimmed:
            self.assertNotIn("UPLOADER-DECLARED", text)
            self.assertIn("over 30 trials", text)

    def test_claims_that_share_nothing_are_untouched(self) -> None:
        texts = ["ASR under ungoverned: 0.5", "task_success_rate under governed: 0.9"]
        trimmed, shared = html._hoist_shared_caveat(texts)
        self.assertEqual(shared, "")
        self.assertEqual(trimmed, texts)

    def test_a_single_claim_is_never_hoisted(self) -> None:
        texts = [f"ASR under ungoverned: 0.5. {self.SHARED}"]
        self.assertEqual(html._hoist_shared_caveat(texts), (texts, ""))

    def test_nothing_is_hoisted_if_it_would_empty_a_claim(self) -> None:
        texts = [self.SHARED, self.SHARED]
        trimmed, shared = html._hoist_shared_caveat(texts)
        self.assertEqual(shared, "")
        self.assertEqual(trimmed, texts)


class TestShortHash(unittest.TestCase):
    def test_a_digest_is_elided_but_kept_whole_in_the_title(self) -> None:
        full = "sha256:" + "ab" * 32
        rendered = html._short_hash(full)
        self.assertIn(f"title='{full}'", rendered)
        self.assertIn("&hellip;", rendered)
        self.assertNotIn(f">{full}<", rendered)

    def test_a_short_value_is_shown_whole(self) -> None:
        self.assertEqual(html._short_hash("n/a"), "<code>n/a</code>")


if __name__ == "__main__":
    unittest.main()
