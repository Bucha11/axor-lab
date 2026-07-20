"""Federation players + population-scale spawn (plan B7 federation / B8).

A federation is a group of member agents that act as ONE player; per
`statistics.md` §1 and domain-model.md, the unit of analysis is one run of the
FEDERATION — per-member values are structure *within* the observation, never
observations themselves. Population scale is the same runtime with N members;
under governance a compromised member is contained at the first boundary — its
tainted signal is refused, so it never spreads past the origin (the Prompt
Infection / topology-attack shape from outreach-targets.md).

This is a local, deterministic model of that structure — the honest statistics
and the containment property, without cloud execution (which is gated by the
sandbox, B6). `blast_radius` counts spread BEYOND the origin compromise and
`contained` is true when that spread is zero, so both names measure exactly
what they say (review r12).
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
    # the ORIGIN compromise set — the members that defect by nature. Naming this
    # explicitly is what lets blast_radius exclude the seed so "spread" means
    # spread, not "the seed plus its spread" (review r12).
    compromised_members: frozenset[str] = frozenset()

    def cooperation_rate(self) -> float:
        """The federation's single per-run value (all members' moves pooled)."""
        moves = [m for seq in self.member_moves.values() for m in seq]
        return sum(moves) / len(moves) if moves else 0.0

    def blast_radius(self) -> int:
        """Members INDUCED to defect BEYOND the origin compromise — the true
        spread of a compromise. The origin compromised members are excluded:
        they defect by nature, so counting them would report a blast radius of
        at least the seed size even when nothing spread at all (the old
        `compromised_spread` did exactly that, review r12)."""
        return sum(
            1 for name, seq in self.member_moves.items()
            if name not in self.compromised_members and not all(seq)
        )

    def contained(self) -> bool:
        """True when the compromise never spread past its origin (blast_radius
        0). This is the containment property — a name that matches what it
        measures, unlike the old `contained_at`, which recorded the round the
        origin FIRST defected (its onset), not any boundary where spread was
        stopped."""
        return self.blast_radius() == 0


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
    governed: bool = True,
) -> FederationRun:
    """Play a federation game. When `governed`, a member does NOT adopt a
    neighbor's tainted (defecting) signal — governance contains the compromise at
    the edge, so it never spreads past the origin. Ungoverned, a defecting
    neighbor flips the member and the compromise infects the population.

    (`governed` replaces the old `carried_taint` flag, whose name read as "the
    taint IS carried" while the True branch actually meant the opposite —
    containment. Same behavior, honest name; review r12.)"""
    n = len(members)
    compromised = frozenset(m.name for m in members if m.compromised)
    history: dict[int, list[bool]] = {i: [] for i in range(n)}
    for _ in range(rounds):
        signals: dict[int, bool] = {}
        for i, member in enumerate(members):
            incoming = [history[j][-1] if history[j] else True for j in neighbors(topology, i, n)]
            # under governance the member treats a defecting neighbor's signal as
            # tainted and refuses to adopt it (containment at the edge); ungoverned
            # it propagates — a single defecting neighbor flips this member too
            neighbor_signal = True if governed else all(incoming)
            move = member.strategy(tuple(history[i]), neighbor_signal)
            if member.compromised:
                move = False  # a compromised member always defects (the origin)
            signals[i] = move
        for i, move in signals.items():
            history[i].append(move)
    return FederationRun(
        run_id=run_id,
        member_moves={members[i].name: history[i] for i in range(n)},
        compromised_members=compromised,
    )
