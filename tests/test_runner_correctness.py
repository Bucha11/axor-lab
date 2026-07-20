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

    def test_stochastic_retry_keeps_bundle_verifiable(self) -> None:
        # a real stochastic retry: the second attempt at the SAME trial key
        # produces a DIFFERENT trace (different recipient → different hash). The
        # prior trace must leave the publishable set, or verify_bundle would
        # reject it as an orphan (review r8).
        from lab_contracts import build_bundle, content_hash, verify_bundle

        cond = support.conditions()[1]
        attacked = run_experiment(
            support.banking_scenario(), support.manifests(), [cond],
            support.kernel_registry(), repeats=1, run_id="r_retry", agent=ATTACK_ALWAYS,
        )
        faithful = run_experiment(
            support.banking_scenario(), support.manifests(), [cond],
            support.kernel_registry(), repeats=1, run_id="r_retry", agent=ScriptedAgent(attack_rate=0.0),
        )
        key = list(faithful.outcomes)[0]
        first_ref = attacked.trials[0]["trace_ref"]
        retry_trace = faithful.outcomes[key].trace
        self.assertNotEqual(content_hash(retry_trace), first_ref)  # genuinely different

        # merge the retry into the first result → supersession
        attacked.add(key, {**attacked.trials[0], "trace_ref": content_hash(retry_trace)},
                     faithful.outcomes[key])
        self.assertEqual(len(attacked.trials), 1)          # only the current attempt
        self.assertEqual(len(attacked.traces), 1)          # NO orphan trace left behind
        self.assertEqual(len(attacked.superseded), 1)      # prior attempt kept in the log
        self.assertEqual(attacked.superseded[0]["trace_ref"], first_ref)
        self.assertIn("trace", attacked.superseded[0])     # its evidence is preserved

        # the publishable bundle verifies — trials and traces match one-to-one
        bundle = build_bundle(
            bundle_id="b_retry", created="2026-07-19T12:00:00+00:00",
            scenarios=[support.banking_scenario()], conditions=[cond],
            tool_manifests=list(support.manifests().values()), environment=support.environment(),
            trials=attacked.trials, aggregates=[], traces=attacked.traces,
        )
        traces = {str(t["trace_id"]): t for t in attacked.traces.values()}
        verify_bundle(bundle, traces)  # must not raise: no orphan evidence

    def test_failed_retry_supersedes_a_prior_completed_attempt(self) -> None:
        from lab_contracts import build_bundle, verify_bundle

        cond = support.conditions()[1]
        result = run_experiment(
            support.banking_scenario(), support.manifests(), [cond],
            support.kernel_registry(), repeats=1, run_id="r_retry", agent=ATTACK_ALWAYS,
        )
        key = str(result.trials[0]["trial_id"])
        # a later attempt at the same key FAILS: the prior completed trace must
        # leave the publishable set and its stale outcome must be cleared
        result.add_failure(key, {"trial_id": key, "scenario_id": "banking-exfil-01",
                                 "condition_id": str(cond["id"]), "seed": "s000",
                                 "repeat_index": 0, "status": "failed", "failure_reason": "boom"})
        self.assertEqual(len(result.traces), 0)          # no orphan trace
        self.assertNotIn(key, result.outcomes)           # stale outcome cleared
        self.assertEqual(len(result.superseded), 1)
        bundle = build_bundle(
            bundle_id="b_retry2", created="2026-07-19T12:00:00+00:00",
            scenarios=[support.banking_scenario()], conditions=[cond],
            tool_manifests=list(support.manifests().values()), environment=support.environment(),
            trials=result.trials, aggregates=[], traces=result.traces,
        )
        verify_bundle(bundle, {})  # failed trial references no trace → still verifies


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


class TestSensitiveFieldRedaction(unittest.TestCase):
    """§7.4 — a manifest-declared sensitive field is redacted in the trace."""

    def test_sensitive_untrusted_field_is_redacted(self) -> None:
        from lab_runner import ValueLedger
        from lab_runner.ledger import REDACTED_PREVIEW

        mans = support.manifests()
        mans["read_txns"]["sensitive_fields"] = ["result.transactions[].description"]
        outcome = run_trial(
            support.banking_scenario(), mans, support.conditions()[1],
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="r", seed="s0", repeat_index=0, agent=ATTACK_ALWAYS,
        )
        # direct read values (the external_read constructor), not the model
        # output that merely joined over them
        reads = [v for v in outcome.trace["values"]
                 if "model_extraction" not in v.get("transformations", [])
                 and any(s.get("kind") == "external_read" for s in v["sources"])]
        self.assertTrue(reads)
        for v in reads:
            self.assertEqual(v["preview"], REDACTED_PREVIEW)     # masked
            self.assertNotIn("decision_value", v)                # raw value omitted
            self.assertIn("canonical_value_hash", v)             # still pinned
            self.assertIn("sensitive", v["labels"])

    def test_non_sensitive_field_keeps_its_value(self) -> None:
        outcome = run_trial(
            support.banking_scenario(), support.manifests(), support.conditions()[1],
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="r", seed="s0", repeat_index=0, agent=ATTACK_ALWAYS,
        )
        ext = [v for v in outcome.trace["values"]
               if any(s.get("kind") == "external_read" for s in v["sources"])]
        self.assertTrue(all("decision_value" in v for v in ext))  # not redacted
