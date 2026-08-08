"""Budget — cost and token efficiency.

Its job in the launch set is to exercise the METRICS layer: it is nothing but
metrics. That also makes it the suite that most needs the platform's "an
unmeasured metric is absent, never 0" rule, because a scripted agent has no
cost to report and a budget invariant over it must error rather than pass.
"""

from __future__ import annotations

from lab_runner.loop import AgentProgram, Finish, LoopOutcome, ScriptedProgram, ToolCall

from ..manifest import ResolvedSuite
from ..sdk import BaseSuite

READ_TOOL = "read_txns"


class BudgetSuite(BaseSuite):
    id = "budget"

    def manifest(self) -> dict[str, object]:
        return {
            "schema_version": "suite/v1",
            "id": self.id,
            "name": "Budget",
            "version": "1.0",
            "description": "Cost and token efficiency",
            "origin": "built_in",
            "agents": [{"ref": "scripted@0.6", "model": "claude-opus-4-8"}],
            "topology": {"kind": "single"},
            "scenarios": [{
                "schema_version": "scenario/v1",
                "name": "budget-spend-summary-01",
                "task": "Summarize this week's spending.",
                "inputs": {"week": "2026-W22"},
                "tools": [{"$ref": READ_TOOL}],
                "fixtures": {READ_TOOL: {"result": {"transactions": [
                    {"description": "rent", "amount": -1200},
                    {"description": "coffee", "amount": -34},
                ]}}},
                "task_success": {"event": "tool_call", "tool": READ_TOOL},
            }],
            "environment": {
                "tools": [_read_manifest()],
                "simulation": {"enabled": True, "strict_manifest": True},
            },
            "execution": {"strategy": "matrix", "repeats": 5, "seed_policy": "per_repeat",
                          "budgets": {"max_usd": 5.0}},
            "evaluation": {
                # A suite-declared evaluator, so `evaluator_outcome` has
                # something real to resolve. Its result comes from this suite's
                # own `metrics_for` hook — the longest path in the platform
                # (suite code -> trial.metrics -> invariant) and the one that
                # silently broke over the wire when `collect_suite_run` did not
                # call the hook at all.
                "evaluators": [
                    {"id": "read_count", "kind": "suite_hook",
                     "hook": "metrics_for", "produces": "reads"},
                ],
                "metrics": [
                    {"name": "task_success", "label": "Task Success", "kind": "boolean",
                     "source": "trial_metric", "from": "task_success",
                     "direction": "higher_is_better"},
                    {"name": "duration_ms", "label": "Latency", "kind": "duration_ms",
                     "source": "trial_metric", "from": "duration_ms", "unit": "ms",
                     "direction": "lower_is_better"},
                    {"name": "tool_calls", "label": "Tool Calls", "kind": "count",
                     "source": "trial_metric", "from": "tool_calls",
                     "direction": "lower_is_better"},
                ],
                "aggregations": [
                    {"metric": "task_success", "fn": "rate", "unit_of_analysis": "trial",
                     "interval": "wilson"},
                    {"metric": "duration_ms", "fn": "mean", "unit_of_analysis": "trial"},
                    {"metric": "tool_calls", "fn": "sum", "unit_of_analysis": "run"},
                ],
            },
            "artifact": {"include_traces": True,
                         "sections": ["overview", "metrics", "scenarios", "failures"]},
            # NOT pinned: cost_usd. This suite used to ship
            # `metric_threshold cost_usd lt 0.05`, and NOTHING in the repo can
            # measure it — the model backend that priced a run was deleted, and
            # a scripted agent calls no provider. The invariant therefore
            # errored on every single run of the launch suite, which is the
            # correct outcome for an absent metric (an unmeasured cost is not a
            # cheap one) and the wrong thing for a built-in to ship: a suite
            # whose purpose is to prove the metrics layer cannot be permanently
            # unevaluable. The honesty property it was demonstrating is pinned
            # in tests/test_platform_slice_e2e.py instead, where an absent
            # metric is asserted to error. The pin comes back with a backend
            # that actually bills.
            "regressions": [{
                "schema_version": "regression/v1",
                "id": "RG-budget-reads",
                "name": "A trial reads the ledger at most twice",
                "rule": {"kind": "metric_threshold", "metric": "reads",
                         "op": "lte", "value": READ_BUDGET},
                "expectation": ("Summarizing one week must not require more than two "
                                "reads. `reads` is this suite's OWN metric, so this "
                                "also pins that a suite-defined metric reaches a "
                                "regression rule."),
            }, {
                "schema_version": "regression/v1",
                "id": "RG-budget-read-count",
                "name": "Summarizing one week takes exactly one read",
                "rule": {"kind": "evaluator_outcome", "evaluator": "read_count",
                         "expect": 1},
                "expectation": ("Exactly one read, compared by canonical "
                                "equality \u2014 so 1 and True are not the "
                                "same answer."),
            }, {
                "schema_version": "regression/v1",
                "id": "RG-budget-latency",
                "name": "Trial latency stays under 10s",
                "rule": {"kind": "metric_threshold", "metric": "duration_ms",
                         "aggregate": "p95", "op": "lt", "value": 10000},
                "expectation": "p95 trial latency must stay below 10 seconds.",
            }],
            "tags": ["cost", "efficiency"],
        }

    def program_for(
        self,
        scenario: dict[str, object],
        seed: str,
        resolved: ResolvedSuite,
    ) -> AgentProgram:
        return ScriptedProgram([ToolCall(READ_TOOL, {}), Finish("summary")])

    def metrics_for(
        self, outcome: LoopOutcome, scenario: dict[str, object]
    ) -> dict[str, object]:
        """A suite-defined metric: how many transactions the agent had to read.

        Note what is NOT here: cost_usd and tokens. A scripted agent calls no
        provider, so reporting them would be inventing measurements.
        """
        results = [e for e in outcome.trace["events"] if e.get("type") == "tool_result"]
        return {"reads": len(results)}

    def evidence_for(
        self, outcome: LoopOutcome, trial: dict[str, object], scenario: dict[str, object]
    ) -> list[dict[str, object]]:
        """A `budget_overflow` case for a trial that read more than it should.

        A NON-GOVERNANCE EvidenceCase — no injection, no gate, no verdict. The
        only builder this repo had was the governance chain, so every other kind
        of investigation was unrepresentable in code while being perfectly
        representable in the schema.

        `threshold_case` returns None when `reads` was never measured, not just
        when it was within bounds: a case asserting an overrun nobody measured
        is a fabricated finding.
        """
        from lab_runner.cases import case_id_for, threshold_case

        case = threshold_case(
            case_id=case_id_for(trial, "budget_overflow"),
            kind="budget_overflow", metric="reads", limit=READ_BUDGET,
            trial=trial, trace=outcome.trace, run_id=str(trial.get("execution_id", "")),
        )
        return [case] if case is not None else []


# What "too many reads" means for this suite, in one place: the invariant the
# manifest pins and the case the extractor raises must not disagree about the
# limit, or a run can fail its regression and produce no evidence for why.
READ_BUDGET = 2


def _read_manifest() -> dict[str, object]:
    return {
        "schema_version": "tool-manifest/v1",
        "id": READ_TOOL,
        "args_schema": {"type": "object", "properties": {}, "required": []},
        "result_schema": {"type": "object", "properties": {
            "transactions": {"type": "array", "items": {"type": "object"}}},
            "required": ["transactions"]},
        "effect": {"default_class": "READ", "driving_args": []},
        "untrusted_fields": ["result.transactions[].description"],
        "side_effecting": False,
        "reset": {"strategy": "fixture", "fixture_ref": READ_TOOL},
    }
