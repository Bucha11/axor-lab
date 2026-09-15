"""The Suite protocol — Suite Platform RFC §12.

A suite exposes a config schema, a UI schema, validators, execution hooks,
metrics, an artifact renderer, a regression extractor and optional EvidenceCase
helpers. Everything except the manifest itself has a default, so the smallest
possible suite is a manifest and nothing else — which is exactly what the Blank
suite is, and what a third-party author starts from.

The config and UI schemas are NOT hooks: they are `config_schema`, `config` and
`ui_schema` on the manifest itself. One place to declare them, so the document
a Builder edits and a runtime receives already carries the suite's own options
— a hook would put the declaration somewhere the manifest cannot reach, and the
manifest is what travels. `lab_suite.manifest` validates `config` against
`config_schema` at author time; the Builder renders it.

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

    # --- comparison design ------------------------------------------------
    def agent_is_deterministic(self) -> bool:
        """Is this suite's agent behaviour fixed by (scenario, seed)?

        It decides the run's COMPARISON DESIGN. Matched pairs — and therefore
        McNemar — are only valid when the same seed produces the same behaviour
        under both arms; a sampled agent draws each condition independently and
        its "pairs" are nominal, so the paired p-value would be spurious.

        Default FALSE, which yields `independent_samples`. The CP bridge reads
        the attested design and refuses to assume a paired one — "never a silent
        default to matched_pairs" — so an author who does not answer gets the
        weaker claim rather than an unearned one. A suite whose `program_for` is
        a pure function of the seed says so by overriding this.

        It describes the SUITE's own agent. A run executed by a connected
        runtime is attested `independent_samples` regardless: a live model is
        sampled, and this hook cannot speak for someone else's machine.
        """
        return False

    # --- presentation ----------------------------------------------------
    def render_artifact(self, artifact: dict[str, object]) -> dict[str, object]:
        """A suite-specific artifact view. Default: the artifact unchanged."""
        return artifact


def comparison_design(
    suite: object, *, executed_by_runtime: bool = False
) -> dict[str, object]:
    """The run's attested `comparison-design/v1`, for `environment`.

    Bound to the ACTUAL agent's determinism and recorded at run time, because
    this — not an uploader-controlled aggregate — is what the Control-Plane
    bridge reads to choose matched_pairs over independent_samples. A suite
    bundle used to carry no design at all, so `_bridge_design` returned None and
    the bridge could never be earned: every suite could be exported to
    production, and none could say governance had changed an outcome.

    `executed_by_runtime` forces independent samples. Lab does not know what a
    connected runtime ran; a live model draws each condition separately, and a
    paired design asserted over that is a spurious p-value with a signature on
    it.
    """
    deterministic = (
        False if executed_by_runtime
        else bool(getattr(suite, "agent_is_deterministic", lambda: False)())
    )
    kind = "matched_pairs" if deterministic else "independent_samples"
    return {
        "schema_version": "comparison-design/v1",
        "kind": kind,
        "unit_key": ["execution_id", "scenario_id", "condition_id", "seed", "repeat_index"],
        "assignment": ("shared_deterministic_agent_state" if deterministic
                       else "independent_per_condition"),
        "agent_deterministic": deterministic,
    }


def drives_its_own_trials(suite: object) -> bool:
    """Whether this suite decides what its agent DOES.

    A manifest says which tools exist and what they mean; it cannot say the
    order an agent calls them in. That is `program_for`, and a suite that does
    not override it gets `_NullProgram` — the loop finishes immediately, the
    trace is empty, every metric is false, and an artifact is written anyway.

    So a locally-run suite without this is not measuring anything, and the
    screens that offer to run one locally have to say so BEFORE the click
    rather than present 108 zeros afterwards. A DISPATCHED suite does not need
    it: the connected runtime drives the loop with a real model, which is what
    the manifest was written for.
    """
    return type(suite).program_for is not BaseSuite.program_for


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
    """The launch set: Blank, AgentDojo, Budget, Ingest.

    Each exercises a different SDK surface once — Blank proves a suite is
    authorable from nothing, AgentDojo proves the import path, Budget proves the
    metrics layer, Ingest proves a suite can be derived from a shipped agent's
    documented tool chain (the case a customer starts from, where nobody has
    curated anything). The catalog is expected to mark the remaining announced
    suites unavailable rather than render a card that runs nothing.
    """
    from .builtin.agentdojo import AgentDojoSuite
    from .builtin.blank import BlankSuite
    from .builtin.budget import BudgetSuite
    from .builtin.ingest import IngestSuite

    registry = SuiteRegistry()
    registry.register(BlankSuite())
    registry.register(AgentDojoSuite())
    registry.register(IngestSuite())
    registry.register(BudgetSuite())
    return registry


__all__ = [
    "BaseSuite",
    "ScriptedProgram",
    "Suite",
    "SuiteRegistry",
    "builtin_registry",
]
