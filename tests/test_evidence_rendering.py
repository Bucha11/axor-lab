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
