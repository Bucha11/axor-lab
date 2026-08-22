"""Sensitive labels propagate through model_extraction (review round 6, P0).

The redaction of a sensitive SOURCE value was pointless if the model could copy
the secret into a sink argument: the derived model_extraction value must inherit
the whole security-label lattice — untrusted AND sensitive — and a derived value
that inherits sensitive must be redacted (masked preview, no raw decision_value),
so the secret never reaches the trace / bundle / EvidenceCase / server.

This behavior now lives in the wrap engine (axor-wrap), which builds the trace's
value ledger — Lab's own `ValueLedger` is gone. So the propagation is asserted
against a REAL wrap-built governed trace rather than a Lab-side ledger object.
"""

from __future__ import annotations

import json
import unittest

from tests import support
from lab_runner import ScriptedAgent
from lab_capabilities.governance import run_trial

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)
SECRET = "sk-secret-abc-123"


def _sensitive_trace() -> dict[str, object]:
    """A real governed attack trace whose untrusted read field is declared
    sensitive and carries a distinctive SECRET; the model copies content derived
    from it into the sink recipient."""
    scenario = support.deep(support.banking_scenario())
    scenario["fixtures"]["read_txns"]["result"]["transactions"][0]["description"] = SECRET
    mans = support.manifests()
    mans["read_txns"]["sensitive_fields"] = ["result.transactions[].description"]
    return run_trial(
        scenario, mans, support.conditions()[1],
        support.kernel_registry().get(support.KERNEL_PINNED),
        run_id="r", seed="s0", repeat_index=0, agent=ATTACK_ALWAYS,
    ).trace


def _default_trace() -> dict[str, object]:
    """A real governed attack trace with NO sensitive fields — the ordinary
    banking slice, whose untrusted-derived recipient is not sensitive."""
    return run_trial(
        support.banking_scenario(), support.manifests(), support.conditions()[1],
        support.kernel_registry().get(support.KERNEL_PINNED),
        run_id="r", seed="s0", repeat_index=0, agent=ATTACK_ALWAYS,
    ).trace


def _model_extractions(trace: dict[str, object]) -> list[dict[str, object]]:
    return [v for v in trace["values"] if "model_extraction" in v.get("transformations", [])]


class TestSensitivePropagation(unittest.TestCase):
    def test_derived_value_inherits_sensitive_and_is_redacted(self) -> None:
        # the model copies content derived from the sensitive read into the sink
        # recipient: the derived model_extraction value must inherit sensitive and
        # be redacted, not keep a raw copy of the secret.
        derived = _model_extractions(_sensitive_trace())
        self.assertTrue(derived)
        for v in derived:
            self.assertIn("untrusted_derived", v["labels"])
            self.assertIn("sensitive", v["labels"])   # inherited, not dropped
            self.assertEqual(v["preview"], "[redacted]")
            self.assertNotIn("decision_value", v)      # no raw value stored

    def test_the_raw_secret_appears_nowhere_in_the_serialized_trace(self) -> None:
        blob = json.dumps(_sensitive_trace()["values"])
        self.assertNotIn(SECRET, blob)  # source AND derived both redacted

    def test_non_sensitive_untrusted_derived_is_not_redacted(self) -> None:
        # regression: the normal banking slice recipient (untrusted, NOT sensitive)
        # must keep its typed decision_value so replay stays exact, and must NOT be
        # spuriously marked sensitive
        derived = _model_extractions(_default_trace())
        self.assertTrue(derived)
        for v in derived:
            self.assertIn("untrusted_derived", v["labels"])
            self.assertNotIn("sensitive", v["labels"])
            self.assertNotEqual(v["preview"], "[redacted]")
            self.assertIn("decision_value", v)


# Two tests were deleted here — both exercised ONLY Lab's deleted `ValueLedger`
# object, which has no live equivalent:
#
#   - `test_clean_model_output_stays_clean` minted a model_extraction over a lone
#     trusted constant and asserted the join added neither label. The banking
#     slice produces no clean (all-trusted-context) model_extraction, so there is
#     no wrap-built trace to assert it against; the "sensitive is not spuriously
#     added" half of no-over-taint is preserved by
#     `test_non_sensitive_untrusted_derived_is_not_redacted` above.
#   - `test_real_kernel_registration_reads_runtime_value_not_serialized` asserted
#     the ledger's in-memory `runtime_value(...)` API (and that reading
#     `decision_value` off a redacted value KeyErrors). That raw-value handoff to
#     the kernel is now internal to axor-wrap; its observable guarantee — the
#     secret never enters the serialized trace while the governed run still gates
#     correctly — is covered by the two tests above and by the governed DENY that
#     `test_runner_correctness` / `test_replay_regression_robustness` exercise.


if __name__ == "__main__":
    unittest.main()
