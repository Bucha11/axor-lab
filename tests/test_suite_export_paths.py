"""A suite can be handed to someone — production, or a reader.

Every export in this repo grew on the `.axl` path and then stopped being fed.
The Suite Platform could run, measure, and dispatch, and could not:

  - export to the Control Plane at all (`no recorded config_hash`),
  - earn the production bridge even when governance demonstrably worked
    (no attested `experiment_design`, and a drop-fraction guard that read a
    scenario with no attack model as a missing pair),
  - be published to a server (`400 malformed request: 'violation'`, then
    `unknown metric 'task_success'`).

Each was a field or a name only the legacy path supplied, so nothing failed
loudly until someone tried to hand the evidence over. These pin the whole
chain against a built-in, because a built-in is what a reviewer reaches for.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lab_contracts import condition_config_hash
from lab_suite import (
    build_assignment,
    builtin_registry,
    comparison_design,
    plan_suite,
    resolve_suite,
    run_suite,
    validate_manifest,
)
from lab_suite.sdk import BaseSuite


def _ingest() -> dict:
    return builtin_registry().get("ingest").manifest()


class TestEveryArmCarriesItsAnchor(unittest.TestCase):
    """`condition.config_hash` — "sha256 over the normalized (kernel + policy)".

    A Control-Plane export REFUSES an arm without one rather than synthesize the
    carry-over key and present it as the measured config. Only the `.axl` path
    set it, so every suite was undeployable."""

    def test_a_resolved_arm_is_stamped(self) -> None:
        for condition in resolve_suite(_ingest()).conditions:
            with self.subTest(arm=condition["id"]):
                self.assertEqual(
                    condition["config_hash"],
                    condition_config_hash(str(condition["kernel"]), condition.get("policy")),
                )

    def test_a_dispatched_arm_is_stamped_too(self) -> None:
        for condition in build_assignment(_ingest(), "rt_x").conditions:
            self.assertTrue(condition.get("config_hash"), condition["id"])

    def test_the_synthesized_ungoverned_arm_is_stamped(self) -> None:
        """A suite declaring no conditions gets one made for it; a baseline with
        no anchor makes the export refuse just as surely."""
        planned = plan_suite(builtin_registry().get("blank").manifest())
        self.assertEqual([c["id"] for c in planned.conditions], ["ungoverned"])
        self.assertTrue(planned.conditions[0].get("config_hash"))

    def test_resolving_does_not_edit_the_manifest_it_was_handed(self) -> None:
        manifest = _ingest()
        resolve_suite(manifest)
        for condition in manifest["execution"]["conditions"]:  # type: ignore[index,union-attr]
            self.assertNotIn("config_hash", condition)

    def test_a_hand_written_anchor_must_recompute(self) -> None:
        """It is the reproducibility anchor a production handoff carries, and
        nobody re-derives it downstream — that is the point of an anchor."""
        manifest = _ingest()
        conditions = [dict(c) for c in manifest["execution"]["conditions"]]  # type: ignore[index,union-attr]
        conditions[1]["config_hash"] = "sha256:" + "0" * 64
        manifest["execution"] = {**manifest["execution"], "conditions": conditions}  # type: ignore[index,dict-item]
        errors = validate_manifest(manifest)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("name a config that did not run", errors[0])


class TestTheComparisonDesignIsAttested(unittest.TestCase):
    """The CP bridge reads `environment.experiment_design`, never an aggregate —
    "never a silent default to matched_pairs". A suite bundle carried none, so
    the bridge could not be earned by any suite, ever."""

    def test_a_seed_determined_suite_attests_matched_pairs(self) -> None:
        design = comparison_design(builtin_registry().get("ingest"))
        self.assertEqual(design["kind"], "matched_pairs")
        self.assertIs(design["agent_deterministic"], True)

    def test_a_suite_that_does_not_answer_gets_the_weaker_claim(self) -> None:
        class Unanswered(BaseSuite):
            id = "x"

        design = comparison_design(Unanswered())
        self.assertEqual(design["kind"], "independent_samples")
        self.assertIs(design["agent_deterministic"], False)

    def test_a_connected_runtime_is_always_independent(self) -> None:
        """Lab does not know what ran on someone else's machine. A live model
        draws each condition separately, and a paired design asserted over that
        is a spurious p-value with a signature on it."""
        design = comparison_design(
            builtin_registry().get("ingest"), executed_by_runtime=True,
        )
        self.assertEqual(design["kind"], "independent_samples")
        self.assertIs(design["agent_deterministic"], False)


class TestAScenarioWithNoAttackModel(unittest.TestCase):
    """Representable by design (RFC §6) — and it broke every consumer that
    reached for `scenario["violation"]` without asking.

    The mix is not exotic: the clean scenario is what proves containment did not
    break the job, so a suite that measures containment honestly is exactly the
    one that could not be exported or published."""

    def _mixed(self) -> tuple[dict, dict]:
        manifest = _ingest()
        run = run_suite(manifest, run_id="r_export", suite=builtin_registry().get("ingest"))
        return manifest, run.artifact(
            artifact_id="a_export", created="2026-09-13T00:00:00+00:00",
            environment={
                "model": {"provider": "scripted", "id": "ingest"},
                "experiment_design": comparison_design(builtin_registry().get("ingest")),
            },
        )

    def test_the_bridge_counts_only_units_that_could_have_violated(self) -> None:
        """A scenario with no breach predicate was never a candidate pair, so
        counting it as a DROPPED one tripped the missingness guard (12 of 36)
        and refused the bridge for a run where governance worked perfectly."""
        from lab_capabilities.governance.cp_export import _arm_coords

        _, artifact = self._mixed()
        bundle: dict = artifact["bundle"]  # type: ignore[assignment]
        coords = _arm_coords(bundle, "ungoverned")
        self.assertEqual({c[0] for c in coords},
                         {"ingest-exfil-attachment", "ingest-exfil-subject"})

    def test_the_export_earns_its_bridge(self) -> None:
        from lab_capabilities.governance.cp_export import export_cp

        from lab_capabilities.governance.cp_export import _earned_for

        _, artifact = self._mixed()
        bundle: dict = artifact["bundle"]  # type: ignore[assignment]
        run = run_suite(_ingest(), run_id="r_export",
                        suite=builtin_registry().get("ingest"))
        export = export_cp(bundle, [], condition_id="governed_allowlist",
                           traces=run.traces)
        self.assertTrue(export.earned_bridge)
        # the receipt names the design it earned under, and the evidence
        earned, analysis = _earned_for(
            bundle, "governed_allowlist", "ungoverned", run.traces,
        )
        self.assertTrue(earned)
        self.assertEqual(analysis["comparison_design"], "matched_pairs")  # type: ignore[index]
        self.assertEqual(analysis["metric"], "ASR")  # type: ignore[index]

    def test_the_server_recompute_excludes_it_from_ASR(self) -> None:
        """ASR's denominator counts attacked trials only — identical to the
        runner's own `_aggregate`. Contributing False would recompute 17/36
        against the bundle's 17/24 and reject an honest run for a mismatch this
        code invented."""
        from lab_server.recompute import recompute_aggregates

        _, artifact = self._mixed()
        bundle: dict = artifact["bundle"]  # type: ignore[assignment]
        run = run_suite(_ingest(), run_id="r_export",
                        suite=builtin_registry().get("ingest"))
        recomputed = recompute_aggregates(bundle, run.traces)
        self.assertEqual(recomputed[("ASR", "ungoverned")]["n"], 24)
        self.assertEqual(recomputed[("task_success", "ungoverned")]["n"], 36)

    def test_it_publishes(self) -> None:
        """The end of the chain: schema, hashes, replay, and statistics
        recomputed from the traces."""
        from lab_server.store import PublicationStore

        _, artifact = self._mixed()
        bundle: dict = artifact["bundle"]  # type: ignore[assignment]
        run = run_suite(_ingest(), run_id="r_export",
                        suite=builtin_registry().get("ingest"))
        with TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            stored = store.publish(
                bundle=bundle, traces=run.traces,
                question="Does taint enforcement contain attachment-borne exfiltration?",
            )
            self.assertTrue(stored.publication["publication_id"])
            self.assertTrue(stored.publication.get("claims"))


class TestTheSuiteMetricNamesAreRecomputable(unittest.TestCase):
    def test_task_success_is_a_known_server_metric(self) -> None:
        """Three of the four built-ins name their success metric `task_success`
        — it IS `trial.metrics.task_success` — and the server's closed registry
        knew only `task_success_rate`, so the whole platform was unpublishable.
        The registry exists to stop an ARBITRARY label resolving to the
        task-success rate; this is the one name that cannot be arbitrary."""
        from lab_server.recompute import _METRIC_OUTCOME

        self.assertEqual(_METRIC_OUTCOME["task_success"], "task_success")
        self.assertEqual(_METRIC_OUTCOME["ASR"], "violation")

    def test_an_arbitrary_label_is_still_refused(self) -> None:
        from lab_server.recompute import _metric_field

        self.assertIsNone(_metric_field("zero_production_incidents"))


if __name__ == "__main__":
    unittest.main()
