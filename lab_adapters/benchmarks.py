"""The benchmark LIBRARY. AgentDojo is the default entry, not the only shape.

The first cut wired AgentDojo straight into an endpoint, which quietly said
"benchmark" and "AgentDojo" were the same word. They are not: a deployment that
wants to measure its own agent against its own suites has the same question —
what does the gate cost me, and what does each secret declaration cost — and no
reason to phrase it in someone else's task names.

So a benchmark is a registered PROVIDER with a stable interface:

    id / title / source      what it is and where it came from
    suites()                 the suites it offers
    matrix(...)              the error matrix under a policy
    sweep(suite, ...)        the marginal cost of each secret declaration

`agentdojo` is registered here and is the default. A second provider needs to
implement the same four calls and register itself; nothing above this module —
the HTTP surface, the CLI, the UI — learns a new concept.

The registry is deliberately in-process and explicit rather than a plugin scan:
a benchmark that appears because a file was on the path is a benchmark nobody
declared, and the numbers it reports would carry that provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class BenchmarkProvider(Protocol):
    """What a benchmark has to answer. Deliberately small."""

    def suites(self) -> list[dict[str, Any]]: ...
    def matrix(self, **policy: Any) -> list[dict[str, Any]]: ...
    def sweep(self, suite: str, **policy: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Benchmark:
    """A registered benchmark: identity for the reader, callables for the caller."""

    id: str
    title: str
    source: str
    description: str
    suites: Callable[[], list[dict[str, Any]]]
    matrix: Callable[..., list[dict[str, Any]]]
    sweep: Callable[..., dict[str, Any]]
    #: which suite names it offers, for validating a request before running it
    suite_ids: Callable[[], tuple[str, ...]]


def _agentdojo() -> Benchmark:
    from lab_adapters import agentdojo_bench as bench
    from lab_adapters.agentdojo_taxonomy import TAXONOMIES

    def suites() -> list[dict[str, Any]]:
        out = []
        for name in bench.SUITES:
            data = bench.suite_data(name)
            taxonomy = TAXONOMIES[name]
            out.append({
                "suite": name,
                "note": taxonomy.note,
                "user_tasks": len(data["user_tasks"]),          # type: ignore[arg-type]
                "injection_tasks": len(data["injection_tasks"]),  # type: ignore[arg-type]
                "tools": data["tools"],
                "secret_candidates": bench.candidates(name),
                "default_secrets": sorted(taxonomy.sensitive_sources),
                "egress_sinks": {k: list(v) for k, v in taxonomy.egress_sinks.items()},
                "untrusted_sources": sorted(taxonomy.untrusted_sources),
                "reference_denials": bench.REFERENCE_DENIALS[name],
            })
        return out

    return Benchmark(
        id="agentdojo",
        title="AgentDojo",
        source=str(bench.dataset()["dataset_version"]),
        description=(
            "Four suites of realistic agent tasks with prompt-injection attacks, "
            "replayed through the real governor over the benchmark's own ground truth."
        ),
        suites=suites,
        matrix=bench.matrix,
        sweep=bench.sweep,
        suite_ids=lambda: tuple(bench.SUITES),
    )


#: id → factory. Lazy so importing the registry does not read every dataset off
#: disk; a provider is built the first time someone asks for it.
_FACTORIES: dict[str, Callable[[], Benchmark]] = {"agentdojo": _agentdojo}
_CACHE: dict[str, Benchmark] = {}

DEFAULT = "agentdojo"


def register(benchmark_id: str, factory: Callable[[], Benchmark]) -> None:
    """Add a provider. Refuses to shadow an existing id.

    Silently replacing one would mean a result labelled `agentdojo` came from
    something else — the provenance failure this whole harness is built to
    avoid.
    """
    if benchmark_id in _FACTORIES:
        raise ValueError(f"benchmark {benchmark_id!r} is already registered")
    _FACTORIES[benchmark_id] = factory


def available() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def get(benchmark_id: str | None = None) -> Benchmark:
    """The provider, defaulting to AgentDojo."""
    key = benchmark_id or DEFAULT
    if key not in _FACTORIES:
        raise KeyError(key)
    if key not in _CACHE:
        _CACHE[key] = _FACTORIES[key]()
    return _CACHE[key]
