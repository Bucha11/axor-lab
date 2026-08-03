"""lab_suite — the Experiment Suite SDK.

The Suite is the core abstraction of the platform (Suite Platform RFC §6): one
manifest defines scenarios, agents, environment, execution strategy, evaluators,
metrics, aggregations, artifact layout and regression rules. This package is
what makes such a manifest EXECUTABLE.

  manifest.py  — load / validate / resolve a suite/v1 into a runnable plan
  sdk.py       — the Suite protocol (RFC §12) and its registry
  execute.py   — plan -> trials -> aggregates -> invariants -> artifact
  builtin/     — the launch set: Blank, AgentDojo, Budget
"""

from __future__ import annotations

from .dispatch import (
    DispatchError,
    SuiteAssignment,
    assign_suite,
    build_assignment,
    collect_suite_run,
)
from .errors import SuiteError, SuiteValidationError
from .execute import SuiteRun, run_suite
from .manifest import ResolvedSuite, load_manifest, resolve_suite, validate_manifest
from .sdk import Suite, SuiteRegistry, builtin_registry

__all__ = [
    "DispatchError",
    "ResolvedSuite",
    "SuiteAssignment",
    "assign_suite",
    "build_assignment",
    "collect_suite_run",
    "Suite",
    "SuiteError",
    "SuiteRegistry",
    "SuiteRun",
    "SuiteValidationError",
    "builtin_registry",
    "load_manifest",
    "resolve_suite",
    "run_suite",
    "validate_manifest",
]
