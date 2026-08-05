"""Replay capability model (review r15).

Not every trace can be replayed exactly. A REDACTED sensitive value that a
decision turned on (a bound gated arg, or an untrusted source the real governor
would re-register) leaves replay with only a hash sentinel — so the replay is
`redacted_input_unavailable`, never `match`, and an EvidenceCase over it does
not claim `exactly_replayable`. And a fail-closed decision's typed
`driving_unresolved` reason is part of the replay-comparable core.
"""

from __future__ import annotations

import copy
import unittest

from lab_runner import ScriptedAgent
from lab_capabilities.governance import REPLAY_MATCH, REPLAY_REDACTED_INPUT_UNAVAILABLE, default_registry, replay_trace_status, run_trial
from lab_capabilities.governance.evidence import build_evidence_case
from lab_capabilities.governance.replay import _verdict_core
from tests import support


def _governed_trace() -> dict:
    return run_trial(
        support.banking_scenario(), support.manifests(), support.conditions()[1],
        support.kernel_registry().get(support.KERNEL_PINNED),
        run_id="r", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
    ).trace


def _redact_bound_recipient(trace: dict) -> dict:
    """Redact the value bound to the sink's recipient: strip decision_value and
    mark it sensitive, keeping its canonical_value_hash (a real redacted value)."""
    trace = copy.deepcopy(trace)
    bound = set()
    for e in trace["events"]:
        if e.get("type") == "tool_call_intent":
            bound |= set(e.get("arg_bindings", {}).values())
    for v in trace["values"]:
        if v["value_id"] in bound and "decision_value" in v:
            del v["decision_value"]
            v["labels"] = sorted(set(v.get("labels", [])) | {"sensitive"})
            v.setdefault("canonical_value_hash", "sha256:" + "0" * 64)
            break
    return trace


class TestRedactedInputUnavailable(unittest.TestCase):
    def setUp(self) -> None:
        self.condition = support.conditions()[1]
        self.kernel = default_registry((str(self.condition["kernel"]),)).get(
            str(self.condition["kernel"])
        )
        self.inputs = support.banking_scenario().get("inputs", {})

    def test_clean_trace_replays_match(self) -> None:
        trace = _governed_trace()
        _, status = replay_trace_status(
            trace, self.condition, self.kernel, support.manifests(), self.inputs
        )
        self.assertEqual(status, REPLAY_MATCH)

    def test_redacted_bound_value_is_replay_unavailable_not_match(self) -> None:
        trace = _redact_bound_recipient(_governed_trace())
        _, status = replay_trace_status(
            trace, self.condition, self.kernel, support.manifests(), self.inputs
        )
        self.assertEqual(status, REPLAY_REDACTED_INPUT_UNAVAILABLE)

    def test_evidence_case_does_not_claim_exact_when_replay_unavailable(self) -> None:
        trace = _redact_bound_recipient(_governed_trace())
        case = build_evidence_case(
            trace, support.banking_scenario(), self.condition, self.kernel, support.manifests()
        )
        cf = case["modes"]["counterfactual_policy_replay"]
        self.assertEqual(cf["claim_kind"], "not_exactly_replayable")
        self.assertEqual(cf["replay_status"], REPLAY_REDACTED_INPUT_UNAVAILABLE)

    def test_a_redacted_value_is_unavailable_in_the_ungoverned_arm_too(self) -> None:
        """This was exempt while the observe-only path returned an unconditional
        ALLOW without inspecting its arguments: a redacted argument could not
        change a verdict that ignored arguments, so replay was trivially exact.

        That path is gone. The ungoverned arm now runs the same decision the
        governed arm runs, so a redacted bound value is exactly as load-bearing
        here — and claiming exact replay over a hash sentinel would be claiming
        to have reproduced a verdict that turned on a value replay never had."""
        ungoverned = support.conditions()[0]
        kernel = default_registry((str(ungoverned["kernel"]),)).get(str(ungoverned["kernel"]))
        clean = run_trial(
            support.banking_scenario(), support.manifests(), ungoverned, kernel,
            run_id="r", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
        ).trace
        _, clean_status = replay_trace_status(
            clean, ungoverned, kernel, support.manifests(), self.inputs
        )
        self.assertEqual(clean_status, REPLAY_MATCH, "unredacted, it replays exactly")

        _, status = replay_trace_status(
            _redact_bound_recipient(clean), ungoverned, kernel,
            support.manifests(), self.inputs,
        )
        self.assertEqual(status, REPLAY_REDACTED_INPUT_UNAVAILABLE)


class TestDrivingUnresolvedInCore(unittest.TestCase):
    def test_driving_unresolved_kind_and_arg_are_part_of_replay_core(self) -> None:
        base = {"verdict": "DENY", "gate": "taint_floor", "driving_value_id": None}
        no_args = _verdict_core({**base, "driving_unresolved": {"kind": "no_driving_args"}})
        unresolved = _verdict_core(
            {**base, "driving_unresolved": {"kind": "unresolved_argument", "arg": "body"}}
        )
        # two DIFFERENT fail-closed reasons must not compare equal in the core
        self.assertIn("driving_unresolved", no_args)
        self.assertNotEqual(no_args, unresolved)

    def test_a_real_driving_value_keeps_the_core_minimal(self) -> None:
        core = _verdict_core({"verdict": "ALLOW", "gate": "taint_floor", "driving_value_id": "v_r"})
        self.assertNotIn("driving_unresolved", core)


if __name__ == "__main__":
    unittest.main()
