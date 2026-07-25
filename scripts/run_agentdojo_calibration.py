#!/usr/bin/env python3
"""Run the frozen AgentDojo suites through the real governor and report denials.

    python scripts/run_agentdojo_calibration.py [--allowlist]

Prints, per suite, how many benign tasks the gate denies and by which gate,
beside the reference figures from contracts/agentdojo-calibration.md. It reports
what the declared taxonomy yields — it does not adjust anything to land on a
target. A row that misses is a finding about the port or about the taxonomy, and
saying which is the whole job.
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
REFERENCE = {
    "banking": "3–5",
    "slack": "13–19",
    "workspace": "~15 (92 taint / 12 consequence over 7 passes)",
    "travel": "0 (structural)",
}


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
    total_denials = 0
    for task in data["user_tasks"]:
        if task["calls"] is None:
            continue
        result = gate_sequence(
            task["calls"], taxonomy, data["tools"], policy=policy, inputs=inputs,
        )
        if result.denied:
            denied_tasks.append(task["id"])
            total_denials += len(result.denials)
            for gate, count in result.denials_by_gate().items():
                by_gate[gate] = by_gate.get(gate, 0) + count
    return {
        "tasks": len(data["user_tasks"]),
        "denied_tasks": denied_tasks,
        "denials": total_denials,
        "by_gate": by_gate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", action="store_true",
                        help="declare the banking known-payee enum (App. D supersession)")
    args = parser.parse_args(argv)

    index = json.loads((DATA / "index.json").read_text())
    print(f"dataset: {index['dataset_version']} ({index['suite_version']})")
    print(f"policy:  strict/content-ledger"
          f"{' + banking known-payee allowlist' if args.allowlist else ''}\n")
    print(f"{'suite':<11}{'tasks':>6}{'denied':>8}{'denials':>9}  {'reference':<38} gates")
    results = {}
    for name in ("banking", "slack", "workspace", "travel"):
        summary = run_suite(name, args.allowlist)
        results[name] = summary
        gates = ", ".join(f"{g}×{c}" for g, c in sorted(summary["by_gate"].items())) or "—"
        print(f"{name:<11}{summary['tasks']:>6}{len(summary['denied_tasks']):>8}"
              f"{summary['denials']:>9}  {REFERENCE[name]:<38} {gates}")
    print("\nbanking denied tasks:", ", ".join(results["banking"]["denied_tasks"]) or "none")
    print("travel  denied tasks:", ", ".join(results["travel"]["denied_tasks"]) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
