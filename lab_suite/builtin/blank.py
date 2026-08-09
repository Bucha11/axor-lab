"""Blank — the smallest suite that runs.

Its job is to prove a suite is authorable from nothing: a manifest, one
scenario, no governance, no suite-specific code. Everything a third-party author
writes starts here, so if Blank needs a workaround the SDK has a hole.
"""

from __future__ import annotations

from lab_runner.loop import AgentProgram, Finish, ScriptedProgram, ToolCall

from ..manifest import ResolvedSuite
from ..sdk import BaseSuite

NOTE_TOOL = "note"


class BlankSuite(BaseSuite):
    id = "blank"

    def manifest(self) -> dict[str, object]:
        return {
            "schema_version": "suite/v1",
            "id": self.id,
            "name": "Blank Suite",
            "version": "1.0",
            "description": "Create your own suite",
            "origin": "built_in",
            "scenarios": [{
                "schema_version": "scenario/v1",
                "name": "blank-01",
                "task": "Record a note.",
                "inputs": {"note": "hello"},
                "tools": [{"$ref": NOTE_TOOL}],
                "fixtures": {NOTE_TOOL: {"result": {"ok": True}}},
                "task_success": {"event": "tool_call", "tool": NOTE_TOOL},
            }],
            "environment": {
                "tools": [_note_manifest()],
                "simulation": {"enabled": True, "strict_manifest": True},
            },
            "execution": {"strategy": "matrix", "repeats": 1, "seed_policy": "per_repeat"},
            "evaluation": {
                "metrics": [{"name": "duration_ms", "label": "Duration",
                             "kind": "duration_ms", "source": "trial_metric",
                             "from": "duration_ms", "direction": "lower_is_better"}],
                "aggregations": [{"metric": "duration_ms", "fn": "mean",
                                  "unit_of_analysis": "trial"}],
            },
            "artifact": {"include_traces": True, "sections": ["overview", "metrics"]},
            "tags": ["starter"],
        }

    def program_for(
        self,
        scenario: dict[str, object],
        seed: str,
        resolved: ResolvedSuite,
    ) -> AgentProgram:
        inputs: dict[str, object] = scenario.get("inputs", {})  # type: ignore[assignment]
        return ScriptedProgram([
            ToolCall(NOTE_TOOL, {"text": inputs.get("note", "")}),
            Finish(),
        ])


def _note_manifest() -> dict[str, object]:
    return {
        "schema_version": "tool-manifest/v1",
        "id": NOTE_TOOL,
        "args_schema": {"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
        "result_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                          "required": ["ok"]},
        "effect": {"default_class": "WRITE", "driving_args": []},
        "untrusted_fields": [],
        "side_effecting": False,
        "reset": {"strategy": "fixture", "fixture_ref": NOTE_TOOL},
    }
