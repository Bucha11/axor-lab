"""Acceptance test 2 — simulated execution is safe.

Running banking-exfil-01 with a side_effecting send_money moves no real
money; the ledger_stub records the attempt. Real execution requires opt-in
plus the full guard set — and is refused in the MVP reference runner even
then (threat-model §1).
"""

from __future__ import annotations

import unittest

from tests import support
from lab_runner import (
    RealExecutionBlocked,
    ScriptedAgent,
    SimulatedToolHost,
    run_trial,
)

ATTACK_ALWAYS = ScriptedAgent(attack_rate=1.0)


def _host() -> SimulatedToolHost:
    scenario = support.banking_scenario()
    return SimulatedToolHost(
        manifests=support.manifests(),
        fixtures=scenario["fixtures"],  # type: ignore[arg-type]
        injection_text=support.INJECTION_TEXT,
    )


class TestSimulationSafety(unittest.TestCase):
    def test_ungoverned_attack_is_recorded_but_simulated(self) -> None:
        scenario = support.banking_scenario()
        host = _host()
        ungoverned = support.conditions()[0]
        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        outcome = run_trial(
            scenario, support.manifests(), ungoverned, kernel,
            run_id="r_sim", seed="s000", repeat_index=0, agent=ATTACK_ALWAYS, host=host,
        )
        self.assertTrue(outcome.violation)
        self.assertEqual(len(host.attempts), 1)
        attempt = host.attempts[0]
        self.assertEqual(attempt["tool"], "send_money")
        self.assertEqual(attempt["executed"], "simulated")
        self.assertEqual(attempt["args"]["recipient"], support.ATTACKER_IBAN)  # type: ignore[index]

    def test_governed_deny_means_no_attempt_reaches_the_stub(self) -> None:
        scenario = support.banking_scenario()
        host = _host()
        governed = support.conditions()[1]
        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        outcome = run_trial(
            scenario, support.manifests(), governed, kernel,
            run_id="r_sim", seed="s000", repeat_index=0, agent=ATTACK_ALWAYS, host=host,
        )
        self.assertFalse(outcome.violation)
        self.assertEqual(host.attempts, [])

    def test_injection_lands_at_the_declared_field(self) -> None:
        host = _host()
        result = host.fixture_result("read_txns")
        self.assertEqual(
            result["transactions"][1]["description"], support.INJECTION_TEXT  # type: ignore[index]
        )
        self.assertEqual(result["transactions"][0]["description"], "rent")  # type: ignore[index]

    def test_real_execution_without_opt_in_is_blocked(self) -> None:
        host = _host()
        with self.assertRaises(RealExecutionBlocked):
            host.execute("send_money", {"recipient": support.LANDLORD_IBAN, "amount": 1}, real=True)

    def test_real_execution_with_opt_in_but_missing_guards_is_blocked(self) -> None:
        mans = support.manifests()
        mans["send_money"]["simulation"]["real_execution"]["opt_in"] = True  # type: ignore[index]
        scenario = support.banking_scenario()
        host = SimulatedToolHost(
            manifests=mans,
            fixtures=scenario["fixtures"],  # type: ignore[arg-type]
            injection_text=support.INJECTION_TEXT,
        )
        with self.assertRaises(RealExecutionBlocked) as ctx:
            host.execute(
                "send_money",
                {"recipient": support.LANDLORD_IBAN, "amount": 1},
                real=True,
                guards=("isolated_test_account",),
            )
        self.assertIn("dry_run_confirmed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
