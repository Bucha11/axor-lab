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

from lab_adapters.agentdojo_bench import (  # noqa: E402
    REFERENCE_DENIALS, TAXONOMIES, candidates, dataset, load_secrets, run_suite,
)

# the measurement lives in lab_adapters.agentdojo_bench and is shared with the
# HTTP API — a scoring loop that exists twice is two sets of numbers


def sweep_secrets(name: str, allowlist: bool) -> int:
    """Print the marginal cost of each candidate secret declaration."""
    from lab_adapters.agentdojo_bench import sweep

    report = sweep(name, allowlist)
    base = report["baseline"]
    print(f"marginal cost of each secret declaration — {name}\n")
    print(f"baseline (integrity only): "
          f"utility {_rate(base['utility']['governed'], base['utility']['base'])}"
          f" · ASR {_rate(base['asr']['governed'], base['asr']['base'])}"
          f" · {base['denials']} denials\n")
    print(f"{'declared secret source':<34}{'utility':>10}{'ASR':>8}{'denials':>9}  cost")
    print("-" * 72)
    for row in report["rows"]:
        marker = "free" if row["cost_pp"] <= 0 else f"-{row['cost_pp']:.1f}pp"
        print(f"{row['source']:<34}"
              f"{_rate(row['utility']['governed'], row['utility']['base']):>10}"
              f"{_rate(row['asr']['governed'], row['asr']['base']):>8}"
              f"{row['denials']:>9}  {marker}")
    done = report["combined"]
    print("-" * 72)
    print(f"{'ALL of the above together':<34}"
          f"{_rate(done['utility']['governed'], done['utility']['base']):>10}"
          f"{_rate(done['asr']['governed'], done['asr']['base']):>8}"
          f"{done['denials']:>9}")
    print("\nA source that costs `free` is one no benign task reads before an egress —"
          "\ndeclaring it is pure upside. A source with a cost and no ASR movement is"
          "\nbuying protection this benchmark cannot show; that may still be the right"
          "\ncall, but it should be a decision, not a default.")
    return 0


def _rate(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confidentiality", action="store_true",
                        help="arm the sensitive-read floor (the reference's rows are "
                             "integrity-only, so this is OFF by default)")
    parser.add_argument("--allowlist", action="store_true",
                        help="declare the banking known-payee enum (App. D supersession)")
    parser.add_argument("--secrets", metavar="FILE",
                        help="your own secret declaration, {suite: [tool, ...]} as JSON; "
                             "replaces the taxonomy default")
    parser.add_argument("--sweep-secrets", metavar="SUITE",
                        help="report the MARGINAL cost of declaring each candidate "
                             "secret source in SUITE, one at a time")
    args = parser.parse_args(argv)

    if args.sweep_secrets:
        return sweep_secrets(args.sweep_secrets, args.allowlist)

    index = dataset()
    print(f"dataset: {index['dataset_version']} ({index['suite_version']})")
    print("policy:  strict/content-ledger"
          f"{' + banking known-payee allowlist' if args.allowlist else ''}"
          f"{' + confidentiality floor' if args.confidentiality else ''}\n")
    header = (f"{'suite':<11}{'utility':>18}{'ASR':>16}{'denials':>9}  "
              f"{'reference':<32}")
    print(header)
    print("-" * len(header))
    declared = load_secrets(args.secrets)
    for name in ("banking", "slack", "workspace", "travel"):
        r = run_suite(name, args.allowlist,
                      args.confidentiality or bool(declared),
                      declared.get(name) if declared else None)
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
