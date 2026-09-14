"""Packaging + schema-source-of-truth (review §12, §3.1 CI hygiene).

The installed-wheel schema loader must find the Lab's own schemas as package
data (no AXOR_LAB_CONTRACTS needed), and that package-data copy must stay
byte-identical to the source of truth in contracts/schemas/ so the two never
drift.

Only the Lab's ten are in either place. `trace`, `tool-manifest` and
`predicate` are axor-core's and come from the installed package, so there is
nothing here to keep in sync and — more to the point — nothing to drift: a copy
on this repo's disk under one of those names would be shadowed by the owner's,
which is what the loader's merge order is for.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from lab_contracts.schemas import KERNEL_SCHEMA_NAMES, LAB_SCHEMA_NAMES, SCHEMA_NAMES

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "contracts" / "schemas"
PACKAGED = REPO_ROOT / "lab_contracts" / "schemas"


class TestSchemaPackageData(unittest.TestCase):
    def test_package_data_copy_matches_source_of_truth(self) -> None:
        for name in LAB_SCHEMA_NAMES:
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
            self.assertEqual(loaded["bundle"]["title"], "Axor Lab Reproducible Bundle")
            # from axor-core, not off this disk
            self.assertEqual(loaded["trace"]["title"], "Axor Trace")
        finally:
            if old is not None:
                os.environ[schemas_mod.ENV_CONTRACTS_DIR] = old
            schemas_mod.load_schemas.cache_clear()


if __name__ == "__main__":
    unittest.main()


class TestTheKernelSchemasAreNotKeptHere(unittest.TestCase):
    """`trace`, `tool-manifest` and `predicate` have one owner.

    This repo held its own copies, documented as "the de-facto axor-core
    baseline" because the kernel did not ship them — plus a third set of
    narrower stubs under `contracts/schemas/_shared_from_axor_core/`. Three
    statements of a format that crosses a product boundary, in one repository.
    A copy reappearing is the whole failure mode, so it is a test.
    """

    def test_no_kernel_schema_sits_on_this_repos_disk(self) -> None:
        for name in KERNEL_SCHEMA_NAMES:
            for where in (SOURCE, PACKAGED):
                path = where / f"{name}.schema.json"
                self.assertFalse(
                    path.exists(),
                    f"{path} is axor-core's; import it, do not copy it",
                )

    def test_the_loader_serves_them_from_axor_core(self) -> None:
        from axor_core.contracts.schemas import load

        from lab_contracts import load_schemas

        loaded = load_schemas()
        for name in KERNEL_SCHEMA_NAMES:
            self.assertEqual(loaded[name], load(name), name)


class TestEveryPackageShips(unittest.TestCase):
    """A package missing from `[tool.setuptools] packages` is invisible until
    someone installs the wheel.

    `lab_service` was absent from the list for as long as the layer had
    existed, so an installed `axor-lab` raised ModuleNotFoundError on every verb
    that reaches it — publish, verify, export-cp, report — and, once the CLI
    imported it at module level, on `--help`. The source tree works either way,
    which is exactly why nobody noticed: the only place this shows is a wheel
    installed outside the checkout, and that is one CI job at the end of the
    list.
    """

    def _declared(self) -> set[str]:
        import re

        text = (REPO_ROOT / "pyproject.toml").read_text()
        # the first "]" after the table header closes the HEADER, not the list
        start = text.index("packages = [", text.index("[tool.setuptools]"))
        block = text[start:text.index("]", start)]
        return set(re.findall(r'"([A-Za-z0-9_.]+)"', block))

    def test_every_lab_package_in_the_tree_is_declared(self) -> None:
        on_disk = {
            path.name for path in REPO_ROOT.iterdir()
            if path.is_dir() and path.name.startswith("lab_")
            and (path / "__init__.py").exists()
        }
        declared = self._declared()
        self.assertTrue(on_disk, "found no lab_* packages — the scan broke")
        self.assertEqual(
            sorted(on_disk - declared), [],
            "these packages exist and would NOT ship in the wheel",
        )

    def test_every_declared_package_exists(self) -> None:
        """The other direction: a stale entry makes the build fail outright."""
        for name in sorted(self._declared()):
            with self.subTest(package=name):
                self.assertTrue(
                    (REPO_ROOT / name.replace(".", "/") / "__init__.py").exists(),
                    f"{name} is declared but not in the tree",
                )

    def test_a_module_level_import_of_a_shipped_package_is_safe(self) -> None:
        """`lab_runner/cli.py` imports `lab_service` at module level, which is
        only safe because it ships. Pin the pairing so a future top-level import
        of an undeclared package is caught here rather than in the wheel job."""
        import ast

        source = (REPO_ROOT / "lab_runner" / "cli.py").read_text()
        tree = ast.parse(source)
        top_level: set[str] = set()
        for node in tree.body:  # module level only, not the lazy in-function ones
            if isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                top_level.update(alias.name.split(".")[0] for alias in node.names)
        declared = self._declared()
        for name in sorted(n for n in top_level if n.startswith("lab_")):
            with self.subTest(imported=name):
                self.assertIn(name, declared)
