"""End-to-end tests of the `axor-lab` CLI (runner-protocol.md), run as real
subprocesses: validate → run → replay → pin → regress → evidence → publish
over the shipped example experiment. Exit codes are part of the contract:
0 ok · 1 integrity failure · 2 validation errors · 3 unconfirmed · 4 pin
differs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import support

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "banking-exfil-01.axl"
REPEATS = 12
CREATED = "2026-07-19T12:00:00+00:00"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_VALIDATION = 2
EXIT_UNCONFIRMED = 3
EXIT_REGRESSION_DIFFERS = 4


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lab_runner", *args],
        capture_output=True, text=True, cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
    )


class TestCliEndToEnd(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    bundle_dir: Path
    denied_trace_id: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        # a faster copy of the shipped example
        document = json.loads(EXAMPLE.read_text())
        document["experiment"]["repeats"] = REPEATS
        cls.axl = root / "experiment.axl"
        cls.axl.write_text(json.dumps(document))
        cls.bundle_dir = root / "bundle"
        run = _cli(
            "run", str(cls.axl), "--out", str(cls.bundle_dir), "--yes", "--created", CREATED
        )
        assert run.returncode == EXIT_OK, run.stderr
        cls.run_stdout = run.stdout
        traces = [
            json.loads(p.read_text()) for p in sorted((cls.bundle_dir / "traces").glob("*.json"))
        ]
        cls.denied_trace_id = next(
            str(t["trace_id"]) for t in traces
            if any(
                e.get("type") == "gate_decision" and e["decision"]["verdict"] == "DENY"
                for e in t["events"]
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    # -- validate ---------------------------------------------------------

    def test_validate_accepts_the_example(self) -> None:
        result = _cli("validate", str(EXAMPLE))
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("valid: exp_banking_01", result.stdout)

    def test_validate_rejects_with_specific_stage_errors(self) -> None:
        document = json.loads(EXAMPLE.read_text())
        document["experiment"]["scenario_ids"] = ["no-such-scenario"]
        document["experiment"]["agent_ref"] = "gpt-blackbox"
        bad = Path(self.tmp.name) / "bad.axl"
        bad.write_text(json.dumps(document))
        result = _cli("validate", str(bad))
        self.assertEqual(result.returncode, EXIT_VALIDATION)
        self.assertIn("'no-such-scenario' not among scenarios", result.stderr)
        self.assertIn("unknown agent_ref", result.stderr)
        self.assertIn("stage: validating", result.stderr)

    # -- run --------------------------------------------------------------

    def test_run_without_confirmation_executes_nothing(self) -> None:
        out = Path(self.tmp.name) / "unconfirmed"
        result = _cli("run", str(self.axl), "--out", str(out))
        self.assertEqual(result.returncode, EXIT_UNCONFIRMED)
        self.assertIn("nothing ran", result.stderr)
        self.assertFalse(out.exists())

    def test_run_walks_the_lifecycle_states(self) -> None:
        for state in ("[validating]", "[estimate]", "[running_local]",
                      "[analyzing]", "[uploading_artifacts]", "[completed]"):
            self.assertIn(state, self.run_stdout)
        self.assertIn("$0.00", self.run_stdout)  # scripted → no paid inference
        self.assertIn(f"n={REPEATS * 2}/{REPEATS * 2}", self.run_stdout)

    def test_run_wrote_a_schema_valid_bundle_dir(self) -> None:
        bundle = json.loads((self.bundle_dir / "bundle.json").read_text())
        self.assertEqual(support.schema_errors(bundle, "bundle"), [])
        self.assertEqual(bundle["packaging"]["layout"], "axor-bundle-dir/v1")
        self.assertEqual(bundle["created"], CREATED)
        trace_files = list((self.bundle_dir / "traces").glob("*.json"))
        self.assertEqual(len(trace_files), REPEATS * 2)
        for path in trace_files[:3]:
            self.assertEqual(support.schema_errors(json.loads(path.read_text()), "trace"), [])

    # -- replay -----------------------------------------------------------

    def test_replay_is_bit_identical(self) -> None:
        result = _cli("replay", str(self.bundle_dir))
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("bit-identical", result.stdout)

    def test_replay_rejects_a_tampered_bundle(self) -> None:
        import shutil

        tampered = Path(self.tmp.name) / "tampered"
        shutil.copytree(self.bundle_dir, tampered)
        # trace files are named by content hash now, so locate the victim by
        # its trace_id rather than assuming the filename
        victim = next(
            p for p in (tampered / "traces").glob("*.json")
            if json.loads(p.read_text())["trace_id"] == self.denied_trace_id
        )
        trace = json.loads(victim.read_text())
        for event in trace["events"]:
            if event.get("type") == "gate_decision":
                event["decision"]["verdict"] = "ALLOW"
        victim.write_text(json.dumps(trace))
        result = _cli("replay", str(tampered))
        self.assertEqual(result.returncode, EXIT_FAILURE)
        self.assertIn("integrity", result.stderr.lower())

    # -- pin + regress ----------------------------------------------------

    def test_pin_then_regress_matches_and_variant_kernel_surfaces(self) -> None:
        pins = Path(self.tmp.name) / "pins.json"
        pin_result = _cli("pin", str(self.bundle_dir), self.denied_trace_id, "DENY",
                          "--out", str(pins))
        self.assertEqual(pin_result.returncode, EXIT_OK, pin_result.stderr)

        ok = _cli("regress", str(self.bundle_dir), "--pins", str(pins))
        self.assertEqual(ok.returncode, EXIT_OK, ok.stderr)
        self.assertIn("match expected", ok.stdout)

        flipped = _cli("regress", str(self.bundle_dir), "--pins", str(pins),
                       "--disable-taint-floor")
        self.assertEqual(flipped.returncode, EXIT_REGRESSION_DIFFERS)
        self.assertIn("differs_from_pinned_expected", flipped.stdout)
        self.assertIn("label each as regression or approved baseline update", flipped.stderr)

    # -- evidence ---------------------------------------------------------

    def test_evidence_renders_three_mode_case(self) -> None:
        result = _cli("evidence", str(self.bundle_dir), self.denied_trace_id)
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        case = json.loads(result.stdout)
        self.assertIn("observed", case["modes"])
        self.assertIn("counterfactual_policy_replay", case["modes"])
        self.assertNotIn("observed_governed_twin", case["modes"])
        self.assertEqual(case["chain"]["gated_call"]["tool"], "send_money")

    # -- publish ----------------------------------------------------------

    def test_publish_mints_a_schema_valid_typed_publication(self) -> None:
        out = Path(self.tmp.name) / "publication.json"
        result = _cli("publish", str(self.bundle_dir),
                      "--question", "Does governance stop the exfil?", "--out", str(out))
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        publication = json.loads(out.read_text())
        self.assertEqual(support.schema_errors(publication, "publication"), [])
        self.assertEqual(publication["origin"], "local")
        self.assertEqual(publication["integrity"], "hash_verified")
        kinds = {c["kind"] for c in publication["claims"]}
        # local publish proves REPLAY only — it does not recompute the aggregates,
        # so it must NOT assert a statistically_reproducible claim over
        # self-reported numbers (that's the server's job). Only the replay claim
        # is minted; statistics_integrity is absent (review r12).
        self.assertEqual(kinds, {"exactly_replayable"})
        self.assertNotIn("statistics_integrity", publication)

    def test_local_publication_id_content_addresses_the_whole_body(self) -> None:
        # the SAME bundle published with different metadata is a genuinely
        # different publication — different id + reproductions_ref (review r12).
        out_a = Path(self.tmp.name) / "pub_a.json"
        out_b = Path(self.tmp.name) / "pub_b.json"
        _cli("publish", str(self.bundle_dir), "--question", "Question A",
             "--visibility", "public", "--out", str(out_a))
        _cli("publish", str(self.bundle_dir), "--question", "Completely different claim",
             "--visibility", "private", "--out", str(out_b))
        a = json.loads(out_a.read_text())
        b = json.loads(out_b.read_text())
        self.assertNotEqual(a["publication_id"], b["publication_id"])
        self.assertNotEqual(a["reproductions_ref"], b["reproductions_ref"])
        self.assertEqual(a["bundle_ref"], b["bundle_ref"])  # same evidence


if __name__ == "__main__":
    unittest.main()
