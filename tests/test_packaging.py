"""Packaging + schema-source-of-truth (review §12, §3.1 CI hygiene).

The installed-wheel schema loader must find the schemas as package data (no
AXOR_LAB_CONTRACTS needed); and the package-data copy must stay byte-identical
to the source-of-truth contracts/schemas/ so the two never drift.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from lab_contracts.schemas import SCHEMA_NAMES

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


if __name__ == "__main__":
    unittest.main()
