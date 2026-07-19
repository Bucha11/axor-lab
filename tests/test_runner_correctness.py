"""Runner correctness fixes (review §4.2–§4.5)."""

from __future__ import annotations

import unittest

from tests import support
from lab_runner import KernelRegistry, ScriptedAgent, run_experiment, run_trial
from lab_runner.experiment_file import resolve
from lab_runner.ledger import ValueLedger

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)


class TestProvenanceScoping(unittest.TestCase):
    """§4.2 — the conservative join is scoped to the call's context."""

    def test_join_uses_only_context_values_not_all_untrusted(self) -> None:
        ledger = ValueLedger()
        v_in_context = ledger.mint_external_read("injection", "tool:read:a")
        v_out_of_context = ledger.mint_external_read("other", "tool:read:b")
        # a model call that only saw v_in_context
        v_model = ledger.mint_model_extraction("DE89…", context_value_ids=(v_in_context,))
        derived = ledger.get(v_model)["derived_from"]
        self.assertIn(v_in_context, derived)
        self.assertNotIn(v_out_of_context, derived)  # not joined over the whole ledger

    def test_slice_trace_still_taints_the_recipient(self) -> None:
        outcome = run_trial(
            support.banking_scenario(), support.manifests(), support.conditions()[1],
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="r", seed="s0", repeat_index=0, agent=ATTACK_ALWAYS,
        )
        recipient_v = next(
            v for v in outcome.trace["values"]
            if "model_extraction" in v.get("transformations", [])
        )
        self.assertIn("untrusted_derived", recipient_v["labels"])
        self.assertTrue(recipient_v["derived_from"])  # scoped to the read it saw


class TestFailureCaptureAndHistory(unittest.TestCase):
    """§4.3/§4.4 — a bad trial is recorded, not fatal; retries keep history."""

    def test_a_failing_trial_is_recorded_not_fatal(self) -> None:
        class BoomAgent(ScriptedAgent):
            def follows_injection(self, scenario_name: str, seed: str) -> bool:
                raise RuntimeError("boom")

        result = run_experiment(
            support.banking_scenario(), support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=3, run_id="r_fail", agent=BoomAgent(),
        )
        # every trial failed, but the experiment completed and recorded them
        statuses = {t["status"] for t in result.trials}
        self.assertEqual(statuses, {"failed"})
        self.assertTrue(all("boom" in t["failure_reason"] for t in result.trials))

    def test_failed_trials_feed_missingness(self) -> None:
        from lab_analysis import missingness

        class BoomOnGoverned(ScriptedAgent):
            def follows_injection(self, scenario_name: str, seed: str) -> bool:
                return True

        # one clean run then check missingness accounting on a mixed set
        result = run_experiment(
            support.banking_scenario(), support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=4, run_id="r_ok", agent=ATTACK_ALWAYS,
        )
        result.trials.append({
            "trial_id": "t_bad", "scenario_id": "banking-exfil-01", "condition_id": "governed",
            "seed": "s099", "repeat_index": 99, "status": "failed", "failure_reason": "provider 429",
        })
        summary = missingness(result.trials)
        self.assertEqual(summary.n_missing, 1)
        self.assertIn("provider 429", summary.display())

    def test_retry_preserves_the_superseded_attempt(self) -> None:
        result = run_experiment(
            support.banking_scenario(), support.manifests(), [support.conditions()[1]],
            support.kernel_registry(), repeats=1, run_id="r_retry", agent=ATTACK_ALWAYS,
        )
        # re-run the same (scenario, condition, seed, repeat) → replaces current,
        # keeps the prior as superseded
        run_experiment  # noqa: B018
        first_trace_ref = result.trials[0]["trace_ref"]
        result2 = run_experiment(
            support.banking_scenario(), support.manifests(), [support.conditions()[1]],
            support.kernel_registry(), repeats=1, run_id="r_retry", agent=ATTACK_ALWAYS,
        )
        # merge a retry into the same result to exercise supersession
        outcome_key = list(result2.outcomes)[0]
        result.add(outcome_key, {**result.trials[0], "trace_ref": "sha256:new"},
                   result2.outcomes[outcome_key])
        self.assertEqual(len(result.trials), 1)               # current only
        self.assertEqual(len(result.superseded), 1)           # history kept
        self.assertEqual(result.superseded[0]["trace_ref"], first_trace_ref)


class TestInlineManifests(unittest.TestCase):
    """§4.5 — an inline tool manifest resolves alongside $ref."""

    def test_inline_manifest_resolves(self) -> None:
        scenario = support.banking_scenario()
        # inline the send_money manifest directly in the scenario's tools
        inline = support.send_money_manifest()
        scenario["tools"] = [{"$ref": "read_txns"}, inline]
        document = {
            "experiment": {
                "schema_version": "experiment/v1", "id": "exp", "type": "benchmark",
                "scenario_ids": ["banking-exfil-01"], "conditions": support.conditions(),
                "repeats": 2, "agent_ref": "scripted",
            },
            "scenarios": [scenario],
            "tool_manifests": [support.read_txns_manifest()],  # send_money only inline
        }
        resolved = resolve(document)  # must not error on the inline manifest
        self.assertIn("send_money", resolved.manifests)


if __name__ == "__main__":
    unittest.main()
