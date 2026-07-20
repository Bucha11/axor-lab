"""Contract/runtime parity cleanup (review round 6, Patch 19).

- count: runtime evaluates it → the validator must accept it (covered in
  test_contract_parity.py).
- duplicate condition ids are rejected.
- an inline manifest that conflicts (same id, different content) with a
  registered one is rejected.
- the local CLI publish and the server produce the SAME decision-derived DENY
  claim (one shared renderer, not two code paths).
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests import support
from lab_runner import ScriptedAgent, run_trial
from lab_runner.claims import deny_claim_text
from lab_runner.errors import ExperimentFileError
from lab_runner.experiment_file import resolve

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "banking-exfil-01.axl"


class TestDuplicateConditionId(unittest.TestCase):
    def test_duplicate_condition_id_is_rejected(self) -> None:
        doc = json.loads(EXAMPLE.read_text())
        conditions = doc["experiment"]["conditions"]
        dup = copy.deepcopy(next(c for c in conditions if c["enforcement"] == "on"))
        dup["policy"] = {"allowlist": ["someone@example.com"]}  # different policy, same id
        dup.pop("config_hash", None)
        conditions.append(dup)
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("duplicate condition id" in e for e in ctx.exception.errors))


class TestInlineManifestConflict(unittest.TestCase):
    def test_conflicting_inline_manifest_is_rejected(self) -> None:
        doc = json.loads(EXAMPLE.read_text())
        scenario = doc["scenarios"][0]
        # add an inline manifest that reuses read_txns' id with DIFFERENT content
        conflicting = copy.deepcopy(support.manifests()["read_txns"])
        conflicting["untrusted_fields"] = ["result.transactions[].amount"]  # changed
        scenario["tools"].append(conflicting)
        with self.assertRaises(ExperimentFileError) as ctx:
            resolve(doc)
        self.assertTrue(any("conflicts" in e for e in ctx.exception.errors))


class TestUnifiedDenyClaim(unittest.TestCase):
    def test_local_and_server_use_the_same_deny_claim_text(self) -> None:
        # both paths call the same deny_claim_text; verify it is decision-derived
        # (names the real gate) and NOT the old template
        trace = run_trial(
            support.banking_scenario(), support.manifests(), support.conditions()[1],
            support.kernel_registry().get(support.KERNEL_PINNED),
            run_id="r", seed="s000", repeat_index=0, agent=ScriptedAgent(attack_rate=1.0),
        ).trace
        text = deny_claim_text(trace)
        self.assertIn("taint_floor", text)         # the recorded gate
        self.assertNotIn("the driving argument is untrusted_derived.", text)  # not the template

        # and the server imports the very same function
        from lab_server.store import _deny_claim_text
        self.assertIs(_deny_claim_text, deny_claim_text)


if __name__ == "__main__":
    unittest.main()
