"""lab_games — a deterministic multi-agent game model (plan B7, spec-lab.md §5).

⚠️ MATURITY: experimental. These are small DETERMINISTIC strategy games, not a
live multi-agent LLM runtime. The federation containment result is a property
of THIS model (carried taint gives neighbours a clean signal), demonstrating
the desired behaviour — it is NOT independent evidence of containment among
real agents. Do not cite "contained at N=200" as a security proof; it is a toy
model that exercises the honest per-run statistics below.

The unit-of-analysis rule that invalidates most game statistics
(`statistics.md` §1): the independent observation is ONE run, never one round.
Rounds within a run are serially correlated — treating them as n fabricates
precision. So a game's metric is computed per run (a within-run rate is the
run's single value) and n is the number of runs. This module produces per-run
values and hands them to `lab_analysis` with `unit_of_analysis="run"`.
"""

from .errors import GameError
from .federation import (
    TOPOLOGY_COMPLETE,
    TOPOLOGY_RING,
    TOPOLOGY_STAR,
    FederationRun,
    Member,
    run_federation,
)
from .runtime import GameResult, IteratedGame, Player, run_game
from .stats import game_rate_aggregate

__all__ = [
    "FederationRun",
    "GameError",
    "GameResult",
    "IteratedGame",
    "Member",
    "Player",
    "TOPOLOGY_COMPLETE",
    "TOPOLOGY_RING",
    "TOPOLOGY_STAR",
    "game_rate_aggregate",
    "run_federation",
    "run_game",
]
