"""Contract parity + misc hardening (review r15).

A mixed-kernel bundle must be schema-valid and readable; write_bundle_dir must
refuse a schema-invalid bundle rather than write-now/read-never; a USD-only
budget must HARD-cap provider output; condition order is counterbalanced and
recorded; the rendered curl is a runnable command; and the signature schema
describes the bytes actually signed.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_agent.cost import CostBudget
from lab_contracts import build_bundle, load_schemas, validate_artifact, verify_bundle
from lab_runner import ScriptedAgent, run_experiment_suite
from lab_runner.bundle_io import write_bundle_dir
from lab_runner.errors import RunnerError

CREATED = "2026-07-20T12:00:00+00:00"


def _env(**extra) -> dict:
    return {"model": {"provider": "scripted", "id": "scripted",
                      "inference_params": {"experiment_id": "x"}}, **extra}


class TestMixedKernelBundle(unittest.TestCase):
    def _mixed_bundle(self, env) -> dict:
        conditions = [
            {"schema_version": "condition/v1", "id": "c1", "enforcement": "off",
             "kernel": "kernel-a", "policy": {}},
            {"schema_version": "condition/v1", "id": "c2", "enforcement": "on",
             "kernel": "kernel-b", "policy": {}},
        ]
        return build_bundle(
            bundle_id="b_mixed", created=CREATED, scenarios=[], conditions=conditions,
            tool_manifests=[], environment=env, trials=[], aggregates=[], traces={},
        )

    def test_mixed_kernel_bundle_roundtrips_schema_validation(self) -> None:
        bundle = self._mixed_bundle(_env(kernel_versions=["kernel-a", "kernel-b"]))
        self.assertEqual(validate_artifact(bundle, "bundle"), [])
        verify_bundle(bundle, {})  # must NOT raise

    def test_kernel_versions_must_match_the_condition_kernels(self) -> None:
        from lab_contracts import BundleIntegrityError

        bundle = self._mixed_bundle(_env(kernel_versions=["kernel-a", "phantom"]))
        with self.assertRaises(BundleIntegrityError):
            verify_bundle(bundle, {})


class TestPreWriteSchemaValidation(unittest.TestCase):
    def test_write_bundle_dir_rejects_schema_invalid_bundle(self) -> None:
        # a bundle whose environment is missing the required 'model' — verify_bundle
        # (hash graph) would pass, but the schema would not: caught at write time
        bundle = build_bundle(
            bundle_id="b_bad", created=CREATED, scenarios=[], conditions=[],
            tool_manifests=[], environment={"kernel_version": "k"}, trials=[],
            aggregates=[], traces={},
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RunnerError) as ctx:
                write_bundle_dir(Path(tmp) / "out", bundle, {})
            self.assertIn("schema-invalid", str(ctx.exception))


class TestUsdOutputCap(unittest.TestCase):
    def test_usd_only_budget_caps_provider_output(self) -> None:
        b = CostBudget(max_usd=0.10)  # USD only, no output ceiling
        usage = {"input_tokens": 0, "output_tokens": 0}
        # no output ceiling → remaining_output_tokens is None (the old, uncapped path)
        self.assertIsNone(b.remaining_output_tokens(usage))
        # but output_cap returns a REAL finite cap bounded by the remaining USD
        cap = b.output_cap(usage, projected_input_tokens=100, model="claude-opus-4-8")
        self.assertIsNotNone(cap)
        # opus output is $75/Mtok → $0.10 buys < ~1350 tokens; definitely finite/bounded
        self.assertLess(cap, 1400)
        self.assertGreater(cap, 0)

    def test_output_cap_is_zero_when_usd_already_spent(self) -> None:
        b = CostBudget(max_usd=0.001)
        cap = b.output_cap({"input_tokens": 1_000_000, "output_tokens": 0},
                           projected_input_tokens=0, model="claude-opus-4-8")
        self.assertEqual(cap, 0)


class TestConditionCounterbalancing(unittest.TestCase):
    def test_condition_order_is_counterbalanced_and_recorded(self) -> None:
        result = run_experiment_suite(
            [support.banking_scenario()], support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=4, run_id="r_cb", agent=ScriptedAgent(),
        )
        by_order = sorted(result.trials, key=lambda t: t["execution_order"])
        # every trial records its execution order
        self.assertEqual([t["execution_order"] for t in by_order], list(range(len(by_order))))
        conds = [t["condition_id"] for t in by_order]
        # block 0 (repeat 0) and block 1 (repeat 1) run the conditions in OPPOSITE
        # order — counterbalanced, not the same declared order every block
        self.assertEqual(conds[0], conds[3])  # block0[0] == block1[1]
        self.assertEqual(conds[1], conds[2])  # block0[1] == block1[0]
        self.assertNotEqual(conds[0], conds[1])


class TestRenderedCurlAndSignatureDoc(unittest.TestCase):
    def test_rendered_curl_command_is_directly_executable(self) -> None:
        from lab_server.html import render_publication
        from lab_server.store import StoredPublication

        pub = {"publication_id": "e_abc", "visibility": "public", "claims": [],
               "integrity": "hash_verified", "bundle_ref": "sha256:x", "question": "does it?",
               "schema_version": "publication/v1", "origin": "local", "license": "CC-BY-4.0"}
        bundle = build_bundle(
            bundle_id="b", created=CREATED, scenarios=[support.banking_scenario()],
            conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
            environment=_env(kernel_version=support.KERNEL_PINNED), trials=[], aggregates=[], traces={},
        )
        html = render_publication(StoredPublication(publication=pub, bundle=bundle, traces={}))
        self.assertIn('AXOR_LAB_URL="https://', html)
        self.assertIn('curl -fsS "$AXOR_LAB_URL/api/publications/e_abc/bundle"', html)

    def test_signature_schema_describes_whole_bundle_payload(self) -> None:
        schema = load_schemas()["bundle"]
        desc = schema["properties"]["signature"]["description"].lower()
        self.assertIn("whole", desc)
        self.assertIn("signature", desc)


if __name__ == "__main__":
    unittest.main()
