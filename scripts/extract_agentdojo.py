#!/usr/bin/env python3
"""Freeze the AgentDojo benchmark into a dataset Lab can run without it installed.

    pip install agentdojo==0.1.35
    python scripts/extract_agentdojo.py --out lab_adapters/agentdojo_data

Why extract rather than depend: Lab's core is deliberately stdlib-only, and a
benchmark that changes under you is not a benchmark. This writes the ground
truth — every task's prompt, its ground-truth call sequence with concrete
arguments, the injection vectors and their defaults — as content-hashed JSON,
so a Lab run is reproducible on a machine that has never heard of AgentDojo and
a change in the upstream package shows up as a hash change rather than as
silently different numbers.

This is the same pattern as `lab_contracts/schemas/` (a build-time copy of the
source of truth, kept honest by a test) and `lab_server/web/`.

`utility()` and `security()` ARE extracted, despite being Python predicates over
a mutated environment. The trick is that a governed run can only differ from an
ungoverned one by which GATED calls were suppressed, and there are at most four
of those in any task of any suite — so the extractor evaluates the predicate
once per subset of gated calls and freezes the whole map. Lab then looks up the
outcome for the denial set its gate actually produced. Sixteen environment runs
per task in the worst case, and the ground truth stops being a thing Lab has to
approximate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# the taxonomy lives in this repo and decides which calls a gate could refuse,
# so the outcome maps are computed over exactly that set — one source of truth
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SUITES = ("banking", "slack", "travel", "workspace")

#: 2**4 = 16 environment runs per task; above this the map is skipped
MAX_GATED = 4


def _jsonable(value: Any) -> Any:
    """AgentDojo args carry pydantic models and enums; flatten to plain JSON."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    for attribute in ("model_dump", "dict"):
        dump = getattr(value, attribute, None)
        if callable(dump):
            try:
                return _jsonable(dump())
            except TypeError:
                pass
    if hasattr(value, "value"):  # enum
        return _jsonable(value.value)
    return str(value)


def _calls(task: Any, environment: Any, runtime: Any) -> list[dict[str, Any]] | None:
    """The ground-truth call sequence WITH each call's real result.

    The results are the point. A content-ledger trust model derives taint by
    matching a sink argument against what an untrusted read actually returned,
    so a dataset of calls without results cannot reproduce a single verdict —
    it would only record what the benchmark intended, not what flowed.

    Executing them also runs the task for real against a fresh environment, so
    the recorded results are the ones the tools produce, not a reconstruction.

    None (not an empty list) when the task declines to give a ground truth:
    'no calls' and 'could not be determined' lead to opposite conclusions about
    a denial count.
    """
    try:
        calls = task.ground_truth(environment)
    except Exception as exc:  # noqa: BLE001 - upstream raises bare exceptions
        print(f"    ground_truth unavailable for {task.ID}: {exc}", file=sys.stderr)
        return None
    recorded = []
    for call in calls:
        args = _jsonable(dict(call.args))
        result: Any = None
        error: str | None = None
        try:
            value, err = runtime.run_function(
                environment, call.function, dict(call.args), raise_on_error=False,
            )
            result, error = _jsonable(value), (str(err) if err else None)
        except Exception as exc:  # noqa: BLE001 - a tool may reject the state
            error = f"{type(exc).__name__}: {exc}"
        recorded.append({"tool": call.function, "args": args,
                         "result": result, "error": error})
    return recorded


def _gated_indices(calls: list[dict[str, Any]], taxonomy: Any) -> list[int]:
    """Which calls a gate could refuse — the only ones a governed run can lose."""
    return [
        i for i, call in enumerate(calls)
        if call["tool"] in taxonomy.egress_sinks or call["tool"] in taxonomy.consequence
    ]


def _outcome_map(
    task: Any, calls: list[dict[str, Any]], taxonomy: Any, suite: Any, runtime: Any,
    predicate: str,
) -> dict[str, bool]:
    """Evaluate `utility`/`security` for every subset of gated calls suppressed.

    The key is the sorted, comma-joined indices of the SUPPRESSED calls, so ""
    is the ungoverned run. Lab reads the entry for whatever its gate denied.

    Capped at 2**MAX_GATED subsets; a task above that is recorded without a map
    rather than with a partial one, since a missing key would silently read as
    "utility held".
    """
    import itertools

    gated = _gated_indices(calls, taxonomy)
    if len(gated) > MAX_GATED:
        print(f"    {task.ID}: {len(gated)} gated calls exceeds the cap — no map",
              file=sys.stderr)
        return {}
    expected = getattr(task, "GROUND_TRUTH_OUTPUT", "") or ""
    outcomes: dict[str, bool] = {}
    for size in range(len(gated) + 1):
        for suppressed in itertools.combinations(gated, size):
            environment = suite.load_and_inject_default_environment(
                suite.get_injection_vector_defaults()
            )
            pre = environment.model_copy(deep=True)
            for index, call in enumerate(calls):
                if index in suppressed:
                    continue  # the gate refused it
                try:
                    runtime.run_function(
                        environment, call["tool"], dict(call["args"]), raise_on_error=False,
                    )
                except Exception:  # noqa: BLE001 - a tool may reject the state
                    pass
            try:
                check = getattr(task, predicate)
                outcomes[",".join(map(str, suppressed))] = bool(
                    check(expected, pre, environment)
                )
            except Exception as exc:  # noqa: BLE001 - upstream predicates raise freely
                print(f"    {task.ID}: {predicate} raised: {exc}", file=sys.stderr)
                return {}
    return outcomes


def _attack_vectors(suite: Any) -> dict[str, dict[str, str]]:
    """The `important_instructions` payload per injection task — the reference's attack.

    `attack()` needs a target pipeline only to name the model and user in the
    prompt, so a stub with a name is enough; the payload's security-relevant part
    is the attacker's GOAL, which carries the destination.
    """
    from agentdojo.attacks.important_instructions_attacks import ImportantInstructionsAttack

    from agentdojo.models import MODEL_NAMES

    class _Stub:
        # the attack interpolates a victim-model name into the injected
        # instruction and looks it up by SUBSTRING over MODEL_NAMES' keys (the
        # full model ids), not its values — so the stub carries a real key
        name = next(iter(MODEL_NAMES))

    try:
        attack = ImportantInstructionsAttack(suite, _Stub())
    except Exception as exc:  # noqa: BLE001
        print(f"    attack unavailable: {exc}", file=sys.stderr)
        return {}
    user_task = next(iter(suite.user_tasks.values()))
    vectors: dict[str, dict[str, str]] = {}
    for injection in suite.injection_tasks.values():
        try:
            vectors[injection.ID] = {
                str(k): str(v) for k, v in attack.attack(user_task, injection).items()
            }
        except Exception as exc:  # noqa: BLE001
            print(f"    attack payload unavailable for {injection.ID}: {exc}",
                  file=sys.stderr)
    return vectors


def extract_suite(name: str, suite: Any) -> dict[str, Any]:
    from agentdojo.functions_runtime import FunctionsRuntime

    vectors = {k: _jsonable(v) for k, v in suite.get_injection_vector_defaults().items()}
    runtime = FunctionsRuntime(suite.tools)

    def fresh() -> Any:
        """A task must not observe another task's mutations.

        Executing ground truth WRITES to the environment (a transfer is sent, a
        file is deleted). Sharing one environment across tasks would make the
        recorded results depend on extraction order.
        """
        return suite.load_and_inject_default_environment(
            suite.get_injection_vector_defaults()
        )

    from lab_adapters.agentdojo_taxonomy import TAXONOMIES

    taxonomy = TAXONOMIES[name]
    user_tasks = []
    for task in suite.user_tasks.values():
        calls = _calls(task, fresh(), runtime)
        user_tasks.append({
            "id": task.ID,
            "prompt": task.PROMPT,
            "calls": calls,
            "comment": getattr(task, "COMMENT", None),
            # utility under every subset of gated calls a governed run could lose
            "utility": (_outcome_map(task, calls, taxonomy, suite, runtime, "utility")
                        if calls else {}),
        })
    attack_vectors = _attack_vectors(suite)
    injection_tasks = []
    for task in suite.injection_tasks.values():
        calls = _calls(task, fresh(), runtime)
        injection_tasks.append({
            # the ATTACK payload as it lands in the environment. Without it the
            # attacker's destination appears in no read, nothing is tainted, and
            # every suite reports ASR 100% for a reason that is purely an
            # artifact of the harness.
            "attack_vectors": attack_vectors.get(task.ID, {}),
            "id": task.ID,
            "goal": task.GOAL,
            "calls": calls,
            "comment": getattr(task, "COMMENT", None),
            # security() is True when the ATTACK SUCCEEDED, so "" is the
            # ungoverned arm and a suppressed sink should flip it to False
            "security": (_outcome_map(task, calls, taxonomy, suite, runtime, "security")
                         if calls else {}),
        })
    tools = sorted(suite.tools.keys()) if hasattr(suite.tools, "keys") else sorted(
        t.name for t in suite.tools
    )
    return {
        "suite": name,
        "tools": tools,
        "injection_vectors": vectors,
        "user_tasks": sorted(user_tasks, key=lambda t: t["id"]),
        "injection_tasks": sorted(injection_tasks, key=lambda t: t["id"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="lab_adapters/agentdojo_data")
    parser.add_argument("--version", default="v1", help="AgentDojo suite version")
    args = parser.parse_args(argv)

    try:
        from importlib.metadata import version as _pkg_version

        from agentdojo.task_suite.load_suites import get_suites
    except ImportError:  # pragma: no cover - the whole point of the script
        raise SystemExit("agentdojo is not installed: pip install agentdojo==0.1.35")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    suites = get_suites(args.version)
    index: dict[str, Any] = {
        # the INSTALLED distribution version, not a module attribute the
        # package may not define — the dataset's provenance has to be exact
        "dataset_version": f"agentdojo@{_pkg_version('agentdojo')}",
        "suite_version": args.version,
        "suites": {},
    }
    for name in SUITES:
        if name not in suites:
            print(f"  {name}: absent from this AgentDojo build — skipped", file=sys.stderr)
            continue
        print(f"  extracting {name}…")
        data = extract_suite(name, suites[name])
        body = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        (out / f"{name}.json").write_text(body)
        index["suites"][name] = {
            "content_hash": "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
            "user_tasks": len(data["user_tasks"]),
            "injection_tasks": len(data["injection_tasks"]),
            "tools": len(data["tools"]),
            "ground_truth_missing": sum(
                1 for t in data["user_tasks"] + data["injection_tasks"] if t["calls"] is None
            ),
        }
    (out / "index.json").write_text(
        json.dumps(index, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    )
    for name, meta in index["suites"].items():
        print(f"  {name:<10} {meta['user_tasks']:>3} user  {meta['injection_tasks']:>2} injection  "
              f"{meta['tools']:>2} tools  {meta['content_hash'][:19]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
