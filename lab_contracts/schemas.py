"""Loading of the contract JSON Schemas (contracts/schemas/*.schema.json).

The schemas in `contracts/` are the source of truth; this module only loads
them. Resolution order for the schemas directory: an explicit argument, the
AXOR_LAB_CONTRACTS environment variable, then the repo-relative default
(this package sits next to `contracts/`).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from .errors import ContractsError

ENV_CONTRACTS_DIR = "AXOR_LAB_CONTRACTS"
_SCHEMA_SUFFIX = ".schema.json"

SCHEMA_NAMES = (
    "attestation",
    "bundle",
    "condition",
    "experiment",
    "predicate",
    "publication",
    "scenario",
    "tool-manifest",
    "trace",
)


def contracts_dir() -> Path:
    env = os.environ.get(ENV_CONTRACTS_DIR)
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "contracts"


def _load_from_package_data() -> dict[str, dict[str, object]] | None:
    """Load schemas shipped as package data (lab_contracts/schemas/*.json).

    This is the installed-wheel path (review §12): a wheel bundles the schemas
    next to the code, so `axor-lab` works outside a source checkout with no
    environment variable. Returns None if the package data is absent (source
    checkout without a build step) so the caller falls back to contracts/.
    """
    try:
        from importlib.resources import files  # noqa: PLC0415

        anchor = files("lab_contracts").joinpath("schemas")
        if not anchor.is_dir():
            return None
        schemas: dict[str, dict[str, object]] = {}
        for name in SCHEMA_NAMES:
            resource = anchor.joinpath(f"{name}{_SCHEMA_SUFFIX}")
            if not resource.is_file():
                return None
            schemas[name] = json.loads(resource.read_text())
        return schemas
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return None


@lru_cache(maxsize=4)
def load_schemas(directory: str | None = None) -> dict[str, dict[str, object]]:
    """Load every contract schema, keyed by short name (e.g. 'trace').

    Resolution order: explicit `directory` arg → AXOR_LAB_CONTRACTS env →
    package data (installed wheel) → the repo-relative contracts/ directory.
    """
    if directory is None and ENV_CONTRACTS_DIR not in os.environ:
        packaged = _load_from_package_data()
        if packaged is not None:
            return packaged
    schemas_path = (Path(directory) if directory else contracts_dir()) / "schemas"
    if not schemas_path.is_dir():
        raise ContractsError(
            f"contract schemas not found (package data missing and {schemas_path} absent); "
            f"set {ENV_CONTRACTS_DIR} to the contracts/ directory"
        )
    schemas: dict[str, dict[str, object]] = {}
    for path in sorted(schemas_path.glob(f"*{_SCHEMA_SUFFIX}")):
        schemas[path.name.removesuffix(_SCHEMA_SUFFIX)] = json.loads(path.read_text())
    missing = set(SCHEMA_NAMES) - set(schemas)
    if missing:
        raise ContractsError(f"missing contract schemas: {sorted(missing)}")
    return schemas
