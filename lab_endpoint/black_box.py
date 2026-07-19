"""Black-box endpoint — evaluation-only, never governance.

Plain task-in / answer-out reveals only the final answer: Lab cannot see
internal tool calls, propagate provenance, or stop a sink. So this mode
produces NO conformant trace and is labeled evaluation-only everywhere it
appears. "compare" here means behavioral configurations, never Axor gate
on/off. We never call black-box scoring "governance."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

BLACK_BOX_LABEL = "evaluation-only — not governance"


@dataclass(frozen=True)
class BlackBoxResult:
    """The only thing a black-box endpoint yields: a scored final answer.

    `trace` is deliberately None — a black-box boundary cannot emit lineage,
    so no conformant trace exists (the schema omits black_box from
    producer.mode for exactly this reason)."""

    task: str
    output: str
    score: float
    label: str = BLACK_BOX_LABEL
    trace: None = None
    governance_available: bool = False


def score_black_box(
    task: str,
    endpoint: Callable[[str], str],
    scorer: Callable[[str], float],
) -> BlackBoxResult:
    """Run a task through a black-box endpoint and score the OUTPUT only."""
    output = endpoint(task)
    return BlackBoxResult(task=task, output=output, score=scorer(output))
