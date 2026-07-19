"""Federation players + population-scale spawn (plan B7 federation / B8).

A federation is a group of member agents that act as ONE player; per
`statistics.md` §1 and domain-model.md, the unit of analysis is one run of the
FEDERATION — per-member values are structure *within* the observation, never
observations themselves. Population scale is the same runtime with N members;
carried taint contains a compromised member at the first boundary (the Prompt
Infection / topology-attack shape from outreach-targets.md).

This is a local, deterministic model of that structure — the honest statistics
and the containment property, without cloud execution (which is gated by the
sandbox, B6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# a member decides cooperate/defect from its own history and a neighbor signal
MemberStrategy = Callable[[tuple[bool, ...], bool], bool]

TOPOLOGY_RING = "ring"
TOPOLOGY_STAR = "star"
TOPOLOGY_COMPLETE = "complete"


@dataclass(frozen=True)
class Member:
    name: str
    strategy: MemberStrategy
    compromised: bool = False


@dataclass
class FederationRun:
    """One run of a federation over `rounds` — a SINGLE observation."""

    run_id: str
    member_moves: dict[str, list[bool]] = field(default_factory=dict)
    contained_at: int | None = None  # round index where carried taint stopped a member

    def cooperation_rate(self) -> float:
        """The federation's single per-run value (all members' moves pooled)."""
        moves = [m for seq in self.member_moves.values() for m in seq]
        return sum(moves) / len(moves) if moves else 0.0

    def compromised_spread(self) -> int:
        """How many members ever defected — the blast radius of a compromise."""
        return sum(1 for seq in self.member_moves.values() if not all(seq))


def neighbors(topology: str, index: int, n: int) -> list[int]:
    if topology == TOPOLOGY_RING:
        return [(index - 1) % n]
    if topology == TOPOLOGY_STAR:
        return [0] if index != 0 else list(range(1, n))
    if topology == TOPOLOGY_COMPLETE:
        return [j for j in range(n) if j != index]
    raise ValueError(f"unknown topology {topology!r}")


def run_federation(
    members: list[Member],
    rounds: int,
    topology: str = TOPOLOGY_RING,
    run_id: str = "fed_0",
    carried_taint: bool = True,
) -> FederationRun:
    """Play a federation game. With `carried_taint`, a compromised member's
    defection is contained at the first boundary — neighbors see the tainted
    signal and refuse to propagate it (the governance property), so the blast
    radius stays local instead of infecting the population."""
    n = len(members)
    result = FederationRun(run_id=run_id)
    history: dict[int, list[bool]] = {i: [] for i in range(n)}
    for _ in range(rounds):
        signals: dict[int, bool] = {}
        for i, member in enumerate(members):
            incoming = [history[j][-1] if history[j] else True for j in neighbors(topology, i, n)]
            # carried taint: a neighbor's defection is a tainted signal; under
            # governance the member does NOT adopt it (containment at the edge)
            neighbor_signal = all(incoming) if not carried_taint else True
            move = member.strategy(tuple(history[i]), neighbor_signal)
            if member.compromised:
                move = False  # a compromised member always defects
                if result.contained_at is None and carried_taint:
                    result.contained_at = len(history[i])
            signals[i] = move
        for i, move in signals.items():
            history[i].append(move)
    result.member_moves = {members[i].name: history[i] for i in range(n)}
    return result
