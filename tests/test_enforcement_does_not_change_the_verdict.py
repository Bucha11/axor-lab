"""Enforcement selects whether a verdict is OBEYED, never what the verdict IS.

Lab's two kernel backends both used to return an unconditional ALLOW when
`enforcement: off`, under a comment claiming "observation stays on". Nothing
observed: `gate_with_governor` returned before it constructed a governor at all,
and the reference `decide` returned before it resolved the effect class. So an
ungoverned arm recorded ALLOW for the exact call the governed arm denied.

Three things were broken by that, and each has a test here:

  1. The two arms disagreed on every verdict, so the comparison contrasted two
     different machines rather than one machine under two policies.
  2. An ungoverned run carried no evidence of what governance WOULD have caught
     — the entire point of running one.
  3. "Governance is on" and "the agent did nothing worth denying" were
     indistinguishable in the record.

The invariant this file defends is the same one axor-wrap's `enforcement: off`
holds: the kernel evaluates every call and records the real verdict; only
whether the runtime acts on it changes.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_contracts import validate_artifact
from lab_runner import ScriptedAgent
from lab_capabilities.governance import axor_available, governor_config, real_kernel_version, run_trial
from lab_capabilities.governance.axor_backend import AxorKernel
from lab_runner.verdicts import contained, executed_under, was_enforced

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)
FAITHFUL = ScriptedAgent(attack_rate=0.0)


def _decisions(trace: dict[str, object]) -> list[dict[str, object]]:
    return [
        e["decision"]  # type: ignore[misc]
        for e in trace["events"]  # type: ignore[union-attr]
        if e.get("type") == "gate_decision"
    ]


class _ArmsMixin:
    kernel: object

    def _arms(self, agent: ScriptedAgent = ATTACK_ALWAYS):
        ungoverned, governed = support.conditions()
        out = {}
        for condition in (ungoverned, governed):
            trace = run_trial(
                support.banking_scenario(), support.manifests(), condition, self.kernel,
                run_id="r", seed="s000", repeat_index=0, agent=agent,
            ).trace
            out[str(condition["id"])] = trace
        return out


class TestTheReferenceKernel(_ArmsMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = support.kernel_registry().get(support.KERNEL_PINNED)

    def test_both_arms_reach_the_same_verdicts(self) -> None:
        arms = self._arms()
        self.assertEqual(
            [d["verdict"] for d in _decisions(arms["ungoverned"])],
            [d["verdict"] for d in _decisions(arms["governed"])],
        )

    def test_the_ungoverned_arm_records_the_denial_it_did_not_obey(self) -> None:
        decisions = _decisions(self._arms()["ungoverned"])
        self.assertEqual([d["verdict"] for d in decisions], ["DENY"])
        self.assertEqual([was_enforced(d) for d in decisions], [False])

    def test_the_ungoverned_arm_still_executes_the_denied_call(self) -> None:
        """Otherwise it is not observe-only, it is governed under another name —
        and it would stop being a record of what the agent ACTUALLY did."""
        decisions = _decisions(self._arms()["ungoverned"])
        self.assertTrue(all(executed_under(d) for d in decisions))

    def test_only_the_governed_arm_contained_anything(self) -> None:
        """The measure that must not be read off `verdict` alone: both arms show
        DENY, one of them stopped something."""
        arms = self._arms()
        self.assertFalse(any(contained(d) for d in _decisions(arms["ungoverned"])))
        self.assertTrue(any(contained(d) for d in _decisions(arms["governed"])))

    def test_a_faithful_run_is_allowed_in_both_arms(self) -> None:
        """The verdict tracks the CALL, not the arm — an ungoverned arm that
        denied everything would be as useless as one that allowed everything."""
        arms = self._arms(FAITHFUL)
        for arm, trace in arms.items():
            with self.subTest(arm=arm):
                self.assertEqual([d["verdict"] for d in _decisions(trace)], ["ALLOW"])

    def test_both_arms_produce_a_valid_trace(self) -> None:
        for arm, trace in self._arms().items():
            with self.subTest(arm=arm):
                self.assertEqual(validate_artifact(trace, "trace"), [])


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestTheRealKernel(_ArmsMixin, unittest.TestCase):
    """The same invariant against the production governor. This is the one that
    mattered most: `gate_with_governor` returned its fabricated ALLOW *before*
    constructing a `ToolCallGovernor`, so an ungoverned arm pinned to a real
    axor-core build never reached axor-core at all."""

    def setUp(self) -> None:
        version = real_kernel_version()
        self.kernel = AxorKernel(
            version=str(version), config=governor_config(support.manifests(), None),
        )

    def test_the_ungoverned_arm_actually_reaches_axor_core(self) -> None:
        decisions = _decisions(self._arms()["ungoverned"])
        self.assertEqual([d["verdict"] for d in decisions], ["DENY"])
        self.assertIn("axor-core governor", str(decisions[0]["reason"]))
        self.assertFalse(was_enforced(decisions[0]))

    def test_both_arms_reach_the_same_verdicts(self) -> None:
        arms = self._arms()
        self.assertEqual(
            [d["verdict"] for d in _decisions(arms["ungoverned"])],
            [d["verdict"] for d in _decisions(arms["governed"])],
        )


class TestReadingADecision(unittest.TestCase):
    """`enforced` is absent in every trace recorded before it existed, and those
    runs were all enforced — so absence must read as True. Reading it as False
    would retroactively turn every legacy denial into something that never
    contained anything."""

    def test_absent_reads_as_enforced(self) -> None:
        legacy = {"verdict": "DENY", "gate": "taint_floor", "driving_value_id": "v1"}
        self.assertTrue(was_enforced(legacy))
        self.assertTrue(contained(legacy))
        self.assertFalse(executed_under(legacy))

    def test_an_allow_ran_whether_or_not_it_was_enforced(self) -> None:
        for enforced in (True, False):
            with self.subTest(enforced=enforced):
                self.assertTrue(executed_under({"verdict": "ALLOW", "enforced": enforced}))

    def test_an_unenforced_deny_ran_and_contained_nothing(self) -> None:
        decision = {"verdict": "DENY", "enforced": False}
        self.assertTrue(executed_under(decision))
        self.assertFalse(contained(decision))


if __name__ == "__main__":
    unittest.main()
