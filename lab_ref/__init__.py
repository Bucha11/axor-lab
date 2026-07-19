"""lab_ref — the minimal reference implementation of the Axor Lab contracts.

Executable referent for `contracts/`: enough of the vertical slice
(banking-exfil-01) to make `contracts/acceptance-tests.md` §1–10 runnable as
an e2e suite, on stdlib only, with a scripted agent standing in for the
model layer. Superseded piece-by-piece by the real packages in Phases 1–5 of
docs/IMPLEMENTATION_PLAN.md; the acceptance tests stay.
"""

from .bundle import build_bundle, verify_bundle
from .canonical import canonical_json, condition_config_hash, content_hash
from .errors import (
    BundleIntegrityError,
    ClaimTypingError,
    LabRefError,
    RealExecutionBlocked,
    ScenarioValidationError,
    UnknownKernelError,
    UnsupportedPredicateError,
)
from .evidence import build_evidence_case
from .kernel import Kernel, KernelRegistry
from .ledger import ValueLedger
from .predicates import evaluate
from .publication import add_reproduction, build_publication, make_claim, provenance_axes
from .regression import RegressionPin, check_pins, pin
from .replay import ReplayReport, replay_bundle, replay_trace
from .runner import ExperimentResult, ScriptedAgent, run_experiment, run_trial, trial_id_for
from .simulator import SimulatedToolHost
from .stats import (
    MissingnessSummary,
    binary_aggregate,
    is_inconclusive,
    mcnemar_exact,
    mcnemar_test,
    missingness,
    paired_bootstrap_ci,
    wilson_interval,
)
from .validation import validate_scenario

__all__ = [
    "BundleIntegrityError",
    "ClaimTypingError",
    "ExperimentResult",
    "Kernel",
    "KernelRegistry",
    "LabRefError",
    "MissingnessSummary",
    "RealExecutionBlocked",
    "RegressionPin",
    "ReplayReport",
    "ScenarioValidationError",
    "ScriptedAgent",
    "SimulatedToolHost",
    "UnknownKernelError",
    "UnsupportedPredicateError",
    "ValueLedger",
    "add_reproduction",
    "binary_aggregate",
    "build_bundle",
    "build_evidence_case",
    "build_publication",
    "canonical_json",
    "check_pins",
    "condition_config_hash",
    "content_hash",
    "evaluate",
    "is_inconclusive",
    "make_claim",
    "mcnemar_exact",
    "mcnemar_test",
    "missingness",
    "paired_bootstrap_ci",
    "pin",
    "provenance_axes",
    "replay_bundle",
    "replay_trace",
    "run_experiment",
    "run_trial",
    "trial_id_for",
    "validate_scenario",
    "verify_bundle",
    "wilson_interval",
]
