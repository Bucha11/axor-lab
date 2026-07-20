"""Claim + evidence rendering must follow the artifact (review round 3, Patch 10).

- The DENY claim is built from the RECORDED decision (gate, driving value,
  labels, reason) — not a hardcoded "the driving argument is untrusted_derived",
  which would be false for a criticality/budget/approval DENY.
- The publication page no longer prints "No exact claims" when there ARE exact
  claims (the `list.extend() or append()` bug), and the methodology shows the
  actual per-condition diff (config hashes), not "differ only in enforcement".
- EvidenceCase replays under the trace's own / selected condition and offers a
  replay link per enforcing condition.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_contracts import build_bundle
from lab_runner import ScriptedAgent, run_experiment_suite, run_trial
from lab_runner.evidence import _chain, evidence_condition, validate_twin
from lab_server.html import render_evidence, render_publication
from lab_server.store import PublicationStore, StoredPublication, _deny_claim_text

CREATED = "2026-07-19T12:00:00+00:00"


def _denied_trace() -> dict:
    return run_trial(
        support.banking_scenario(), support.manifests(), support.conditions()[1],
        support.kernel_registry().get(support.KERNEL_PINNED),
        run_id="r", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
    ).trace


class TestDenyClaimFromRecordedDecision(unittest.TestCase):
    def test_deny_claim_names_the_recorded_gate_and_driving_value(self) -> None:
        text = _deny_claim_text(_denied_trace())
        self.assertIn("DENY", text)
        self.assertIn("taint_floor", text)  # the real gate, from the decision
        self.assertIn("untrusted", text)    # the driving value's real label

    def test_non_taint_deny_is_not_described_as_untrusted_derived(self) -> None:
        # a DENY from a different gate with a CLEAN driving value must not be
        # narrated as an untrusted-derived taint denial
        trace = _denied_trace()
        for value in trace["values"]:
            value["labels"] = ["clean"]
        for event in trace["events"]:
            if event.get("type") == "gate_decision":
                event["decision"]["gate"] = "criticality_ceiling"
                event["decision"]["reason"] = "operation exceeds criticality ceiling"
        text = _deny_claim_text(trace)
        self.assertIn("criticality_ceiling", text)
        self.assertNotIn("untrusted", text)


def _stored() -> StoredPublication:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=6, run_id="r_ev",
    )
    bundle = build_bundle(
        bundle_id="b_ev", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=[], traces=result.traces,
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = PublicationStore(root=Path(tmp))
        return store.publish(bundle, result.traces, question="does governance stop exfil?")


class TestPublicationRendering(unittest.TestCase):
    def setUp(self) -> None:
        self.stored = _stored()

    def test_exact_claims_do_not_also_render_no_exact_claims(self) -> None:
        exact = [c for c in self.stored.publication["claims"] if c["kind"] == "exactly_replayable"]
        self.assertTrue(exact)  # this bundle DOES have an exact DENY claim
        html = render_publication(self.stored)
        self.assertNotIn("No exact claims", html)

    def test_methodology_shows_per_condition_config_hashes(self) -> None:
        html = render_publication(self.stored)
        self.assertNotIn("differ only in enforcement", html)
        for condition in self.stored.bundle["conditions"]:
            self.assertIn(str(condition["config_hash"]), html)


class TestChainCallIdCorrelation(unittest.TestCase):
    """The chain must pair the DENY decision with ITS OWN intent by call_id.

    Picking `first intent` + `first decision` independently rendered call A's
    call/lineage for a DENY that actually landed on call B in a multi-call
    trace (review r12)."""

    def _multi_call_trace(self) -> dict:
        return {
            "trace_id": "t_multi",
            "trial": {"condition_id": "governed_taint_floor"},
            "producer": {"provenance_fidelity": "sound_attribution"},
            "values": [
                {"value_id": "vA", "labels": ["clean"], "derived_from": []},
                {"value_id": "vB", "labels": ["untrusted"], "derived_from": []},
            ],
            "events": [
                {"seq": 0, "type": "tool_call_intent", "tool": "read_email",
                 "call_id": "call_A", "arg_bindings": {"folder": "vA"}},
                {"seq": 1, "type": "gate_decision", "call_id": "call_A",
                 "decision": {"verdict": "ALLOW", "driving_value_id": "vA",
                              "gate": "taint_floor"}},
                {"seq": 2, "type": "tool_call_intent", "tool": "send_money",
                 "call_id": "call_B", "arg_bindings": {"recipient": "vB"}},
                {"seq": 3, "type": "gate_decision", "call_id": "call_B",
                 "decision": {"verdict": "DENY", "driving_value_id": "vB",
                              "gate": "taint_floor"}},
            ],
        }

    def test_deny_on_second_call_shows_second_calls_intent_and_lineage(self) -> None:
        scenario = {"injection": {"text": "exfil"}, "inputs": {}}
        chain = _chain(self._multi_call_trace(), scenario)
        # the DENY landed on call_B (send_money), NOT the first intent (read_email)
        self.assertEqual(chain["gated_call"]["tool"], "send_money")
        self.assertEqual(chain["verdict"]["verdict"], "DENY")
        self.assertEqual(chain["verdict"]["driving_value_id"], "vB")
        provenance_ids = {v["value_id"] for v in chain["provenance"]}
        self.assertIn("vB", provenance_ids)
        self.assertNotIn("vA", provenance_ids)  # call A's value is not the driver

    def test_order_swapped_still_pairs_by_call_id_not_position(self) -> None:
        # put the DENY call FIRST in event order: correlation must still follow
        # driving/call_id, and here the first (and only) DENY is the sole decision
        trace = self._multi_call_trace()
        # flip which call denies: now call_A denies, call_B allows
        trace["events"][1]["decision"] = {"verdict": "DENY", "driving_value_id": "vA",
                                           "gate": "taint_floor"}
        trace["events"][3]["decision"] = {"verdict": "ALLOW", "driving_value_id": "vB",
                                          "gate": "taint_floor"}
        chain = _chain(trace, {"injection": {"text": "exfil"}, "inputs": {}})
        self.assertEqual(chain["gated_call"]["tool"], "read_email")  # call_A now
        self.assertEqual(chain["verdict"]["driving_value_id"], "vA")


class TestSharedEvidenceConditionResolver(unittest.TestCase):
    """The CLI and HTML share ONE condition resolver, and it does not silently
    show a strict counterfactual for an allowlist trace (review r13)."""

    def _bundle_with_two_enforcing(self):
        import copy
        scenario = support.banking_scenario()
        conditions = support.conditions()
        allow = copy.deepcopy(next(c for c in conditions if c["enforcement"] == "on"))
        allow["id"] = "governed_allowlist"
        conditions = [*conditions, allow]
        # a trace produced under governed_allowlist
        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        trace = run_trial(scenario, support.manifests(), allow, kernel,
                          run_id="r", seed="s000", repeat_index=0,
                          agent=ScriptedAgent(attack_rate=1.0)).trace
        trace["trial"]["condition_id"] = "governed_allowlist"
        bundle = {"conditions": conditions}
        return bundle, trace

    def test_own_enforcing_condition_wins_over_first(self) -> None:
        bundle, trace = self._bundle_with_two_enforcing()
        chosen = evidence_condition(bundle, trace, None)
        # NOT "governed" (the first enforcing) — the trace's OWN allowlist policy
        self.assertEqual(chosen["id"], "governed_allowlist")

    def test_explicit_policy_wins(self) -> None:
        bundle, trace = self._bundle_with_two_enforcing()
        self.assertEqual(evidence_condition(bundle, trace, "governed")["id"], "governed")

    def test_unknown_or_non_enforcing_policy_raises(self) -> None:
        bundle, trace = self._bundle_with_two_enforcing()
        with self.assertRaises(ValueError):
            evidence_condition(bundle, trace, "nope")
        bundle["conditions"][0]["id"]  # ungoverned is enforcement-off
        with self.assertRaises(ValueError):
            evidence_condition(bundle, trace, "ungoverned")


class TestTwinValidation(unittest.TestCase):
    """A governed twin must be the SAME case under an enforcing policy (r13)."""

    def _pair(self):
        scenario = support.banking_scenario()
        conditions = support.conditions()  # ungoverned(off) + governed(on)
        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        base = run_trial(scenario, support.manifests(), conditions[0], kernel,
                         run_id="r", seed="s000", repeat_index=0,
                         agent=ScriptedAgent(attack_rate=1.0)).trace
        twin = run_trial(scenario, support.manifests(), conditions[1], kernel,
                         run_id="r", seed="s000", repeat_index=0,
                         agent=ScriptedAgent(attack_rate=1.0)).trace
        bundle = {"conditions": conditions}
        return base, twin, bundle

    def test_matching_governed_twin_is_accepted(self) -> None:
        base, twin, bundle = self._pair()
        validate_twin(base, twin, bundle)  # same scenario/seed/repeat, enforcing → ok

    def test_twin_from_a_different_seed_is_rejected(self) -> None:
        base, twin, bundle = self._pair()
        twin["trial"]["seed"] = "s999"
        with self.assertRaises(ValueError) as ctx:
            validate_twin(base, twin, bundle)
        self.assertIn("seed", str(ctx.exception))

    def test_ungoverned_twin_is_rejected(self) -> None:
        base, _, bundle = self._pair()
        # use the ungoverned base itself as a "twin" — enforcement off → rejected
        with self.assertRaises(ValueError) as ctx:
            validate_twin(base, base, bundle)
        self.assertIn("enforcement-on", str(ctx.exception))


class TestEvidenceConditionSelection(unittest.TestCase):
    def test_multiple_governed_conditions_have_distinct_replay_links(self) -> None:
        stored = _stored()
        # add a second enforcing condition (rendering does not re-verify); the
        # page must offer a replay link per enforcing condition
        conditions = list(stored.bundle["conditions"])
        extra = copy.deepcopy(next(c for c in conditions if c["enforcement"] == "on"))
        extra["id"] = "governed_allowlist"
        conditions.append(extra)
        stored.bundle["conditions"] = conditions
        trace_id = next(iter(stored.traces))
        html = render_evidence(stored, trace_id)
        self.assertIn("policy=governed", html)
        self.assertIn("policy=governed_allowlist", html)


if __name__ == "__main__":
    unittest.main()
