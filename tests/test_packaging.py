"""Packaging + schema-source-of-truth (review §12, §3.1 CI hygiene).

The installed-wheel schema loader must find the schemas as package data (no
AXOR_LAB_CONTRACTS needed); and the package-data copy must stay byte-identical
to the source-of-truth contracts/schemas/ so the two never drift.

The built web UI follows the same pattern and for the same reason: an installed
user has no `frontend/` directory, so without a copy inside the package
`pip install axor-lab` could never serve the UI.
"""

from __future__ import annotations

import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path

from lab_contracts.schemas import SCHEMA_NAMES
from lab_server import local_run, spa

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "contracts" / "schemas"
PACKAGED = REPO_ROOT / "lab_contracts" / "schemas"


class TestSchemaPackageData(unittest.TestCase):
    def test_package_data_copy_matches_source_of_truth(self) -> None:
        for name in SCHEMA_NAMES:
            src = (SOURCE / f"{name}.schema.json").read_bytes()
            pkg = (PACKAGED / f"{name}.schema.json").read_bytes()
            self.assertEqual(src, pkg, f"{name}: package-data schema drifted from contracts/")

    def test_loader_finds_schemas_without_env_or_checkout_relative_path(self) -> None:
        # simulate "outside a checkout": no AXOR_LAB_CONTRACTS, and clear the
        # cache so the package-data path is exercised
        import os

        from lab_contracts import schemas as schemas_mod

        schemas_mod.load_schemas.cache_clear()
        old = os.environ.pop(schemas_mod.ENV_CONTRACTS_DIR, None)
        try:
            loaded = schemas_mod.load_schemas()
            self.assertEqual(set(loaded), set(SCHEMA_NAMES))
            self.assertEqual(loaded["trace"]["title"], "Axor Lab Trace")
        finally:
            if old is not None:
                os.environ[schemas_mod.ENV_CONTRACTS_DIR] = old
            schemas_mod.load_schemas.cache_clear()


class TestBundledExamplePackageData(unittest.TestCase):
    """The landing page's one-click first result must survive `pip install`.

    It did not: `POST /runs/local {}` resolved the example against the
    repository root, so every installed user got a 500 from the single
    capability the product leads with.
    """

    def test_package_data_copy_matches_source_of_truth(self) -> None:
        source = (REPO_ROOT / "examples" / local_run.EXAMPLE_NAME).read_bytes()
        packaged = (REPO_ROOT / "lab_server" / "examples" / local_run.EXAMPLE_NAME).read_bytes()
        self.assertEqual(source, packaged, "packaged example drifted from examples/")

    def test_the_wheel_declares_the_example_as_package_data(self) -> None:
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        self.assertIn("examples/*.axl", config["tool"]["setuptools"]["package-data"]["lab_server"])

    def test_the_example_resolves_without_a_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packaged = Path(tmp) / "examples"
            packaged.mkdir()
            (packaged / local_run.EXAMPLE_NAME).write_text("{}")
            with unittest.mock.patch.object(
                local_run, "_CHECKOUT_EXAMPLES", Path(tmp) / "absent",
            ), unittest.mock.patch.object(local_run, "_PACKAGED_EXAMPLES", packaged):
                self.assertEqual(local_run.example_path(), packaged / local_run.EXAMPLE_NAME)

    def test_the_bundled_example_actually_loads(self) -> None:
        # the copy is only worth shipping if it parses as an experiment
        self.assertEqual(local_run.load_example()["scenarios"][0]["name"],
                         "banking-exfil-01")


class TestWebUIPackageData(unittest.TestCase):
    """`pip install axor-lab` must be able to serve the UI, with no node."""

    def test_the_wheel_declares_the_web_build_as_package_data(self) -> None:
        # without this declaration the staged build is silently dropped from the
        # wheel and the install serves a UI-less server that looks like a product
        # with nothing in it
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        package_data = config["tool"]["setuptools"]["package-data"]
        self.assertIn("web/**/*", package_data["lab_server"])
        self.assertIn("lab_server", config["tool"]["setuptools"]["packages"])

    def test_find_dist_falls_back_to_the_packaged_build(self) -> None:
        """No checkout, no `frontend/` — the copy inside the package is the UI."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_pkg = Path(tmp) / "lab_server"
            (fake_pkg / "web" / "assets").mkdir(parents=True)
            (fake_pkg / "web" / "index.html").write_text("<!doctype html>")
            with unittest.mock.patch.object(spa, "PACKAGED_WEB", fake_pkg / "web"), \
                    unittest.mock.patch.object(spa, "CHECKOUT_DIST", Path(tmp) / "absent"):
                self.assertEqual(spa.find_dist(), fake_pkg / "web")

    def test_a_checkout_build_wins_over_the_packaged_one(self) -> None:
        """Someone who just ran the build means the thing they built."""
        with tempfile.TemporaryDirectory() as tmp:
            packaged, checkout = Path(tmp) / "web", Path(tmp) / "dist"
            for path in (packaged, checkout):
                path.mkdir()
                (path / "index.html").write_text("<!doctype html>")
            with unittest.mock.patch.object(spa, "PACKAGED_WEB", packaged), \
                    unittest.mock.patch.object(spa, "CHECKOUT_DIST", checkout):
                self.assertEqual(spa.find_dist(), checkout)

    def test_the_staging_script_reproduces_the_build_directory(self) -> None:
        """A partial copy serves an index that references assets that are absent."""
        if not (REPO_ROOT / "frontend" / "dist" / "index.html").is_file():
            self.skipTest("no frontend build in this checkout")
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_web", REPO_ROOT / "scripts" / "build_web.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "web"
            # a stale asset must not survive the sync: content-hashed filenames
            # mean nothing ever overwrites it, so it would be served forever
            staged.mkdir()
            (staged / "stale.js").write_text("gone")
            with unittest.mock.patch.object(module, "PACKAGED", staged):
                module.sync()
            source = REPO_ROOT / "frontend" / "dist"
            self.assertEqual(
                sorted(p.relative_to(staged) for p in staged.rglob("*")),
                sorted(p.relative_to(source) for p in source.rglob("*")),
            )
            self.assertFalse((staged / "stale.js").exists())


if __name__ == "__main__":
    unittest.main()
