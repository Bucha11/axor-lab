"""The in-process assemble_and_gate path is bound like the HTTP gateway
(review r9, P0 — the r8 fix was applied to only one of the two endpoint paths).

assemble_and_gate used to take the concrete `item.args` independently of the
bindings, pass them straight to the kernel, and record only the bindings — the
exact laundering bypass the HTTP gateway had before r8. Both paths now share
`gated_args`: the gate decides on the bound value's decision_value, and a
conflicting concrete assertion fails closed.
"""

from __future__ import annotations

import unittest

from tests import support
from lab_endpoint import EmittedEvent, assemble_and_gate
from lab_endpoint.gating import GatingError
from lab_runner import replay_bundle, default_registry
from lab_contracts import content_hash

SAFE_IBAN = "DE00KNOWN0000000000"  # in the scenario's known_ibans allowlist


def _kernel():
    return support.kernel_registry().get(support.KERNEL_PINNED)


def _value(vid, decision_value, labels):
    return {"value_id": vid, "decision_value": decision_value, "preview": str(decision_value),
            "labels": labels, "sources": [{"kind": "external_read", "origin_ref": "o"}]}


class TestInstrumentedArgsBinding(unittest.TestCase):
    def _assemble(self, binding_value, binding_labels, asserted_args):
        scenario = support.banking_scenario()
        emitted = [
            EmittedEvent(type="tool_result", tool="read_txns",
                         values=[_value("v_r", binding_value, binding_labels),
                                 _value("v_amt", 1200, ["prompt_given"])]),
            EmittedEvent(type="tool_call_intent", tool="send_money",
                         arg_bindings={"recipient": "v_r", "amount": "v_amt"}, args=asserted_args),
        ]
        return assemble_and_gate(
            emitted, support.conditions()[1], support.manifests(), scenario["inputs"],
            _kernel(), run_id="r_ep", scenario_id="banking-exfil-01",
            fixtures=scenario.get("fixtures", {}), trusted_runtime=True,
        )

    def test_clean_binding_with_malicious_concrete_arg_fails_closed(self) -> None:
        # bind a clean allowlisted IBAN but assert the attacker IBAN → refused
        with self.assertRaises(GatingError):
            self._assemble(SAFE_IBAN, ["prompt_given"],
                           {"recipient": support.ATTACKER_IBAN, "amount": 1200})

    def test_honest_clean_binding_allows(self) -> None:
        trace = self._assemble(SAFE_IBAN, ["prompt_given"], {"recipient": SAFE_IBAN, "amount": 1200})
        decision = next(e for e in trace["events"] if e.get("type") == "gate_decision")
        self.assertEqual(decision["decision"]["verdict"], "ALLOW")

    def test_tainted_binding_denies_and_replays_identically(self) -> None:
        trace = self._assemble(support.ATTACKER_IBAN, ["untrusted_derived"],
                               {"recipient": support.ATTACKER_IBAN, "amount": 1200})
        decision = next(e for e in trace["events"] if e.get("type") == "gate_decision")
        self.assertEqual(decision["decision"]["verdict"], "DENY")
        # the gated value is recoverable from the ledger and replays to the SAME
        # verdict — no divergence between live decision and replay
        v_r = next(v for v in trace["values"] if v["value_id"] == "v_r")
        self.assertEqual(v_r["decision_value"], support.ATTACKER_IBAN)
        condition = support.conditions()[1]
        kernels = {k.version: k for k in default_registry((str(condition["kernel"]),)).kernels}
        bundle = {"conditions": [condition], "scenarios": [support.banking_scenario()],
                  "tool_manifests": list(support.manifests().values())}
        report = replay_bundle(bundle, {str(trace["trace_id"]): trace}, kernels)
        self.assertTrue(report.bit_identical)

    def test_fidelity_needs_trusted_runtime_not_a_bare_flag(self) -> None:
        # default (untrusted) assembly is heuristic even though the caller
        # supplied labels — fidelity is a runtime-trust claim, not a param
        scenario = support.banking_scenario()
        emitted = [EmittedEvent(type="tool_result", tool="read_txns",
                                values=[_value("v_r", SAFE_IBAN, ["prompt_given"])])]
        trace = assemble_and_gate(
            emitted, support.conditions()[1], support.manifests(), scenario["inputs"],
            _kernel(), run_id="r_ep", scenario_id="banking-exfil-01",
            fixtures=scenario.get("fixtures", {}),  # trusted_runtime defaults False
        )
        self.assertEqual(trace["producer"]["provenance_fidelity"], "heuristic_attribution")


if __name__ == "__main__":
    unittest.main()
