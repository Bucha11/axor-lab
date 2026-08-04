"""A carried pin travels with the trace it pins.

The Control Plane's receiving side is fully built: it converts a Lab
`trace/v1` into kernel events, folds them through axor-core's own replay, and
marks a pin `replayable` only when the pinned verdict actually reproduces under
the CP's installed kernel. It reads the bodies from `regression_traces` on the
package.

Lab never wrote that key. So every pin Lab exported landed
`skipped: "package carries no trace body for this pin"`, and the entire
replayable-pin path on the receiving side had no producer — the mirror image of
a consumer with no producer. Nothing caught it: the CP's own integration test
asserts this key exists, but axor-lab is not a dependency of the CP backend, so
`importorskip` skips that module every time it runs.

The export writes bodies to a sibling `regression-traces/` directory too, and
that is not a substitute: `POST /v1/lab/deploy` takes the `cp-deploy.json`
document alone.
"""

from __future__ import annotations

import json
import unittest

from tests import support
from lab_contracts import build_bundle, content_hash
from lab_runner import ScriptedAgent, run_experiment_suite
from lab_runner.cp_export import CPExportError, export_cp

CREATED = "2026-08-04T00:00:00+00:00"


def _run_and_export(with_pins: bool = True):
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=24, run_id="r_cp", agent=ScriptedAgent(attack_rate=1.0),
    )
    bundle = build_bundle(
        bundle_id="b_cp", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials,
        aggregates=[], traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    pins = []
    if with_pins:
        from lab_runner.verdicts import contained

        denied = next(
            t for t in traces.values()
            if any(e.get("type") == "gate_decision" and contained(e["decision"])
                   for e in t["events"])
        )
        pins = [{"trace_id": str(denied["trace_id"]), "trace_ref": content_hash(denied),
                 "expected_verdict": "DENY"}]
    return export_cp(bundle, regressions=pins, traces=traces), traces, pins


class TestThePackageCarriesItsEvidence(unittest.TestCase):
    def setUp(self) -> None:
        self.export, self.traces, self.pins = _run_and_export()
        self.config = self.export.config

    def test_a_carried_pin_has_its_trace_body_embedded(self) -> None:
        bodies = self.config["regression_traces"]
        self.assertEqual(
            set(bodies), {str(p["trace_id"]) for p in self.config["regressions"]},
        )

    def test_the_body_names_its_own_trace_id(self) -> None:
        """What the receiving side validates: a body whose `trace_id` disagrees
        with its key is refused."""
        for trace_id, body in self.config["regression_traces"].items():
            with self.subTest(trace_id=trace_id):
                self.assertEqual(str(body["trace_id"]), str(trace_id))

    def test_the_body_still_hashes_to_the_pin_it_backs(self) -> None:
        """The CP re-checks the body against the pin's `trace_ref` before
        replaying it. Embedding anything but the frozen bytes would make every
        pin unreplayable in a way that looks like tampering."""
        by_id = {str(p["trace_id"]): p for p in self.config["regressions"]}
        for trace_id, body in self.config["regression_traces"].items():
            with self.subTest(trace_id=trace_id):
                self.assertEqual(content_hash(body), str(by_id[trace_id]["trace_ref"]))

    def test_it_carries_only_the_pinned_traces(self) -> None:
        """A package ships the evidence its pins need, not the whole bundle."""
        self.assertLess(len(self.config["regression_traces"]), len(self.traces))

    def test_the_body_is_serializable_as_part_of_the_package(self) -> None:
        """It travels inside `cp-deploy.json`; the sibling directory is not a
        substitute, because `POST /v1/lab/deploy` takes the document alone."""
        json.loads(json.dumps(self.config))


class TestAPackageWithNoPins(unittest.TestCase):
    def test_it_carries_an_empty_map_not_a_missing_key(self) -> None:
        export, _, _ = _run_and_export(with_pins=False)
        self.assertEqual(export.config["regression_traces"], {})

    def test_an_export_without_the_traces_is_refused_outright(self) -> None:
        """Unchanged, and the reason the bodies are always available above: an
        export is an evidence-backed handoff, so it REQUIRES the traces. Nothing
        can be carried — pin or body — that was never validated against them."""
        scenario = support.banking_scenario()
        conditions = support.conditions()
        result = run_experiment_suite(
            [scenario], support.manifests(), conditions, support.kernel_registry(),
            repeats=2, run_id="r", agent=ScriptedAgent(attack_rate=1.0),
        )
        bundle = build_bundle(
            bundle_id="b", created=CREATED, scenarios=[scenario], conditions=conditions,
            tool_manifests=list(support.manifests().values()),
            environment=support.environment(), trials=result.trials,
            aggregates=[], traces=result.traces,
        )
        with self.assertRaises(CPExportError):
            export_cp(bundle, regressions=[{"trace_id": "t_x", "expected_verdict": "DENY"}],
                      traces={})


class TestTheReceivingSideCanUseIt(unittest.TestCase):
    """The shape the Control Plane's `deploy_plans` reads, asserted here because
    axor-lab is the producer and cannot import the consumer.

    Verified against the real backend outside the suite: a package exported this
    way yields `replayable=True` with converted kernel events, while one whose
    trace was recorded under the reference kernel is honestly refused with
    "CP replays the real axor-core".
    """

    def test_regression_traces_is_a_map_of_trace_id_to_object(self) -> None:
        export, _, _ = _run_and_export()
        bodies = export.config["regression_traces"]
        self.assertIsInstance(bodies, dict)
        for trace_id, body in bodies.items():
            with self.subTest(trace_id=trace_id):
                self.assertIsInstance(trace_id, str)
                self.assertIsInstance(body, dict)
                self.assertEqual(body["schema_version"], "trace/v1")

    def test_the_carried_trace_names_the_real_kernel(self) -> None:
        """A trace recorded under the in-process reference kernel converts, but
        the CP refuses to REPLAY it — it runs the real axor-core. The default is
        the installed kernel, so an exported pin is replayable by default."""
        export, _, _ = _run_and_export()
        for body in export.config["regression_traces"].values():
            with self.subTest(trace=body["trace_id"]):
                self.assertTrue(
                    str(body["producer"]["kernel_version"]).startswith("axor-core@"),
                )


if __name__ == "__main__":
    unittest.main()
