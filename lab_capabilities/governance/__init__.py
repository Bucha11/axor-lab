"""The governance capability: policy gates, verdicts, deterministic replay.

Everything here is reachable only from a suite that declares `governance` in its
capabilities and supplies `execution.conditions`. The platform's general
execution path (`lab_runner.loop`) does not import any of it — it knows only the
`Gate` protocol, and a run with no gate produces no verdicts to strip out.

The kernel, replay, EvidenceCase and Control Plane bridge implementations still
live in `lab_runner` for now: they are twenty-odd rounds of hardened correctness
work, and relocating those files is churn that would risk the behaviour the plan
explicitly says to preserve. What matters — and what is enforced by
`tests/test_capability_boundary.py` — is the DIRECTION of the dependency:
governance reaches into the platform, never the other way round.
"""

from __future__ import annotations

from .gate import KernelGate, gate_for_condition

__all__ = ["KernelGate", "gate_for_condition"]
