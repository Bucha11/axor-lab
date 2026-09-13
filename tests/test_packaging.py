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
