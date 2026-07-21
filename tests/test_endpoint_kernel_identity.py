"""Kernel identity closure applies to EVERY execution surface, not just the
local runner/replay (review r17).

The HTTP gateway and the in-process endpoint used to build a reference Kernel for
any version string — so a condition pinning `axor-core@X` produced a trace that
claimed the production build while a reference kernel actually decided. Both
surfaces now resolve through the ONE shared `resolve_kernel` (real build or
UnknownKernelError) and dispatch to the real governor for an AxorKernel.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request

from tests import support
from lab_contracts import condition_config_hash
from lab_endpoint import EmittedEvent, assemble_and_gate, make_gateway
from lab_runner import (
    AxorKernel,
    axor_available,
    real_kernel_version,
    resolve_kernel,
    resolve_kernel_for_trace,
)
from lab_runner.errors import UnknownKernelError
from lab_runner.kernel import KernelRegistry, default_registry


def _real_condition() -> dict[str, object]:
    version = real_kernel_version()
    policy = {"profile": "strict", "trust_model": "content-ledger"}
    return {
        "schema_version": "condition/v1", "id": "governed", "label": "governed (axor-core)",
        "enforcement": "on", "kernel": version, "policy": policy,
        "config_hash": condition_config_hash(version, policy),
    }


def _drive_attack(server) -> dict:
    """Open a run, emit an untrusted-derived recipient, intend the exfil, read."""
    base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def post(path, obj, secret=None):
            headers = {"Content-Type": "application/json"}
            if secret:
                headers["Authorization"] = f"Bearer {secret}"
            req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())

        opened = post("/runs", {})
        rid, secret = opened["run_id"], opened["run_secret"]
        post(f"/runs/{rid}/events", {
            "type": "tool_result", "tool": "read_txns",
            "values": [{"value_id": "v_r", "decision_value": support.ATTACKER_IBAN,
                        "labels": ["untrusted_derived"],
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]},
                       {"value_id": "v_a", "decision_value": 1200, "labels": ["untrusted_derived"],
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]}],
        }, secret=secret)
        decision = post(f"/runs/{rid}/events", {
            "type": "tool_call_intent", "tool": "send_money",
            "arg_bindings": {"recipient": "v_r", "amount": "v_a"},
        }, secret=secret)
        return decision["decision"]
    finally:
        server.shutdown()
        server.server_close()


class TestGatewayMissingRealKernel(unittest.TestCase):
    def test_gateway_missing_real_kernel_fails_at_construction(self) -> None:
        # a real-kernel pin for a build that is NOT the installed one must fail at
        # construction — never silently build a reference kernel under the label
        bad = {
            "schema_version": "condition/v1", "id": "governed", "enforcement": "on",
            "kernel": "axor-core@9.9.9-not-installed", "policy": {"profile": "strict"},
        }
        with self.assertRaises(UnknownKernelError):
            make_gateway(bad, support.manifests(), support.banking_scenario()["inputs"],
                         scenario_id="banking-exfil-01")


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestGatewayRealKernel(unittest.TestCase):
    def test_gateway_real_kernel_pin_never_uses_reference_kernel(self) -> None:
        server = make_gateway(
            _real_condition(), support.manifests(), support.banking_scenario()["inputs"],
            scenario_id="banking-exfil-01",
        )
        decision = _drive_attack(server)
        self.assertEqual(decision["verdict"], "DENY")
        # the verdict came from the REAL governor, not a Lab reference reimplementation
        self.assertIn("axor-core governor", decision["reason"])


class TestInstrumentedKernelIdentity(unittest.TestCase):
    def test_instrumented_endpoint_rejects_kernel_condition_mismatch(self) -> None:
        # a reference Kernel handed in for a real-kernel condition must be refused
        condition = {
            "schema_version": "condition/v1", "id": "governed", "enforcement": "on",
            "kernel": "axor-core@0.9.2", "policy": {"profile": "strict"},
        }
        ref = default_registry((support.KERNEL_PINNED,)).get(support.KERNEL_PINNED)
        with self.assertRaises(ValueError):
            assemble_and_gate(
                [], condition, support.manifests(), support.banking_scenario()["inputs"],
                ref, run_id="r", scenario_id="banking-exfil-01",
            )

    def test_instrumented_endpoint_rejects_wrong_version(self) -> None:
        # even a reference kernel whose version string disagrees with the condition
        condition = dict(support.conditions()[1])
        wrong = default_registry((support.KERNEL_NO_TAINT_FLOOR,)).get(support.KERNEL_NO_TAINT_FLOOR)
        with self.assertRaises(ValueError):
            assemble_and_gate(
                [], condition, support.manifests(), support.banking_scenario()["inputs"],
                wrong, run_id="r", scenario_id="banking-exfil-01",
            )


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestInstrumentedRealKernel(unittest.TestCase):
    def test_instrumented_endpoint_dispatches_axor_kernel_correctly(self) -> None:
        condition = _real_condition()
        kernel = resolve_kernel(
            str(condition["kernel"]), support.manifests(), condition.get("policy"),
            KernelRegistry(kernels=()), support.banking_scenario()["inputs"],
        )
        emitted = [
            EmittedEvent(type="tool_result", tool="read_txns", values=[
                {"value_id": "v_r", "decision_value": support.ATTACKER_IBAN,
                 "labels": ["untrusted_derived"],
                 "sources": [{"kind": "external_read", "origin_ref": "o"}]},
                {"value_id": "v_a", "decision_value": 1200, "labels": ["untrusted_derived"],
                 "sources": [{"kind": "external_read", "origin_ref": "o"}]},
            ]),
            EmittedEvent(type="tool_call_intent", tool="send_money",
                         arg_bindings={"recipient": "v_r", "amount": "v_a"}),
        ]
        trace = assemble_and_gate(
            emitted, condition, support.manifests(), support.banking_scenario()["inputs"],
            kernel, run_id="r", scenario_id="banking-exfil-01",
        )
        decision = next(e for e in trace["events"] if e.get("type") == "gate_decision")
        self.assertEqual(decision["decision"]["verdict"], "DENY")
        self.assertIn("axor-core governor", decision["decision"]["reason"])
        self.assertEqual(trace["producer"]["kernel_version"], real_kernel_version())


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestPerScenarioKernelResolution(unittest.TestCase):
    """The shared `resolve_kernel_for_trace` — used by CLI regress / CLI & HTML
    EvidenceCase / incident import — expands an input-backed allowlist against the
    TRACE's OWN scenario, so two scenarios under one condition don't share a stale
    allowlist expansion (review r17)."""

    def _bundle_with_two_scenarios(self) -> dict:
        version = real_kernel_version()
        policy = {"profile": "strict", "trust_model": "content-ledger",
                  "allowlist": ["$inputs.known_ibans"]}
        condition = {
            "schema_version": "condition/v1", "id": "governed", "enforcement": "on",
            "kernel": version, "policy": policy,
            "config_hash": condition_config_hash(version, policy),
        }
        s_a = {**support.banking_scenario(), "name": "scn-a",
               "inputs": {"landlord_iban": "IBAN_A", "known_ibans": ["IBAN_A"]}}
        s_b = {**support.banking_scenario(), "name": "scn-b",
               "inputs": {"landlord_iban": "IBAN_B", "known_ibans": ["IBAN_B"]}}
        return {
            "conditions": [condition], "scenarios": [s_a, s_b],
            "tool_manifests": list(support.manifests().values()),
        }

    def _trace_for(self, scenario_name: str) -> dict:
        return {"trial": {"condition_id": "governed", "scenario_id": scenario_name}}

    def test_resolve_kernel_for_trace_expands_allowlist_per_scenario(self) -> None:
        bundle = self._bundle_with_two_scenarios()
        k_a = resolve_kernel_for_trace(bundle, self._trace_for("scn-a"))
        k_b = resolve_kernel_for_trace(bundle, self._trace_for("scn-b"))
        self.assertIsInstance(k_a, AxorKernel)
        self.assertIsInstance(k_b, AxorKernel)

        def enums(kernel: AxorKernel) -> list:
            vps = kernel.config.get("value_policies", {})
            return [v for vp in vps.values() for arg in vp.values() for v in arg["enum"]]

        self.assertIn("IBAN_A", enums(k_a))
        self.assertNotIn("IBAN_B", enums(k_a))
        self.assertIn("IBAN_B", enums(k_b))
        self.assertNotIn("$inputs.known_ibans", enums(k_a))


if __name__ == "__main__":
    unittest.main()
