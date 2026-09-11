"""The Suite protocol — Suite Platform RFC §12.

A suite exposes a config schema, a UI schema, validators, execution hooks,
metrics, an artifact renderer, a regression extractor and optional EvidenceCase
helpers. Everything except the manifest itself has a default, so the smallest
possible suite is a manifest and nothing else — which is exactly what the Blank
suite is, and what a third-party author starts from.

The hooks are deliberately narrow. A suite decides WHAT its agent does and WHAT
its own metrics mean; it never touches provenance, gating or the ledger. Those
belong to the runtime, and a suite that could reach them could launder taint.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lab_runner.loop import AgentProgram, Finish, LoopContext, LoopOutcome, ScriptedProgram

from .errors import SuiteNotFound
from .manifest import ResolvedSuite, validate_manifest


class _NullProgram:
    """Does nothing. The default when a suite declares no behaviour — a run
    that produces empty traces rather than pretending to have done work."""

    def next_action(self, ctx: LoopContext) -> Finish:
        return Finish()


@runtime_checkable
class Suite(Protocol):
    """What a suite implementation provides."""

    id: str

    def manifest(self) -> dict[str, object]: ...


class BaseSuite:
    """Default implementations of every optional hook.

    Subclass and override only what differs. Every default is a no-op that
    ADDS nothing rather than inventing something — a suite that does not define
    metrics gets no metrics, not zeros.
    """

    id: str = "base"

    def manifest(self) -> dict[str, object]:
        raise NotImplementedError

    # --- validation ------------------------------------------------------
    def validate(self, manifest: dict[str, object]) -> list[str]:
        """Suite-specific rules on top of the platform's. Default: none."""
        return []

    # --- execution -------------------------------------------------------
    def program_for(
        self,
        scenario: dict[str, object],
        seed: str,
        resolved: ResolvedSuite,
    ) -> AgentProgram:
        """The agent behaviour for one trial. Default: do nothing."""
        return _NullProgram()

    # --- observation -----------------------------------------------------
    def metrics_for(
        self, outcome: LoopOutcome, scenario: dict[str, object]
    ) -> dict[str, object]:
        """Suite-defined per-trial metrics, merged over the platform's.

        Returning nothing is correct for a suite with no metrics of its own. A
        value here must be something the suite actually measured — the platform
        treats an absent metric as unmeasured, and an invented 0 would let an
        invariant pass over a measurement that never happened.
        """
        return {}

    # --- investigation ---------------------------------------------------
    def evidence_for(
        self, outcome: LoopOutcome, trial: dict[str, object], scenario: dict[str, object]
    ) -> list[dict[str, object]]:
        """EvidenceCases this suite extracts automatically. Default: none —
        curation is a human act unless a suite knows better."""
        return []

    def regressions_for(
        self, outcome: LoopOutcome, trial: dict[str, object], scenario: dict[str, object]
    ) -> list[dict[str, object]]:
        """Invariants this suite pins from a trial. Default: none."""
        return []

    # --- presentation ----------------------------------------------------
    def render_artifact(self, artifact: dict[str, object]) -> dict[str, object]:
        """A suite-specific artifact view. Default: the artifact unchanged."""
        return artifact


class SuiteRegistry:
    """Suites by id. Third-party suites register here too (RFC §17)."""

    def __init__(self) -> None:
        self._suites: dict[str, BaseSuite] = {}

    def register(self, suite: BaseSuite) -> None:
        self._suites[suite.id] = suite

    def get(self, suite_id: str) -> BaseSuite:
        try:
            return self._suites[suite_id]
        except KeyError:
            raise SuiteNotFound(
                f"no suite {suite_id!r}; registered: {sorted(self._suites)}"
            ) from None

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._suites))

    def catalog(self) -> list[dict[str, object]]:
        """One card per REGISTERED suite. Every card is `available`.

        For the full catalog the screen renders — these plus the announced ones
        that do not exist yet — use :func:`suite_catalog`.
        """
        cards: list[dict[str, object]] = []
        for suite_id in self.ids():
            manifest = self._suites[suite_id].manifest()
            cards.append({
                "id": suite_id,
                "name": manifest.get("name", suite_id),
                "description": manifest.get("description", ""),
                "origin": manifest.get("origin", "built_in"),
                "tags": manifest.get("tags", []),
                "capabilities": manifest.get("capabilities", []),
                "available": True,
            })
        return cards

    def validate(self, suite_id: str) -> list[str]:
        """Platform rules plus the suite's own."""
        suite = self.get(suite_id)
        manifest = suite.manifest()
        return validate_manifest(manifest) + suite.validate(manifest)


# Suites the design boards show and the launch set does not include
# (INTEGRATION_PLAN §6 Phase 2, and the risk table's last row). They are listed
# so the catalog can say "not yet" out loud. The alternative — showing six cards
# and having three of them do nothing — is the failure this exists to prevent,
# and dropping them from the catalog entirely is the other one: a user comparing
# the product to the design board would conclude the feature was cut.
UNAVAILABLE_SUITES: tuple[dict[str, object], ...] = (
    {"id": "prompt_injection", "name": "Prompt Injection",
     "description": "Injection corpora against an agent's tool surface."},
    {"id": "performance", "name": "Performance",
     "description": "Latency and throughput under load."},
    {"id": "reliability", "name": "Reliability",
     "description": "Failure and retry behaviour over repeated trials."},
)

_UNAVAILABLE_REASON = (
    "announced in the design catalog; not implemented — the launch set is "
    "Blank, AgentDojo and Budget"
)


def suite_catalog(registry: "SuiteRegistry | None" = None) -> list[dict[str, object]]:
    """The Suite Catalog payload: registered suites, then the announced ones
    marked unavailable with the reason.

    One function so the CLI and the screen endpoint cannot disagree about which
    suites exist — two independent lists is how a catalog ends up offering a
    suite the runner cannot resolve.
    """
    cards = list((registry or builtin_registry()).catalog())
    known = {str(c["id"]) for c in cards}
    for announced in UNAVAILABLE_SUITES:
        if str(announced["id"]) in known:
            continue  # someone implemented it — the registry wins
        cards.append({**announced, "origin": "built_in", "tags": [],
                      "capabilities": [], "available": False,
                      "reason": _UNAVAILABLE_REASON})
    return cards


def builtin_registry() -> SuiteRegistry:
    """The launch set: Blank, AgentDojo, Budget.

    Three, not the six the design's catalog shows. Together they exercise every
    SDK surface once — Blank proves a suite is authorable from nothing,
    AgentDojo proves the import path, Budget proves the metrics layer — and the
    catalog is expected to mark the other three unavailable rather than render a
    card that runs nothing.
    """
    from .builtin.agentdojo import AgentDojoSuite
    from .builtin.blank import BlankSuite
    from .builtin.budget import BudgetSuite

    registry = SuiteRegistry()
    registry.register(BlankSuite())
    registry.register(AgentDojoSuite())
    registry.register(BudgetSuite())
    return registry


__all__ = [
    "BaseSuite",
    "ScriptedProgram",
    "Suite",
    "SuiteRegistry",
    "builtin_registry",
]
