"""A minimal iterated-game runtime producing per-run values.

Players are deterministic strategies (a `Player` maps history → cooperate/
defect), so a run is reproducible from its seed. The runtime records rounds
for the EvidenceCase/trace, but the STATISTIC is per run: the run's
cooperation rate over its rounds is the run's single value. n is the number
of runs (repeats), never the number of rounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# a strategy: (my_past, their_past) -> True (cooperate) | False (defect)
Strategy = Callable[[tuple[bool, ...], tuple[bool, ...]], bool]


@dataclass(frozen=True)
class Player:
    name: str
    strategy: Strategy


def tit_for_tat(my_past: tuple[bool, ...], their_past: tuple[bool, ...]) -> bool:
    return their_past[-1] if their_past else True


def always_defect(my_past: tuple[bool, ...], their_past: tuple[bool, ...]) -> bool:
    return False


def always_cooperate(my_past: tuple[bool, ...], their_past: tuple[bool, ...]) -> bool:
    return True


@dataclass
class GameResult:
    """One run of an iterated game."""

    run_id: str
    rounds: list[tuple[bool, bool]] = field(default_factory=list)

    def cooperation_rate(self) -> float:
        """The run's SINGLE value — a within-run rate, not n observations."""
        moves = [m for pair in self.rounds for m in pair]
        return sum(moves) / len(moves) if moves else 0.0


@dataclass(frozen=True)
class IteratedGame:
    a: Player
    b: Player
    rounds: int = 20


def run_game(game: IteratedGame, run_id: str) -> GameResult:
    """Play `rounds` rounds; return the per-run result (deterministic)."""
    a_hist: list[bool] = []
    b_hist: list[bool] = []
    result = GameResult(run_id=run_id)
    for _ in range(game.rounds):
        a_move = game.a.strategy(tuple(a_hist), tuple(b_hist))
        b_move = game.b.strategy(tuple(b_hist), tuple(a_hist))
        a_hist.append(a_move)
        b_hist.append(b_move)
        result.rounds.append((a_move, b_move))
    return result
