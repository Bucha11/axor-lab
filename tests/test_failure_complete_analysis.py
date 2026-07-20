"""One failed trial must not sink the whole run (review round 7, P0).

The runner captures a trial exception as status=failed with no outcome, but the
analysis then indexed result.outcomes[trial_id] for every trial — a KeyError on
the failed one, crashing the command after execution so no bundle or missingness
was produced. Analysis now uses completed-only outcomes and reports missingness
first.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_runner import run_experiment_suite
from lab_runner.experiment_file import ResolvedExperiment
from lab_analysis import missingness


class _FlakyAgent:
    """A deterministic agent that RAISES on one seed to force a failed trial."""

    is_deterministic = True

    def follows_injection(self, scenario_name: str, seed: str) -> bool:
        if seed == "s001":
            raise RuntimeError("simulated backend failure on this trial")
        return True

    def attacker_target(self, injection_text: str) -> str:
        import re
        m = re.search(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}\b", injection_text)
        return m.group(0) if m else "ATTACKER"


class TestFailureCompleteAnalysis(unittest.TestCase):
    def _result(self):
        return run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=6, run_id="r_flaky", agent=_FlakyAgent(),
        )

    def test_failed_trials_are_recorded_not_fatal(self) -> None:
        result = self._result()
        failed = [t for t in result.trials if t["status"] == "failed"]
        self.assertTrue(failed)  # s001 failed under both conditions

    def test_pairs_skip_failed_trials_without_crashing(self) -> None:
        result = self._result()
        pairs = result.pairs("ungoverned", "governed", metric="ASR")  # must not KeyError
        # s001 failed under both conditions → its pair is absent
        self.assertLessEqual(len(pairs), 5)

    def test_aggregates_and_missingness_survive_a_failed_trial(self) -> None:
        from lab_runner.cli import _aggregates
        result = self._result()
        resolved = ResolvedExperiment(
            experiment={"id": "e", "agent_ref": "scripted@0.6", "repeats": 6},
            scenarios=(support.banking_scenario(),), manifests=support.manifests(),
            conditions=tuple(support.conditions()), agent=_FlakyAgent(),
            kernel_registry=support.kernel_registry(),
        )
        # analysis must not crash on the failed trials
        aggregates = _aggregates(resolved, result, _FlakyAgent())
        self.assertTrue(aggregates)  # completed trials still produce aggregates
        summary = missingness(result.trials)
        self.assertGreater(summary.n_missing, 0)
        self.assertLess(summary.n_completed, summary.n_total)


if __name__ == "__main__":
    unittest.main()
