"""Trial identity — pure, and needed by every execution path.

This lived in the paired `.axl` experiment runner, which moved into the
governance capability. The id derivation itself has nothing to do with
governance: a single-arm suite trial needs a stable id for exactly the same
reasons a governed one does, and leaving the function behind would have made
`lab_suite` import the capability just to name a trial.
"""

from __future__ import annotations

from lab_contracts import content_hash


def trial_id_for(
    run_id: str, scenario_id: str, condition_id: str, seed: str, repeat_index: int
) -> str:
    """Stable trial identity, SCOPED TO THE RUN.

    Includes run_id so two runs of the same experiment with different agents /
    models (run_id carries the agent fingerprint) do not mint identical trial
    ids — otherwise distinct experiments would look like retries of one trial
    when merged (review r3). Within one run a retry of the same coordinate still
    yields the same id (idempotent replace).
    """
    return content_hash(
        {"run": run_id, "scenario": scenario_id, "condition": condition_id,
         "seed": seed, "repeat": repeat_index}
    )
