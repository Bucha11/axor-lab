"""The release gate must catch what PyPI would reject, before the tag is spent.

`tools/release_preflight.py` exists because two publish failures are invisible
until the upload step, when the tag is already pushed: a PEP 508 direct-URL
dependency (PyPI answers 400) and a dependency floor no published version
satisfies (the upload succeeds and every install afterwards fails). Both are
true of this package right now — `pyproject.toml` pins axor-wrap and axor-eval
to git branches — so a release workflow without this gate would be a red run
and a burned version number.

These tests drive the checks over synthesized wheels rather than the repo's own
build, so they stay meaningful once the git pins are finally replaced by
version ranges: the gate must still refuse a direct reference then.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tools import release_preflight as rp  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

GIT_PIN = "axor-wrap @ git+https://github.com/Bucha11/axor-wrap@some-branch"
RANGE = "axor-core<0.12,>=0.11"
EXTRA = 'pyyaml>=6.0; extra == "yaml"'
MARKED = 'tomli>=2.0; python_version < "3.11"'


def _wheel(tmp: pathlib.Path, specs: list[str], *, name: str = "axor_lab-0.1.0") -> pathlib.Path:
    """A minimal wheel carrying exactly these Requires-Dist lines."""
    path = tmp / f"{name}-py3-none-any.whl"
    lines = ["Metadata-Version: 2.4", "Name: axor-lab", "Version: 0.1.0"]
    lines += [f"Requires-Dist: {spec}" for spec in specs]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{name}.dist-info/METADATA", "\n".join(lines) + "\n")
    (tmp / f"{name}.tar.gz").write_bytes(b"")
    return path


class TestDirectReferences(unittest.TestCase):
    def test_a_git_pin_is_a_direct_reference(self) -> None:
        self.assertEqual(rp.direct_references([GIT_PIN, RANGE, EXTRA]), [GIT_PIN])

    def test_a_version_range_is_not(self) -> None:
        self.assertEqual(rp.direct_references([RANGE]), [])

    def test_an_environment_marker_is_not_mistaken_for_one(self) -> None:
        # the marker half is dropped before the `@` search, so a requirement
        # with a marker and no URL must stay clean
        self.assertEqual(rp.direct_references([MARKED, EXTRA]), [])

    def test_the_message_names_the_package_and_the_remedy(self) -> None:
        (problem,) = rp.check_direct_references([GIT_PIN])
        self.assertIn("axor-wrap", problem)
        self.assertIn("direct dependency", problem)
        self.assertIn("version range", problem)


class TestRuntimeRequirements(unittest.TestCase):
    def test_extras_are_not_required_of_a_plain_install(self) -> None:
        self.assertNotIn("pyyaml>=6.0", rp.runtime_requirements([EXTRA]))

    def test_a_plain_dependency_is(self) -> None:
        self.assertIn(RANGE, rp.runtime_requirements([RANGE, EXTRA]))

    def test_a_marked_dependency_keeps_its_requirement_half_only(self) -> None:
        self.assertEqual(rp.runtime_requirements([MARKED]), ["tomli>=2.0"])

    def test_a_direct_reference_is_not_probed_against_pypi(self) -> None:
        # it is already a blocker; probing `name @ url` would clone the repo
        self.assertEqual(rp.runtime_requirements([GIT_PIN]), [])


class TestTagAgreement(unittest.TestCase):
    def test_the_repo_version_matches_its_own_tag(self) -> None:
        version = rp.version_of(ROOT / "pyproject.toml")
        self.assertEqual(rp.check_tag(f"v{version}", ROOT / "pyproject.toml"), [])

    def test_a_mismatched_tag_is_refused(self) -> None:
        (problem,) = rp.check_tag("v9.9.9", ROOT / "pyproject.toml")
        self.assertIn("version mismatch", problem)

    def test_a_malformed_tag_is_refused(self) -> None:
        self.assertTrue(rp.check_tag("v1.2", ROOT / "pyproject.toml"))
        self.assertTrue(rp.check_tag("release-1.2.3", ROOT / "pyproject.toml"))


class TestTheGateEndToEnd(unittest.TestCase):
    """Through `main()`, the way the workflow calls it."""

    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_git_pin_fails_the_release(self) -> None:
        _wheel(self.tmp, [GIT_PIN, RANGE])
        self.assertEqual(rp.main(["--dist", str(self.tmp), "--offline"]), 1)

    def test_clean_metadata_passes(self) -> None:
        _wheel(self.tmp, [RANGE, EXTRA])
        self.assertEqual(rp.main(["--dist", str(self.tmp), "--offline"]), 0)

    def test_a_missing_sdist_fails(self) -> None:
        _wheel(self.tmp, [RANGE])
        (self.tmp / "axor_lab-0.1.0.tar.gz").unlink()
        self.assertEqual(rp.main(["--dist", str(self.tmp), "--offline"]), 1)

    def test_two_wheels_fail_rather_than_publishing_the_wrong_one(self) -> None:
        _wheel(self.tmp, [RANGE])
        _wheel(self.tmp, [RANGE], name="axor_lab-0.2.0")
        self.assertEqual(rp.main(["--dist", str(self.tmp), "--offline"]), 1)

    def test_the_script_runs_as_the_workflow_invokes_it(self) -> None:
        _wheel(self.tmp, [GIT_PIN])
        proc = subprocess.run(
            [sys.executable, "tools/release_preflight.py", "--dist", str(self.tmp), "--offline"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error::", proc.stderr)


class TestTheWorkflowWiring(unittest.TestCase):
    """A gate that the publish leg can bypass is not a gate."""

    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_release_workflow_exists_and_is_tag_driven(self) -> None:
        self.assertIn('tags: ["v*"]', self.text)

    def test_the_publish_leg_needs_the_preflight(self) -> None:
        publish = self.text.split("  pypi:", 1)[1]
        self.assertIn("needs: preflight", publish)

    def test_the_publish_leg_only_runs_on_a_tag(self) -> None:
        publish = self.text.split("  pypi:", 1)[1]
        self.assertIn("if: github.ref_type == 'tag'", publish)

    def test_publishing_is_credential_free(self) -> None:
        # trusted publishing (OIDC), the Control Plane's arrangement: an API
        # token in the repo is the thing this must never regress into
        self.assertIn("id-token: write", self.text)
        self.assertIn("pypa/gh-action-pypi-publish", self.text)
        self.assertNotIn("PYPI_API_TOKEN", self.text)
        self.assertNotIn("password:", self.text)

    def test_the_preflight_script_is_actually_invoked(self) -> None:
        self.assertIn("tools/release_preflight.py", self.text)

    def test_the_published_files_are_the_ones_that_were_checked(self) -> None:
        # a rebuild in the publish leg would upload artefacts nothing verified
        publish = self.text.split("  pypi:", 1)[1]
        self.assertIn("download-artifact", publish)
        self.assertNotIn("python -m build", publish)


if __name__ == "__main__":
    unittest.main()
