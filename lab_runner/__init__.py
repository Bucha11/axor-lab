"""lab_runner — the local execution engine and CLI (Phases 1–2 of the plan).

Owns everything that runs: value ledger with conservative-join provenance,
the single pure `decide` shared by live runs and replay, simulated tools,
predicate evaluation over traces, the trial/experiment runner, exact replay,
EvidenceCase rendering, and regression pinning. Contracts live in
lab_contracts; statistics in lab_analysis.
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
from .evidence import build_evidence_case
from .kernel import Kernel, KernelRegistry, default_registry
from .ledger import ValueLedger
from .predicates import evaluate
from .regression import RegressionPin, check_pins, pin
from .replay import ReplayReport, replay_bundle, replay_trace
from .runner import ExperimentResult, TrialOutcome, run_experiment, run_trial, trial_id_for
from .simulator import SimulatedToolHost

__all__ = [
    "AgentAdapter",
    "ConfirmationRequired",
    "ExperimentFileError",
    "ExperimentResult",
    "Kernel",
    "KernelRegistry",
    "RealExecutionBlocked",
    "RegressionPin",
    "ReplayReport",
    "RunnerError",
    "ScriptedAgent",
    "SimulatedToolHost",
    "TrialOutcome",
    "UnknownAgentError",
    "UnknownKernelError",
    "UnsupportedPredicateError",
    "ValueLedger",
    "build_evidence_case",
    "check_pins",
    "default_registry",
    "evaluate",
    "pin",
    "replay_bundle",
    "replay_trace",
    "resolve_agent",
    "run_experiment",
    "run_trial",
    "trial_id_for",
]
