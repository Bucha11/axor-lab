"""HTTP surface for the benchmark library.

  GET  /benchmarks              which benchmarks exist, their suites, and which
                                tools may be declared secrets
  POST /benchmarks/run          the error matrix under a given policy
  POST /benchmarks/sweep        what each secret declaration would cost

`benchmark` defaults to `agentdojo`. The first cut of this file wired AgentDojo
straight into the path, which said "benchmark" and "AgentDojo" were the same
word; they are not, and a deployment measuring its own suites has the same
question with different task names.

The measurement lives in the registered provider
(`lab_adapters.benchmarks`) and is shared with the CLI. This module validates
input and shapes a response — a scoring loop that exists twice is two sets of
numbers.
"""

from __future__ import annotations

from typing import Any

from lab_adapters import benchmarks

from .errors import PublishRejected


def _benchmark(name: object) -> benchmarks.Benchmark:
    key = name if isinstance(name, str) and name else benchmarks.DEFAULT
    try:
        return benchmarks.get(key)
    except KeyError:
        raise PublishRejected(
            f"unknown benchmark {key!r}; registered: "
            f"{', '.join(benchmarks.available())}", status=400,
        ) from None


def _suite(bench: benchmarks.Benchmark, name: object) -> str:
    known = bench.suite_ids()
    if not isinstance(name, str) or name not in known:
        raise PublishRejected(
            f"unknown suite {name!r} for {bench.id}; expected one of {', '.join(known)}",
            status=400,
        )
    return name


def _secrets(bench: benchmarks.Benchmark, raw: object) -> dict[str, list[str]]:
    """Validate a `{suite: [tool, ...]}` declaration against this benchmark.

    A tool the suite does not have is refused rather than ignored: silently
    dropping it would report the cost of a policy the operator did not write,
    which is the one thing this endpoint must never do.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PublishRejected("secrets must be an object of {suite: [tool, ...]}", status=400)
    by_suite = {s["suite"]: s for s in bench.suites()}
    declared: dict[str, list[str]] = {}
    for name, tools in raw.items():
        suite = _suite(bench, name)
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise PublishRejected(f"secrets[{suite}] must be a list of tool names", status=400)
        unknown = sorted(set(tools) - set(by_suite[suite]["tools"]))
        if unknown:
            raise PublishRejected(
                f"secrets[{suite}] names tools this suite does not have: "
                f"{', '.join(unknown)}", status=400,
            )
        declared[suite] = sorted(set(tools))
    return declared


def handle_index() -> tuple[int, dict[str, object]]:
    """Every registered benchmark, its suites, and what may be declared."""
    catalogue = []
    for benchmark_id in benchmarks.available():
        bench = benchmarks.get(benchmark_id)
        catalogue.append({
            "benchmark": bench.id,
            "title": bench.title,
            "source": bench.source,
            "description": bench.description,
            "suites": bench.suites(),
        })
    return 200, {"default": benchmarks.DEFAULT, "benchmarks": catalogue}


def handle_run(body: dict[str, Any]) -> tuple[int, dict[str, object]]:
    """The error matrix: what the gate costs in utility and buys in ASR."""
    bench = _benchmark(body.get("benchmark"))
    secrets = _secrets(bench, body.get("secrets"))
    chosen = body.get("suites")
    if chosen is not None:
        if not isinstance(chosen, list) or not chosen:
            raise PublishRejected("suites must be a non-empty list", status=400)
        chosen = [_suite(bench, s) for s in chosen]
    rows = bench.matrix(
        allowlist=bool(body.get("allowlist")),
        confidentiality=bool(body.get("confidentiality")),
        secrets=secrets,
        suites=chosen,
    )
    return 200, {
        "benchmark": bench.id,
        "source": bench.source,
        # the result carries its own configuration: a matrix screenshotted
        # without its policy is unreadable a week later
        "policy": {
            "profile": "strict", "trust_model": "content-ledger",
            "allowlist": bool(body.get("allowlist")),
            "confidentiality": bool(body.get("confidentiality")),
            "secrets": secrets,
            "suites": chosen or [s["suite"] for s in bench.suites()],
        },
        "rows": rows,
    }


def handle_sweep(body: dict[str, Any]) -> tuple[int, dict[str, object]]:
    """What would declaring each candidate secret source cost me?"""
    bench = _benchmark(body.get("benchmark"))
    report = bench.sweep(
        _suite(bench, body.get("suite")), allowlist=bool(body.get("allowlist")),
    )
    return 200, {"benchmark": bench.id, "source": bench.source, **report}
