"""lab_runner — the local execution engine and CLI (Phases 1–2 of the plan).

Owns everything that runs: value ledger with conservative-join provenance,
the single pure `decide` shared by live runs and replay, simulated tools,
predicate evaluation over traces, the trial/experiment runner, exact replay,
EvidenceCase rendering, and regression pinning. Contracts live in
lab_contracts; statistics in lab_analysis.
"""

from .agents import AgentAdapter, ScriptedAgent, resolve_agent
from .axor_backend import (
    AxorKernel,
    axor_available,
    governor_config,
    real_kernel_version,
    resolve_kernel,
)
from .errors import (
    ConfirmationRequired,
    ExperimentFileError,
    RealExecutionBlocked,
    RunnerError,
    UnknownAgentError,
    UnknownKernelError,
    UnsupportedPredicateError,
)
from .evidence import build_evidence_case, evidence_condition, validate_twin
from .kernel import Kernel, KernelRegistry, default_registry
from .ledger import ValueLedger
from .predicates import evaluate
from .regression import RegressionPin, check_pins, pin
from .replay import (
    REPLAY_MALFORMED_TRACE,
    REPLAY_MATCH,
    REPLAY_MISMATCH,
    REPLAY_UNSUPPORTED_KERNEL,
    ReplayReport,
    replay_bundle,
    replay_trace,
    replay_trace_status,
)
from .runner import (
    ExperimentResult,
    TrialOutcome,
    run_experiment,
    run_experiment_suite,
    run_trial,
    trial_id_for,
)
from .simulator import SimulatedToolHost

__all__ = [
    "AgentAdapter",
    "AxorKernel",
    "ConfirmationRequired",
    "axor_available",
    "governor_config",
    "real_kernel_version",
    "resolve_kernel",
    "ExperimentFileError",
    "ExperimentResult",
    "Kernel",
    "KernelRegistry",
    "REPLAY_MALFORMED_TRACE",
    "REPLAY_MATCH",
    "REPLAY_MISMATCH",
    "REPLAY_UNSUPPORTED_KERNEL",
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
    "evidence_condition",
    "validate_twin",
    "check_pins",
    "default_registry",
    "evaluate",
    "pin",
    "replay_bundle",
    "replay_trace",
    "replay_trace_status",
    "resolve_agent",
    "run_experiment",
    "run_experiment_suite",
    "run_trial",
    "trial_id_for",
]
