"""The Suite SDK has to be reachable FROM THE PRODUCT.

`lab_suite/` shipped with a registry, three built-in suites, execution, metrics,
aggregation, invariants and artifact assembly — and nothing outside `lab_suite/`
and `tests/` imported it. Not the CLI, not the server. `axor-lab run` still went
through `run_experiment_suite` and `.axl`.

Phase 2's exit criterion ("`axor-lab run` completes a suite with zero conditions
and produces an artifact with metrics") was therefore satisfied only from a test.
A user could not reach the code, which also means a regression in it could not
reach a user — the SDK was an island with a test harness moored to it.

This file pins the entry points that make it not an island: the CLI
(`axor-lab suites`, `run-suite`, `suite-yaml`) and the screen endpoints
(`GET /suites`, `GET /suites/{id}`, `GET /suites/{id}/yaml`,
`POST /suites/validate`, `POST /suites/validate-yaml`). It runs the real
`main()` and the real HTTP server, because an entry point tested by calling the
function underneath it is the same island with a longer bridge.
"""

from __future__ import annotations

import importlib.util
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from lab_contracts import validate_artifact
from lab_runner.cli import main
from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_suite import UNAVAILABLE_SUITES, builtin_registry, suite_catalog

CONTROL = "control-token-for-tests"

# The YAML editing mode is the `yaml` extra (PyYAML). Its CLI/server entry points
# 501/exit-1 with a "pip install axor-lab[yaml]" instruction when it is absent, so
# every test that drives a REAL YAML round trip is skipUnless-gated — it RUNS in
# the dedicated `yaml` CI job and SKIPS in the base acceptance job, rather than
# hard-failing there. JSON validate/catalog/token tests need nothing extra.
_HAS_YAML = importlib.util.find_spec("yaml") is not None
_YAML_REASON = "requires the axor-lab[yaml] extra (PyYAML)"


def _run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestTheCatalogIsOneList(unittest.TestCase):
    """The CLI and the screen endpoint must not keep separate ideas of which
    suites exist — that is how a catalog offers a suite the runner cannot
    resolve."""

    def test_every_registered_suite_is_available(self) -> None:
        registered = set(builtin_registry().ids())
        available = {str(c["id"]) for c in suite_catalog() if c["available"]}
        self.assertEqual(registered, available)

    def test_the_announced_suites_are_listed_and_marked_unavailable(self) -> None:
        """Dropping them would read as "the feature was cut" to anyone holding
        the design board; rendering them as ordinary cards would offer three
        suites that run nothing."""
        cards = {str(c["id"]): c for c in suite_catalog()}
        for announced in UNAVAILABLE_SUITES:
            card = cards[str(announced["id"])]
            self.assertFalse(card["available"])
            self.assertTrue(card["reason"])

    def test_a_registered_suite_wins_over_its_announced_placeholder(self) -> None:
        """When one of the three is actually implemented, the catalog must show
        the real thing rather than two cards for one id."""
        from lab_suite.sdk import BaseSuite, SuiteRegistry

        claimed = str(UNAVAILABLE_SUITES[0]["id"])

        class _Real(BaseSuite):
            id = claimed

            def manifest(self) -> dict[str, object]:
                return {"id": claimed, "name": "Real"}

        registry = SuiteRegistry()
        registry.register(_Real())
        cards = [c for c in suite_catalog(registry) if str(c["id"]) == claimed]
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0]["available"])


class TestTheCliListsSuites(unittest.TestCase):
    def test_suites_names_every_built_in(self) -> None:
        code, out, _ = _run_cli("suites")
        self.assertEqual(code, 0)
        for suite_id in builtin_registry().ids():
            self.assertIn(suite_id, out)

    def test_it_says_out_loud_which_ones_do_not_exist(self) -> None:
        _, out, _ = _run_cli("suites")
        self.assertIn("UNAVAILABLE", out)


class TestTheCliRunsASuite(unittest.TestCase):
    def test_a_zero_condition_suite_runs_and_writes_an_artifact(self) -> None:
        """Phase 2's exit criterion, through the product this time. `budget`
        declares no conditions and no governance capability: no kernel arm is
        configured, and the run still produces a conformant artifact."""
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "artifact"
            code, out, err = _run_cli("run-suite", "budget", "--out", str(out_dir), "--yes")
            self.assertEqual(code, 0, err)
            self.assertIn("single-arm: no governance", out)
            artifact = json.loads((out_dir / "artifact.json").read_text())
            self.assertEqual(validate_artifact(artifact, "artifact"), [])

    def test_every_trial_in_that_artifact_carries_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "artifact"
            _run_cli("run-suite", "budget", "--out", str(out_dir), "--yes")
            artifact = json.loads((out_dir / "artifact.json").read_text())
            trials = artifact["bundle"]["trials"]
            self.assertTrue(trials)
            for trial in trials:
                self.assertIn("duration_ms", trial["metrics"])

    def test_an_unmeasured_metric_is_absent_rather_than_zero(self) -> None:
        """A scripted agent calls no provider, so there is no spend. Recording
        `cost_usd: 0` would make a run that priced nothing look free."""
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "artifact"
            _run_cli("run-suite", "budget", "--out", str(out_dir), "--yes")
            artifact = json.loads((out_dir / "artifact.json").read_text())
            for trial in artifact["bundle"]["trials"]:
                self.assertNotIn("cost_usd", trial["metrics"])

    def test_the_governed_suite_runs_through_the_same_command(self) -> None:
        """AgentDojo declares the governance capability and two arms. One
        command runs both shapes, or the capability is a second product."""
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "artifact"
            code, out, err = _run_cli(
                "run-suite", "agentdojo", "--out", str(out_dir), "--yes",
            )
            self.assertEqual(code, 0, err)
            artifact = json.loads((out_dir / "artifact.json").read_text())
            arms = {str(c["label"]) for c in artifact["bundle"]["conditions"]}
            self.assertEqual(arms, {"ungoverned", "governed"})

    def test_a_manifest_path_runs_too(self) -> None:
        """The Builder's loop is edit-a-manifest-then-run; a suite id is only
        the shortcut for the manifest a built-in already carries."""
        with TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "suite.json"
            manifest_path.write_text(
                json.dumps(builtin_registry().get("budget").manifest())
            )
            out_dir = Path(tmp) / "artifact"
            code, _, err = _run_cli(
                "run-suite", str(manifest_path), "--out", str(out_dir), "--yes",
            )
            self.assertEqual(code, 0, err)

    def test_an_invalid_manifest_is_refused_before_anything_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "suite.json"
            manifest_path.write_text(json.dumps({"schema_version": "suite/v1", "id": "x"}))
            out_dir = Path(tmp) / "artifact"
            code, _, err = _run_cli(
                "run-suite", str(manifest_path), "--out", str(out_dir), "--yes",
            )
            self.assertEqual(code, 2, err)
            self.assertFalse(out_dir.exists(), "a refused suite wrote output anyway")

    # -- scenario_refs, locally ------------------------------------------
    #
    # The hosted server resolves `scenario_refs` from the workspace store. A
    # manifest run from a FILE has no workspace, and nothing passed a registry
    # at all — so a ref died on "resolves to nothing" on the one path where a
    # user is most likely to be assembling suites out of shared scenarios.
    # The filesystem beside the manifest is the registry there.

    def _ref_suite(self, root: Path, scenarios_dir: str = "scenarios") -> Path:
        import copy

        blank = copy.deepcopy(builtin_registry().get("blank").manifest())
        shared = copy.deepcopy(blank["scenarios"][0])
        shared["name"] = "shared-note"
        (root / scenarios_dir).mkdir(parents=True, exist_ok=True)
        (root / scenarios_dir / "shared-note.json").write_text(json.dumps(shared))

        manifest = {**blank, "id": "local-refs", "name": "Local refs",
                    "origin": "workspace", "scenario_refs": ["shared-note"]}
        path = root / "suite.json"
        path.write_text(json.dumps(manifest))
        return path

    def test_a_ref_resolves_from_scenarios_beside_the_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._ref_suite(Path(tmp))
            out_dir = Path(tmp) / "artifact"
            code, out, err = _run_cli("run-suite", str(path), "--out", str(out_dir), "--yes")
            self.assertEqual(code, 0, err)
            # the inline scenario AND the ref, both planned and both run
            self.assertIn("scenarios=2", out)
            artifact = json.loads((out_dir / "artifact.json").read_text())
            self.assertEqual(validate_artifact(artifact, "artifact"), [])

    def test_scenarios_dir_points_the_registry_elsewhere(self) -> None:
        """A library shared across suites in other directories."""
        with TemporaryDirectory() as tmp:
            path = self._ref_suite(Path(tmp), scenarios_dir="lib")
            out_dir = Path(tmp) / "artifact"
            code, out, err = _run_cli(
                "run-suite", str(path), "--out", str(out_dir), "--yes",
                "--scenarios", str(Path(tmp) / "lib"),
            )
            self.assertEqual(code, 0, err)
            self.assertIn("scenarios=2", out)

    def test_an_unresolved_ref_is_refused_before_anything_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._ref_suite(Path(tmp), scenarios_dir="lib")  # not the default dir
            out_dir = Path(tmp) / "artifact"
            code, _, err = _run_cli("run-suite", str(path), "--out", str(out_dir), "--yes")
            self.assertEqual(code, 2, err)
            self.assertIn("scenario_ref 'shared-note' resolves to nothing", err)
            self.assertFalse(out_dir.exists())

    def test_an_unreadable_scenario_file_names_itself(self) -> None:
        """Skipping it would report "resolves to nothing" against a suite that
        names the ref correctly — the error would point at the innocent
        document."""
        with TemporaryDirectory() as tmp:
            path = self._ref_suite(Path(tmp))
            (Path(tmp) / "scenarios" / "broken.json").write_text("{not json")
            code, _, err = _run_cli(
                "run-suite", str(path), "--out", str(Path(tmp) / "artifact"), "--yes")
            self.assertEqual(code, 2)
            self.assertIn("[scenarios] broken.json", err)

    def test_a_json_file_that_is_not_a_scenario_says_so(self) -> None:
        with TemporaryDirectory() as tmp:
            path = self._ref_suite(Path(tmp))
            (Path(tmp) / "scenarios" / "notes.json").write_text('{"hello": 1}')
            code, _, err = _run_cli(
                "run-suite", str(path), "--out", str(Path(tmp) / "artifact"), "--yes")
            self.assertEqual(code, 2)
            self.assertIn("not a scenario document", err)

    def test_a_builtin_id_has_no_registry_and_needs_none(self) -> None:
        """Nothing shipped names a ref, and a suite id has no directory to look
        beside — an absent registry is correct there, not a gap."""
        from lab_service import default_scenario_dir

        self.assertIsNone(default_scenario_dir("blank"))
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "artifact"
            self.assertEqual(
                _run_cli("run-suite", "blank", "--out", str(out_dir), "--yes")[0], 0)

    def test_it_does_not_run_without_confirmation(self) -> None:
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "artifact"
            code, _, _ = _run_cli("run-suite", "blank", "--out", str(out_dir))
            self.assertEqual(code, 3)
            self.assertFalse(out_dir.exists())


@unittest.skipUnless(_HAS_YAML, _YAML_REASON)
class TestTheYamlModeIsReachableToo(unittest.TestCase):
    """A third editing mode nobody can open is the same island the SDK was."""

    def test_the_cli_prints_a_manifest_as_yaml(self) -> None:
        code, out, err = _run_cli("suite-yaml", "budget")
        self.assertEqual(code, 0, err)
        self.assertIn("schema_version: suite/v1", out)

    def test_what_it_prints_parses_back_to_the_same_manifest(self) -> None:
        from lab_contracts import content_hash
        from lab_suite import from_yaml

        _, out, _ = _run_cli("suite-yaml", "budget")
        self.assertEqual(
            content_hash(from_yaml(out)),
            content_hash(builtin_registry().get("budget").manifest()),
        )


class TestInvariantOutcomesGetDistinctExitCodes(unittest.TestCase):
    """`failed` and `error` are not the same answer and must not share an exit
    code: one says a change regressed, the other says the invariant could not be
    evaluated at all. Collapsing them sends a CI chasing a regression that never
    happened — or, worse in the other direction, reports success for a check
    that never ran."""

    def _suite_with(self, rule: dict[str, object]):
        from lab_suite.sdk import BaseSuite

        manifest = json.loads(json.dumps(builtin_registry().get("budget").manifest()))
        manifest["regressions"] = [{
            "schema_version": "regression/v1", "id": "RG-probe",
            "name": "probe", "rule": rule, "expectation": "probe",
        }]

        class _Probe(BaseSuite):
            id = "budget"

            def manifest(self) -> dict[str, object]:
                return manifest

        return manifest, _Probe()

    def _run(self, rule: dict[str, object]) -> tuple[int, str]:
        manifest, _ = self._suite_with(rule)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            path.write_text(json.dumps(manifest))
            out_dir = Path(tmp) / "artifact"
            code, _, err = _run_cli("run-suite", str(path), "--out", str(out_dir), "--yes")
            return code, err

    def test_a_violated_invariant_exits_regression_differs(self) -> None:
        code, err = self._run(
            {"kind": "metric_threshold", "metric": "duration_ms", "op": "lt", "value": 0.0}
        )
        self.assertEqual(code, 4)
        self.assertIn("VIOLATED", err)

    def test_an_unevaluable_invariant_does_not_exit_regression_differs(self) -> None:
        code, err = self._run(
            {"kind": "metric_threshold", "metric": "cost_usd", "op": "lt", "value": 0.05}
        )
        self.assertNotEqual(code, 0, "an uncheckable invariant reported success")
        self.assertNotEqual(code, 4, "unevaluable was reported as a regression")
        self.assertIn("could not be evaluated", err)


class TestNoBuiltInShipsAnUnevaluableInvariant(unittest.TestCase):
    """The Budget suite used to pin `cost_usd < 0.05`. Nothing in this repo can
    measure cost — the backend that priced a run was deleted — so the launch
    suite errored on every run it ever did. An absent metric erroring is the
    correct rule; a built-in that can never satisfy its own invariant is a
    broken built-in."""

    def test_every_built_in_suite_completes_cleanly(self) -> None:
        for suite_id in builtin_registry().ids():
            with self.subTest(suite=suite_id), TemporaryDirectory() as tmp:
                out_dir = Path(tmp) / "artifact"
                code, _, err = _run_cli(
                    "run-suite", suite_id, "--out", str(out_dir), "--yes",
                )
                self.assertEqual(code, 0, f"{suite_id}: {err}")


class SuiteEndpointTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = make_runtime_server(
            port=0, control_token=CONTROL, store=RuntimeJobStore(),
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _request(self, method: str, path: str, body: object = None,
                 token: str | None = CONTROL) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class TestTheSuiteEndpoints(SuiteEndpointTestCase):
    def test_the_catalog_endpoint_serves_the_same_list_as_the_cli(self) -> None:
        status, payload = self._request("GET", "/suites")
        self.assertEqual(status, 200)
        self.assertEqual(payload["suites"], suite_catalog())

    def test_one_suite_serves_its_manifest(self) -> None:
        status, payload = self._request("GET", "/suites/budget")
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "budget")
        self.assertEqual(validate_artifact(payload, "suite"), [])

    def test_an_announced_but_unimplemented_suite_is_a_404(self) -> None:
        """It has a catalog card and no manifest. A 200 with an empty body would
        open an empty Builder."""
        status, _ = self._request("GET", f"/suites/{UNAVAILABLE_SUITES[0]['id']}")
        self.assertEqual(status, 404)

    def test_validate_accepts_a_posted_manifest(self) -> None:
        manifest = builtin_registry().get("budget").manifest()
        status, payload = self._request("POST", "/suites/validate", {"suite": manifest})
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "errors": []})

    def test_validate_returns_every_error_not_just_the_first(self) -> None:
        """An author fixing a manifest one error per round-trip is a bad
        authoring loop, and the Builder's YAML mode has no other feedback."""
        status, payload = self._request(
            "POST", "/suites/validate", {"suite": {"schema_version": "suite/v1"}},
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertGreater(len(payload["errors"]), 1)

    def test_validate_refuses_a_body_with_no_suite(self) -> None:
        status, _ = self._request("POST", "/suites/validate", {})
        self.assertEqual(status, 400)

    def test_a_get_to_the_validate_path_is_not_read_as_a_suite_id(self) -> None:
        """Without the route guard this answers `no suite 'validate'`, which
        reads like the endpoint does not exist rather than like the method is
        wrong."""
        status, payload = self._request("GET", "/suites/validate")
        self.assertEqual(status, 404)
        self.assertNotIn("no suite", json.dumps(payload))

    @unittest.skipUnless(_HAS_YAML, _YAML_REASON)
    def test_the_yaml_endpoint_serves_the_same_manifest(self) -> None:
        from lab_contracts import content_hash
        from lab_suite import from_yaml

        request = urllib.request.Request(f"{self.base}/suites/budget/yaml")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            self.assertEqual(response.headers["Content-Type"], "application/yaml")
            text = response.read().decode()
        self.assertEqual(
            content_hash(from_yaml(text)),
            content_hash(builtin_registry().get("budget").manifest()),
        )

    @unittest.skipUnless(_HAS_YAML, _YAML_REASON)
    def test_validate_yaml_reports_a_parse_error_as_an_error_not_a_500(self) -> None:
        """A user editing YAML types invalid YAML constantly. That is feedback,
        not a server fault."""
        status, payload = self._request(
            "POST", "/suites/validate-yaml", {"yaml": "id: [unclosed\n"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["errors"])

    @unittest.skipUnless(_HAS_YAML, _YAML_REASON)
    def test_validate_yaml_applies_the_same_rules_as_the_json_validator(self) -> None:
        """Same verdict, same errors. The YAML path additionally returns the
        PARSED manifest, so the Builder can switch modes without becoming a
        second YAML implementation."""
        from lab_suite import to_yaml

        manifest = builtin_registry().get("budget").manifest()
        _, from_json = self._request("POST", "/suites/validate", {"suite": manifest})
        _, from_text = self._request(
            "POST", "/suites/validate-yaml", {"yaml": to_yaml(manifest)},
        )
        self.assertEqual(
            (from_json["ok"], from_json["errors"]),
            (from_text["ok"], from_text["errors"]),
        )
        self.assertEqual(from_text["suite"], manifest)

    @unittest.skipUnless(_HAS_YAML, _YAML_REASON)
    def test_the_serializer_round_trips_an_EDITED_manifest(self) -> None:
        """The Builder's mode switch serializes the document it is HOLDING, not
        the one on disk. Serializing the stored suite would silently discard the
        edits the user just made — the one thing "three modes, one document"
        exists to prevent."""
        from lab_suite import from_yaml

        edited = {**builtin_registry().get("budget").manifest(), "description": "edited"}
        request = urllib.request.Request(
            f"{self.base}/suites/to-yaml",
            data=json.dumps({"suite": edited}).encode(), method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            self.assertEqual(response.headers["Content-Type"], "application/yaml")
            text = response.read().decode()
        self.assertEqual(from_yaml(text), edited)

    @unittest.skipUnless(_HAS_YAML, _YAML_REASON)
    def test_validate_yaml_returns_the_parsed_manifest_so_the_client_need_not_parse(
        self,
    ) -> None:
        from lab_suite import to_yaml

        manifest = builtin_registry().get("budget").manifest()
        _, payload = self._request(
            "POST", "/suites/validate-yaml", {"yaml": to_yaml(manifest)},
        )
        self.assertEqual(payload["suite"], manifest)

    @unittest.skipUnless(_HAS_YAML, _YAML_REASON)
    def test_unparseable_yaml_returns_no_manifest_to_switch_to(self) -> None:
        """No parse, no document — there is nothing for a form to hold."""
        _, payload = self._request(
            "POST", "/suites/validate-yaml", {"yaml": "id: [unclosed\n"},
        )
        self.assertFalse(payload["ok"])
        self.assertNotIn("suite", payload)

    @unittest.skipUnless(_HAS_YAML, _YAML_REASON)
    def test_a_parsed_but_invalid_manifest_comes_back_with_its_errors(self) -> None:
        """A semantically invalid document is still THE document. Withholding
        it trapped the user in YAML mode: introduce one semantic error there
        and every route back to the form that would help fix it was refused."""
        _, payload = self._request(
            "POST", "/suites/validate-yaml", {"yaml": "schema_version: suite/v1\n"},
        )
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["errors"])
        self.assertEqual(payload["suite"], {"schema_version": "suite/v1"})

    def test_the_endpoints_require_the_control_token(self) -> None:
        for method, path, body in (
            ("GET", "/suites", None),
            ("GET", "/suites/budget", None),
            ("POST", "/suites/validate", {"suite": {}}),
            ("POST", "/suites/to-yaml", {"suite": {}}),
        ):
            with self.subTest(path=path):
                status, _ = self._request(method, path, body, token=None)
                self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
