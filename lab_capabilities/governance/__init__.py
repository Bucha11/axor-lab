"""The governance capability: policy gates, verdicts, deterministic replay.

Everything here is reachable only from a suite that declares `governance` in its
capabilities and supplies `execution.conditions` (Suite Platform RFC §10). The
platform's general execution path (`lab_runner.loop`) imports none of it — it
knows only the `Gate` protocol, and a run with no gate produces no verdicts to
strip out.

The implementations live HERE now, not in `lab_runner`. They used to be there
and be re-exported from the package root, so `import lab_runner` dragged the
kernel, replay, EvidenceCase rendering, verdict pinning and the whole Control
Plane bridge into any process that wanted a value ledger — which is the opposite
of optional. Moving the files is what makes the capability separable for
packaging (INTEGRATION_PLAN §4.8); what makes it CORRECT is the direction of the
dependency, enforced both ways by `tests/test_capability_boundary.py`.

Honest scope note: a Lab run still always resolves a kernel. There is
deliberately no kernel-free arm — an unobserved run cannot emit value lineage,
so it cannot produce a conformant `trace/v1` at all. "Optional" here means a
suite need not declare conditions, opt into enforcement, or carry any of the
comparison machinery. It does not mean this package can be uninstalled and have
`run_suite` still work.

  gate.py             the Gate a suite's condition resolves to
  kernel.py           the reference taint-floor kernel + registry
  axor_backend.py     the real axor-core kernel, and config compilation
  replay.py           recompute verdicts over a frozen trace
  evidence.py         the injection -> provenance -> gate -> verdict chain
  regression.py       verdict-sequence pins
  cp_export.py        the Control Plane handoff
  claims.py           denial claim text
  runner.py           the paired `.axl` experiment runner
  experiment_file.py  `.axl` load / resolve
"""

from __future__ import annotations

from .axor_backend import (
    AxorKernel,
    axor_available,
    gate_with_governor,
    governor_config,
    real_kernel_version,
    resolve_candidate_kernel_for_trace,
    resolve_kernel,
    resolve_kernel_for_trace,
    resolve_recorded_kernel_for_trace,
)
from .evidence import build_evidence_case, evidence_condition, validate_twin
from .experiment_file import ResolvedExperiment, load_axl, resolve
from .gate import KernelGate, gate_for_condition
from .kernel import Kernel, KernelRegistry, default_registry
from .regression import RegressionPin, check_pins, pin
from .replay import (
    REPLAY_MALFORMED_TRACE,
    REPLAY_MATCH,
    REPLAY_MISMATCH,
    REPLAY_REDACTED_INPUT_UNAVAILABLE,
    REPLAY_UNSUPPORTED_KERNEL,
    ReplayReport,
    replay_bundle,
    replay_trace,
    replay_trace_status,
)
from .runner import (
    ExperimentResult,
    TrialOutcome,
    connected_runtime_condition,
    connected_runtime_kernel,
    observe_only_condition,
    remote_executable_kernel_errors,
    run_experiment,
    run_experiment_suite,
    run_trial,
)

__all__ = [
    "AxorKernel",
    "ExperimentResult",
    "Kernel",
    "KernelGate",
    "KernelRegistry",
    "REPLAY_MALFORMED_TRACE",
    "REPLAY_MATCH",
    "REPLAY_MISMATCH",
    "REPLAY_REDACTED_INPUT_UNAVAILABLE",
    "REPLAY_UNSUPPORTED_KERNEL",
    "RegressionPin",
    "ReplayReport",
    "ResolvedExperiment",
    "TrialOutcome",
    "axor_available",
    "build_evidence_case",
    "check_pins",
    "connected_runtime_condition",
    "connected_runtime_kernel",
    "default_registry",
    "evidence_condition",
    "gate_for_condition",
    "gate_with_governor",
    "governor_config",
    "load_axl",
    "observe_only_condition",
    "pin",
    "real_kernel_version",
    "remote_executable_kernel_errors",
    "replay_bundle",
    "replay_trace",
    "replay_trace_status",
    "resolve",
    "resolve_candidate_kernel_for_trace",
    "resolve_kernel",
    "resolve_kernel_for_trace",
    "resolve_recorded_kernel_for_trace",
    "run_experiment",
    "run_experiment_suite",
    "run_trial",
    "validate_twin",
]
