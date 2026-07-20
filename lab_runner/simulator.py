"""Simulated tool host: fixtures + `$injection` placement + ledger_stub.

The load-bearing safety choice (threat-model §1): every `side_effecting`
tool runs through a simulator by default; real execution requires explicit
opt-in AND the full guard set. Running an attack benchmark cannot create
the incident it studies.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from lab_contracts.subset_validator import validate_against

from .errors import RealExecutionBlocked, SimulationError

INJECTION_TOKEN = "$injection"
_INDEX_RE = re.compile(r"\[(\d+)\]")

REQUIRED_GUARDS_DEFAULT = ("isolated_test_account", "resource_allowlist", "dry_run_confirmed")

# the simulation adapters the reference host actually implements. An unknown
# adapter is a validation error, NOT a silent noop_stub success (review r6).
KNOWN_ADAPTERS = frozenset({"ledger_stub", "noop_stub", "outbox_stub", "email_outbox"})


def _schema_errors(obj: object, schema: dict[str, object] | None) -> list[str]:
    """Validate a value against an inline JSON-subset schema (args/result)."""
    if not schema:
        return []
    return validate_against(obj, "_inline", {"_inline": schema})


@dataclass
class SimulatedToolHost:
    """Executes tools against fixtures; records side-effect attempts."""

    manifests: dict[str, dict[str, object]]
    fixtures: dict[str, dict[str, object]]
    injection_text: str
    attempts: list[dict[str, object]] = field(default_factory=list)

    def execute(
        self,
        tool_id: str,
        args: dict[str, object],
        *,
        real: bool = False,
        guards: tuple[str, ...] = (),
    ) -> object:
        manifest = self.manifests[tool_id]
        # the call's args are a runnable contract: a call that violates
        # args_schema is a protocol error, never a silently-accepted call
        arg_errors = _schema_errors(args, manifest.get("args_schema"))  # type: ignore[arg-type]
        if arg_errors:
            raise SimulationError(
                f"tool {tool_id} args do not match args_schema: {'; '.join(arg_errors[:5])}"
            )
        if bool(manifest.get("side_effecting")):
            # honor the manifest's simulation contract — do NOT fake a result for
            # a tool that declares itself unsimulatable or names an unknown adapter
            adapter = self._require_simulatable(manifest)
            if real:
                self._check_real_execution(manifest, guards)
            self.attempts.append(
                {"tool": tool_id, "args": dict(args), "executed": "simulated", "adapter": adapter}
            )
            return {"status": "simulated", "adapter": adapter}
        return self.fixture_result(tool_id)

    def _require_simulatable(self, manifest: dict[str, object]) -> str:
        """Return the adapter to use, or raise if the tool cannot be simulated."""
        simulation: dict[str, object] | None = manifest.get("simulation")  # type: ignore[assignment]
        if not simulation:
            raise SimulationError(
                f"tool {manifest['id']} is side_effecting but declares no simulation; "
                "the reference host will not fake a result"
            )
        if not bool(simulation.get("supported", False)):
            raise SimulationError(
                f"tool {manifest['id']} declares simulation.supported=false; refusing to "
                "return a simulated success it cannot model"
            )
        adapter = str(simulation.get("adapter", ""))
        if adapter not in KNOWN_ADAPTERS:
            raise SimulationError(
                f"tool {manifest['id']} names unknown simulation adapter {adapter!r} "
                f"(known: {sorted(KNOWN_ADAPTERS)})"
            )
        return adapter

    def fixture_result(self, tool_id: str) -> object:
        """The tool's fixture result with `$injection` substituted in place."""
        fixture = self.fixtures.get(tool_id, {})
        result = copy.deepcopy(fixture.get("result"))
        placement: dict[str, object] = fixture.get("injection_placement", {})  # type: ignore[assignment]
        if placement.get("field") and result is not None:
            _place(result, str(placement["field"]), placement.get("mode", "append"), self.injection_text)
        return result

    def _check_real_execution(self, manifest: dict[str, object], guards: tuple[str, ...]) -> None:
        simulation: dict[str, object] = manifest.get("simulation", {})  # type: ignore[assignment]
        real_cfg: dict[str, object] = simulation.get("real_execution", {})  # type: ignore[assignment]
        if not bool(real_cfg.get("opt_in", False)):
            raise RealExecutionBlocked(
                f"tool {manifest['id']} is side_effecting and not opted into real execution"
            )
        required = tuple(real_cfg.get("requires", REQUIRED_GUARDS_DEFAULT))  # type: ignore[arg-type]
        missing = [g for g in required if g not in guards]
        if missing:
            raise RealExecutionBlocked(
                f"tool {manifest['id']} real execution missing guards: {', '.join(missing)}"
            )
        raise RealExecutionBlocked(
            "real side effects are not implemented in the MVP reference runner"
        )


def validate_fixture_results(
    scenario: dict[str, object], manifests: dict[str, dict[str, object]]
) -> list[str]:
    """Author-time: every fixture result must satisfy its tool's result_schema,
    so an authoring mistake fails before a run rather than surfacing as a
    KeyError mid-trial (review r6)."""
    errors: list[str] = []
    fixtures: dict[str, dict[str, object]] = scenario.get("fixtures", {})  # type: ignore[assignment]
    for tool_id, fixture in fixtures.items():
        manifest = manifests.get(tool_id)
        if manifest is None:
            continue  # unknown-tool errors are raised by scenario validation
        schema = manifest.get("result_schema")
        result = fixture.get("result")
        errors += [
            f"fixture {tool_id}: result {e}" for e in _schema_errors(result, schema)  # type: ignore[arg-type]
        ]
    return errors


def _place(result: object, path: str, mode: object, injection: str) -> None:
    """Substitute the injection at `path` (e.g. transactions[1].description)."""
    parts = _INDEX_RE.sub(r".\1", path).split(".")
    node = result
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]  # type: ignore[index]
    leaf = parts[-1]
    key: object = int(leaf) if isinstance(node, list) else leaf
    current = node[key]  # type: ignore[index]
    if mode == "append" and isinstance(current, str) and INJECTION_TOKEN not in current:
        node[key] = f"{current} {injection}"  # type: ignore[index]
    else:
        node[key] = injection  # type: ignore[index]
