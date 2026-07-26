"""HTTP surface for the AgentDojo governance benchmark.

Three questions an operator actually has, each one request:

  GET  /agentdojo            what suites exist, and which tools could be secrets
  POST /agentdojo/run        the error matrix under a given policy
  POST /agentdojo/sweep      what each secret declaration would cost me

The measurement lives in `lab_adapters.agentdojo_bench` and is shared with the
CLI. This module only validates input and shapes a response — a scoring loop
that exists twice is two sets of numbers.
"""

from __future__ import annotations

from typing import Any

from lab_adapters import agentdojo_bench as bench
from lab_adapters.agentdojo_taxonomy import TAXONOMIES

from .errors import PublishRejected


def _suite(name: object) -> str:
    if not isinstance(name, str) or name not in bench.SUITES:
        raise PublishRejected(
            f"unknown suite {name!r}; expected one of {', '.join(bench.SUITES)}",
            status=400,
        )
    return name


def _secrets(raw: object) -> dict[str, list[str]]:
    """Validate a `{suite: [tool, ...]}` declaration.

    A tool the suite does not have is refused rather than ignored: silently
    dropping it would report the cost of a policy the operator did not write,
    which is the one thing this endpoint must never do.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PublishRejected("secrets must be an object of {suite: [tool, ...]}", status=400)
    declared: dict[str, list[str]] = {}
    for name, tools in raw.items():
        suite = _suite(name)
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise PublishRejected(f"secrets[{suite}] must be a list of tool names", status=400)
        known = set(bench.suite_data(suite)["tools"])  # type: ignore[arg-type]
        unknown = sorted(set(tools) - known)
        if unknown:
            raise PublishRejected(
                f"secrets[{suite}] names tools this suite does not have: "
                f"{', '.join(unknown)}", status=400,
            )
        declared[suite] = sorted(set(tools))
    return declared


def handle_index() -> tuple[int, dict[str, object]]:
    """The suites, their sizes, and what an operator may declare."""
    index = bench.dataset()
    suites = []
    for name in bench.SUITES:
        data = bench.suite_data(name)
        taxonomy = TAXONOMIES[name]
        suites.append({
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
    return 200, {
        "dataset_version": index["dataset_version"],
        "suite_version": index["suite_version"],
        "suites": suites,
    }


def handle_run(body: dict[str, Any]) -> tuple[int, dict[str, object]]:
    """The error matrix: what the gate costs in utility and buys in ASR."""
    rows = bench.matrix(
        allowlist=bool(body.get("allowlist")),
        confidentiality=bool(body.get("confidentiality")),
        secrets=_secrets(body.get("secrets")),
    )
    return 200, {
        "policy": {
            "profile": "strict", "trust_model": "content-ledger",
            "allowlist": bool(body.get("allowlist")),
            "confidentiality": bool(body.get("confidentiality")),
            "secrets": _secrets(body.get("secrets")),
        },
        "dataset_version": bench.dataset()["dataset_version"],
        "rows": rows,
    }


def handle_sweep(body: dict[str, Any]) -> tuple[int, dict[str, object]]:
    """What would declaring each candidate secret source cost me?"""
    return 200, bench.sweep(
        _suite(body.get("suite")), allowlist=bool(body.get("allowlist")),
    )
