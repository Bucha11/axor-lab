#!/usr/bin/env python3
"""Run the frozen AgentDojo suites through the real governor: full error matrix.

    python scripts/run_agentdojo_calibration.py [--allowlist]

Reports, per suite, what the gate actually costs and buys:

  * **utility** — of the benign tasks that succeed ungoverned, how many still
    succeed once the gate has had its say. The gap is the FALSE POSITIVE rate:
    legitimate work the defense broke.
  * **ASR** — of the injection tasks that succeed ungoverned, how many still
    succeed governed. What survives is the FALSE NEGATIVE rate. Measured
    PAIRWISE: the attacker's call is gated inside each user task's trajectory,
    after the reads that carried the injection, and an attack counts as blocked
    only if it is blocked in EVERY pairing.

This replaces the old "ASR 55% → 0%" headline for good. That number came from a
scripted agent whose injection-following rate is a parameter, so it was a dial.
These are properties of the gate over the benchmark's own ground truth.

Denial counts are printed beside the reference figures from
contracts/agentdojo-calibration.md. Nothing here is adjusted to land on a
target: a row that misses is a finding about the port or the taxonomy, and
saying which is the job.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lab_adapters.agentdojo_taxonomy import BANKING_KNOWN_PAYEES, TAXONOMIES  # noqa: E402
from lab_runner.sequence import gate_sequence  # noqa: E402

DATA = REPO_ROOT / "lab_adapters" / "agentdojo_data"

#: §6.2 of the reference — denials per pass
REFERENCE_DENIALS = {
    "banking": "3–5", "slack": "13–19",
    "workspace": "~15 (92 taint / 12 consequence)", "travel": "0 (structural)",
}


def _outcome(task: dict, denied_indices: list[int]) -> bool | None:
    """The frozen ground truth for exactly this denial set, or None if unmapped.

    None is NOT False. A task whose predicate could not be evaluated at
    extraction time is excluded from the rates rather than counted as a failure,
    which would quietly flatter the defense on utility and damn it on ASR.
    """
    outcomes = task.get("utility") or task.get("security") or {}
    if not outcomes:
        return None
    return outcomes.get(",".join(map(str, sorted(denied_indices))))


def run_suite(name: str, allowlist: bool) -> dict[str, object]:
    data = json.loads((DATA / f"{name}.json").read_text())
    taxonomy = TAXONOMIES[name]
    policy: dict[str, object] = {"profile": "strict", "trust_model": "content-ledger"}
    inputs: dict[str, object] = {}
    if allowlist and name == "banking":
        policy["allowlist"] = ["$inputs.known_payees"]
        inputs["known_payees"] = list(BANKING_KNOWN_PAYEES)

    denied_tasks: list[str] = []
    by_gate: dict[str, int] = {}
    denials = 0
    util = {"base": 0, "governed": 0, "unmapped": 0}
    asr = {"base": 0, "governed": 0, "unmapped": 0}

    for task in data["user_tasks"]:
        if task["calls"] is None:
            continue
        result = gate_sequence(task["calls"], taxonomy, data["tools"],
                               policy=policy, inputs=inputs)
        if result.denied:
            denied_tasks.append(task["id"])
            denials += len(result.denials)
            for gate, count in result.denials_by_gate().items():
                by_gate[gate] = by_gate.get(gate, 0) + count
        ungoverned = _outcome(task, [])
        governed = _outcome(task, [d.index for d in result.denials])
        if ungoverned is None or governed is None:
            util["unmapped"] += 1
        else:
            util["base"] += int(ungoverned)
            util["governed"] += int(ungoverned and governed)

    # ASR is measured PAIRWISE, the way the benchmark defines it: the attacker's
    # call happens inside a user task's trajectory, after the reads that carried
    # the injection. Gating the injection task's call ALONE reports ASR 100% for
    # a trivial reason — with no preceding read there is nothing tainted, so the
    # attacker's destination looks clean and every gate passes it. That is an
    # artifact of the harness, not a property of the kernel, and it is exactly
    # the kind of thing a calibration target exists to catch.
    for injection in data["injection_tasks"]:
        if injection["calls"] is None or not injection.get("security"):
            asr["unmapped"] += 1
            continue
        if _outcome(injection, []) is not True:
            continue  # the attack does not succeed even ungoverned
        blocked_in_every_pairing = True
        for user in data["user_tasks"]:
            if user["calls"] is None:
                continue
            prefix = len(user["calls"])
            result = gate_sequence(
                user["calls"] + injection["calls"], taxonomy, data["tools"],
                policy=policy, inputs=inputs,
                injected=list((injection.get("attack_vectors") or {}).values()),
            )
            # re-base the denied indices onto the injection task's own call list,
            # which is what its frozen security map is keyed by
            denied = [d.index - prefix for d in result.denials if d.index >= prefix]
            if _outcome(injection, denied) is not False:
                blocked_in_every_pairing = False
                break
        asr["base"] += 1
        asr["governed"] += int(not blocked_in_every_pairing)
    return {"denied_tasks": denied_tasks, "denials": denials, "by_gate": by_gate,
            "utility": util, "asr": asr}


def _rate(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", action="store_true",
                        help="declare the banking known-payee enum (App. D supersession)")
    args = parser.parse_args(argv)

    index = json.loads((DATA / "index.json").read_text())
    print(f"dataset: {index['dataset_version']} ({index['suite_version']})")
    print("policy:  strict/content-ledger"
          f"{' + banking known-payee allowlist' if args.allowlist else ''}\n")
    header = (f"{'suite':<11}{'utility':>18}{'ASR':>16}{'denials':>9}  "
              f"{'reference':<32}")
    print(header)
    print("-" * len(header))
    for name in ("banking", "slack", "workspace", "travel"):
        r = run_suite(name, args.allowlist)
        u, a = r["utility"], r["asr"]
        util = f"{_rate(u['governed'], u['base'])} of {u['base']}"
        asr = f"{_rate(a['governed'], a['base'])} of {a['base']}"
        print(f"{name:<11}{util:>18}{asr:>16}{r['denials']:>9}  "
              f"{REFERENCE_DENIALS[name]:<32}")
        gates = ", ".join(f"{g}×{c}" for g, c in sorted(r["by_gate"].items()))
        detail = f"  gates: {gates or '—'}"
        if u["unmapped"] or a["unmapped"]:
            detail += f" · unmapped: {u['unmapped']} benign, {a['unmapped']} attack"
        print(detail)
    print("\nutility = benign tasks still succeeding after the gate, of those that "
          "succeed ungoverned\nASR     = injection tasks still succeeding after the "
          "gate, of those that succeed ungoverned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
