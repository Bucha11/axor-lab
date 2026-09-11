"""The CLI and the hosted face answer the same question the same way.

The CLI used to be a second IMPLEMENTATION, not a second face: `verify`,
`export-cp`, `verify-cp-export` and `import-incident` existed only in
`lab_runner/cli.py`, so having a shell was a precondition for using them, and
`grep export_cp lab_server/` came back empty.

These tests pin the property that makes them faces: one verb, two renderings.
A drift between them is now a test failure rather than a thing a user discovers
by finding the web app cannot do what the docs describe.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from tests import support
from tests.test_runtime_http_integration import CONTROL, RuntimeHttpTestCase

from lab_runner import cli
from lab_service import Outcome, verify_package, verify_package_document


def _bundle_dir(root: Path) -> Path:
    """A real run, written out as a bundle directory."""
    from lab_capabilities.governance import run_experiment_suite
    from lab_contracts import build_bundle
    from lab_runner.bundle_io import PACKAGING, write_bundle_dir

    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=2, run_id="r_parity",
    )
    bundle = build_bundle(
        bundle_id="b_parity", created="2026-01-01T00:00:00Z", scenarios=[scenario],
        conditions=conditions, tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials, aggregates=[],
        traces=result.traces, packaging=dict(PACKAGING),
    )
    out = root / "bundle"
    write_bundle_dir(out, bundle, result.traces)
    return out


class TestVerifyParity(unittest.TestCase):
    def test_the_cli_prints_exactly_the_checks_the_service_ran(self) -> None:
        """The CLI adds rendering, never findings. Every line it prints is a check
        the service returned, so a face cannot quietly assert something the verb
        did not conclude."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _bundle_dir(Path(tmp))
            result = verify_package(path)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["verify", str(path)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIs(result.outcome, Outcome.OK)
        printed = [ln for ln in (out.getvalue() + err.getvalue()).splitlines() if ln.strip()]
        self.assertEqual(printed, [f"{c.name}: {c.message}" for c in result.checks])

    def test_an_ok_check_goes_to_stdout_and_a_finding_to_stderr(self) -> None:
        """Piping `verify` must not swallow the reason it failed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _bundle_dir(root)
            bare = root / "bare.json"
            from lab_runner.bundle_io import read_bundle_dir

            bundle, traces = read_bundle_dir(path)
            bare.write_text(json.dumps({"bundle": bundle, "traces": list(traces.values())}))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["verify", str(bare)])
        # a bare file without --allow-bare is refused, not silently passed
        self.assertEqual(code, cli.EXIT_VALIDATION)
        self.assertIn("NOT a versioned reproduction envelope", err.getvalue())
        self.assertNotIn("NOT a versioned", out.getvalue())


class TestVerifyOverHttp(RuntimeHttpTestCase):
    def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {CONTROL}"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def test_the_hosted_face_verifies_a_package_the_cli_could_only_verify_locally(self) -> None:
        """The verb reaches the web. Before the service layer this endpoint could
        not exist: the checks lived in an argparse handler."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _bundle_dir(Path(tmp))
            from lab_runner.bundle_io import read_bundle_dir

            bundle, traces = read_bundle_dir(path)
            # the SAME inputs on both sides: a bare payload carrying no server
            # proofs, accepted explicitly. Comparing a directory against an upload
            # would compare two different modes and prove nothing.
            local = verify_package_document(bundle, traces, {}, allow_bare=True)
            served = self._post("/verify/package", {
                "package": {"bundle": bundle, "traces": traces},
                "allow_bare": True,
            })
        self.assertEqual(served["outcome"], local.outcome.value)
        self.assertEqual(
            [c["message"] for c in served["checks"]],  # type: ignore[index,union-attr]
            [c.message for c in local.checks],
        )

    def test_a_package_without_a_bundle_is_a_clean_400(self) -> None:
        """A malformed request is the caller's to fix — never a 500."""
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post("/verify/package", {"package": {"traces": {}}})
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
