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

from .errors import RealExecutionBlocked

INJECTION_TOKEN = "$injection"
_INDEX_RE = re.compile(r"\[(\d+)\]")

REQUIRED_GUARDS_DEFAULT = ("isolated_test_account", "resource_allowlist", "dry_run_confirmed")


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
        if bool(manifest.get("side_effecting")):
            if real:
                self._check_real_execution(manifest, guards)
            self.attempts.append(
                {"tool": tool_id, "args": dict(args), "executed": "simulated"}
            )
            return {"status": "simulated", "adapter": self._adapter(manifest)}
        return self.fixture_result(tool_id)

    def fixture_result(self, tool_id: str) -> object:
        """The tool's fixture result with `$injection` substituted in place."""
        fixture = self.fixtures.get(tool_id, {})
        result = copy.deepcopy(fixture.get("result"))
        placement: dict[str, object] = fixture.get("injection_placement", {})  # type: ignore[assignment]
        if placement.get("field") and result is not None:
            _place(result, str(placement["field"]), placement.get("mode", "append"), self.injection_text)
        return result

    def _adapter(self, manifest: dict[str, object]) -> str:
        simulation: dict[str, object] = manifest.get("simulation", {})  # type: ignore[assignment]
        return str(simulation.get("adapter", "noop_stub"))

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
