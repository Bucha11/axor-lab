"""import-incident preserves the exact recorded condition (review r2, Patch 5).

The importer used to fabricate the condition (enforcement=on, kernel from the
trace), silently discarding the real enforcement mode / policy / config hash —
so replay after import could yield a different verdict than the incident
actually produced. It now REQUIRES the recorded condition, fully validates every
artifact, verifies the config hash, and replays before writing anything.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_runner import ScriptedAgent, run_trial

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lab_runner", *args],
        capture_output=True, text=True, cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
    )


class TestImportIncident(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.governed = support.conditions()[1]
        # a real production-style incident: attacker-controlled recipient, DENY
        trace = run_trial(
            support.banking_scenario(), support.manifests(), self.governed,
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="prod", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
        ).trace
        self.trace_path = self._write("trace.json", trace)
        self.scenario_path = self._write("scenario.json", support.banking_scenario())
        self.manifests_path = self._write("manifests.json", list(support.manifests().values()))
        self.condition_path = self._write("condition.json", self.governed)

    def _write(self, name: str, obj: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(obj))
        return path

    def _import(self, condition_path: Path, out: str = "bundle") -> subprocess.CompletedProcess[str]:
        return _cli(
            "import-incident", "--trace", str(self.trace_path),
            "--scenario", str(self.scenario_path), "--manifests", str(self.manifests_path),
            "--condition", str(condition_path), "--out", str(self.root / out),
        )

    def test_import_with_recorded_condition_succeeds_and_replays(self) -> None:
        result = self._import(self.condition_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        # the imported bundle carries the recorded condition verbatim and replays
        replay = _cli("replay", str(self.root / "bundle"))
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertIn("DENY", replay.stdout)

    def test_import_marks_runtime_provenance_reconstructed(self) -> None:
        # the imported bundle RECONSTRUCTS the runtime config hash at import — the
        # production trace never recorded it — so it must NOT masquerade as
        # recorded_at_execution, and an evidence-backed CP export must refuse it
        # (review r21 finding #5)
        import json as _json

        result = self._import(self.condition_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        bundle = _json.loads((self.root / "bundle" / "bundle.json").read_text())
        trial = next(t for t in bundle["trials"] if t["status"] == "completed")
        self.assertEqual(trial["runtime_provenance"], "reconstructed_incident")
        self.assertEqual(
            bundle["environment"]["config_provenance"]["provenance_status"],
            "reconstructed_incident",
        )

    def test_condition_is_required(self) -> None:
        result = _cli(
            "import-incident", "--trace", str(self.trace_path),
            "--scenario", str(self.scenario_path), "--manifests", str(self.manifests_path),
            "--out", str(self.root / "bundle"),
        )
        self.assertNotEqual(result.returncode, 0)  # argparse rejects the missing flag
        self.assertIn("condition", result.stderr.lower())

    def test_wrong_enforcement_condition_is_rejected_by_replay(self) -> None:
        # same id (so the cross-ref passes) but enforcement off and no stale
        # config_hash: the recorded incident DENYs, this condition would ALLOW,
        # so replay must not match and the import must refuse
        wrong = {k: v for k, v in self.governed.items() if k != "config_hash"}
        wrong["enforcement"] = "off"
        wrong_path = self._write("wrong_condition.json", wrong)
        result = self._import(wrong_path, out="bundle_wrong")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "bundle_wrong" / "bundle.json").exists())


if __name__ == "__main__":
    unittest.main()
