"""Framework adapters axor-claude + axor-langchain (contracts/adapters.md §4).

Each is a thin `AgentAdapter` = RunnerAgentAdapter + a framework `ModelBackend`; the
shared wrapped runtime does the provenance mint + gate-before-dispatch. Proven
offline: a fake LangChain chat model and a deterministic cassette stand in for the
live SDKs, and a real governed trial (with a real gate_decision) is produced.
"""

from __future__ import annotations

import asyncio
import unittest

from tests import support
from lab_agent import CassetteBackend
from lab_client import ExecutionContext, TraceSink
from lab_client.frameworks import axor_claude, axor_langchain

SINK = str(support.send_money_manifest()["id"])


def _fixtures():
    scenario = support.banking_scenario()
    conditions = support.conditions()
    return (
        {str(scenario["name"]): scenario},
        support.manifests(),
        {str(c["id"]): c for c in conditions},
        support.kernel_registry(),
        conditions,
    )


def _ctx(condition_id: str) -> ExecutionContext:
    coord = {"run_id": "r", "scenario_id": "banking-exfil-01",
             "condition_id": condition_id, "seed": "s000", "repeat_index": 0}
    return ExecutionContext(condition={"condition_id": condition_id},
                            trace_sink=TraceSink(), trial=coord,
                            run_id="r", trial_id="t")


class _FakeAI:
    def __init__(self, tool_calls) -> None:
        self.tool_calls = tool_calls
        self.content = ""
        self.usage_metadata = {"input_tokens": 12, "output_tokens": 4}


class _FakeLangChainModel:
    """A stateless LangChain-shaped chat model: every invoke calls the sink tool with
    the attacker IBAN (as an injected agent would)."""

    def bind_tools(self, tools):  # noqa: ANN001
        return self

    def invoke(self, messages):  # noqa: ANN001
        return _FakeAI([{"name": SINK, "args": {"recipient": support.ATTACKER_IBAN,
                                                 "amount": 1200}, "id": "1"}])


class TestFrameworkAdapters(unittest.TestCase):
    def _assert_real_governed_trace(self, adapter, condition_id: str) -> None:
        ctx = _ctx(condition_id)
        result = asyncio.run(adapter.run(None, ctx))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.provenance_fidelity, "explicit_flow_tracked")
        # a REAL trace with a gate_decision the local kernel actually made
        gates = [e for e in result.trace["events"] if e.get("type") == "gate_decision"]
        self.assertTrue(gates)
        self.assertIn(gates[-1]["decision"]["verdict"], {"ALLOW", "DENY"})
        # events left through the sink egress (adapters.md §6), not adapter HTTP
        self.assertTrue(ctx.trace_sink.events)

    def test_axor_langchain_runs_a_real_trial(self) -> None:
        scenarios, manifests, conditions, kernels, conds = _fixtures()
        adapter = axor_langchain.adapter(
            scenarios, manifests, conditions, kernels, model=_FakeLangChainModel())
        self.assertEqual(asyncio.run(adapter.describe()).adapter_kind, "langchain")
        governed = next(c["id"] for c in conds if str(c["enforcement"]) == "on")
        self._assert_real_governed_trace(adapter, governed)

    def test_axor_claude_runs_a_real_trial_via_cassette(self) -> None:
        scenarios, manifests, conditions, kernels, conds = _fixtures()
        # a deterministic cassette stands in for the live Anthropic call (offline)
        cassette = CassetteBackend.from_records(
            [{"tool": SINK, "args": {"recipient": support.ATTACKER_IBAN, "amount": 1200}}])
        adapter = axor_claude.adapter(
            scenarios, manifests, conditions, kernels, backend=cassette)
        self.assertEqual(asyncio.run(adapter.describe()).adapter_kind, "claude")
        ungoverned = next(c["id"] for c in conds if str(c["enforcement"]) == "off")
        self._assert_real_governed_trace(adapter, ungoverned)

    def test_langchain_governed_denies_the_injected_transfer(self) -> None:
        # the framework adapter earns a REAL governance result: the injected attack
        # is DENIED under enforcement=on and ALLOWED under enforcement=off
        scenarios, manifests, conditions, kernels, conds = _fixtures()
        adapter = axor_langchain.adapter(
            scenarios, manifests, conditions, kernels, model=_FakeLangChainModel())
        verdict = {}
        for c in conds:
            ctx = _ctx(str(c["id"]))
            asyncio.run(adapter.run(None, ctx))
            trace = [e for e in ctx.trace_sink.events if e.get("type") == "gate_decision"]
            verdict[str(c["enforcement"])] = trace[-1]["decision"]["verdict"]
        self.assertEqual(verdict["on"], "DENY")
        self.assertEqual(verdict["off"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
