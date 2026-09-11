"""lab_runner — the platform's execution engine.

What runs EVERY experiment, governed or not: the value ledger with
conservative-join provenance, the general agent loop, simulated tools, the typed
predicate evaluator, executable invariants, bundle I/O and the CLI.

This package used to also own the kernel, replay, EvidenceCase rendering,
verdict pinning, the Control Plane bridge and the paired `.axl` experiment
runner — and re-exported all of them from here, so `import lab_runner` pulled
the entire governance stack into any process that wanted a value ledger. They
live in `lab_capabilities.governance` now (Suite Platform RFC §10: governance is
an optional capability). Nothing here imports them, which is what
`tests/test_capability_boundary.py` enforces in both directions.

Contracts live in lab_contracts; statistics in lab_analysis; the Suite SDK in
lab_suite.
"""

from .agents import AgentAdapter, ScriptedAgent, resolve_agent
from .errors import (
    ConfirmationRequired,
    ExperimentFileError,
    RealExecutionBlocked,
    RunnerError,
    UnknownAgentError,
    UnknownKernelError,
    UnsupportedPredicateError,
)
from .invariants import InvariantResult, check_invariant
from .loop import LoopOutcome, run_loop_trial
from .predicates import evaluate
from .simulator import SimulatedToolHost
from .trials import trial_id_for

__all__ = [
    "AgentAdapter",
    "ConfirmationRequired",
    "ExperimentFileError",
    "InvariantResult",
    "LoopOutcome",
    "RealExecutionBlocked",
    "RunnerError",
    "ScriptedAgent",
    "SimulatedToolHost",
    "UnknownAgentError",
    "UnknownKernelError",
    "UnsupportedPredicateError",
    "check_invariant",
    "evaluate",
    "resolve_agent",
    "run_loop_trial",
    "trial_id_for",
]
