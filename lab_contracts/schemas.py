"""Loading of the contract JSON Schemas.

Thirteen schemas, from two owners.

`trace`, `tool-manifest` and `predicate` describe artifacts that cross a
product boundary, and they belong to `axor_core.contracts.schemas`. This repo
used to hold its own copies — three times over, counting a byte-copy under
`lab_contracts/schemas/` and narrower stubs under a directory named after the
owner that did not have them. The kernel ships them now, so the copies are
gone and these three are imported. A schema with two definitions has none.

The other ten — `artifact`, `attestation`, `bundle`, `condition`,
`evidence-case`, `experiment`, `publication`, `regression`, `scenario`,
`suite` — are the Lab's own product and stay in `contracts/`, which remains
their source of truth; this module only loads them. Resolution order for that
directory: an explicit argument, the AXOR_LAB_CONTRACTS environment variable,
package data (installed wheel), then the repo-relative default (this package
sits next to `contracts/`).

All thirteen are returned in ONE dictionary because they reference each other
across the split: a `condition` refs `predicate`, a `scenario` refs
`tool-manifest`. A validator handed only half resolves neither.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from axor_core.contracts.schemas import load as load_kernel_schema

from .errors import ContractsError

ENV_CONTRACTS_DIR = "AXOR_LAB_CONTRACTS"
_SCHEMA_SUFFIX = ".schema.json"

# Owned by axor-core and imported from it — never read off this repo's disk.
KERNEL_SCHEMA_NAMES = ("predicate", "tool-manifest", "trace")

# The Lab's own product schemas, loaded from contracts/.
LAB_SCHEMA_NAMES = (
    "artifact",
    "attestation",
    "bundle",
    "condition",
    "evidence-case",
    "experiment",
    "publication",
    "regression",
    "scenario",
    "suite",
)

SCHEMA_NAMES = tuple(sorted(LAB_SCHEMA_NAMES + KERNEL_SCHEMA_NAMES))


def contracts_dir() -> Path:
    env = os.environ.get(ENV_CONTRACTS_DIR)
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "contracts"


def _load_from_package_data() -> dict[str, dict[str, object]] | None:
    """Load the LAB schemas shipped as package data (lab_contracts/schemas/).

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
        for name in LAB_SCHEMA_NAMES:
            resource = anchor.joinpath(f"{name}{_SCHEMA_SUFFIX}")
            if not resource.is_file():
                return None
            schemas[name] = json.loads(resource.read_text())
        return schemas
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return None


@lru_cache(maxsize=4)
def load_schemas(directory: str | None = None) -> dict[str, dict[str, object]]:
    """Every contract schema, keyed by short name (e.g. 'trace').

    The Lab's ten come from disk (explicit `directory` arg → AXOR_LAB_CONTRACTS
    env → package data → the repo-relative contracts/ directory); the kernel's
    three come from axor-core, wherever this repo's contracts happen to live.
    """
    # The kernel's three go on top, so a stale copy left on disk under one of
    # their names cannot shadow the owner's. There is no local override.
    # Named individually rather than taking everything axor-core ships: it also
    # owns `cp-deploy`, which is the Control Plane's handoff format and has no
    # business in the set the Lab validates its own artifacts against.
    kernel = {name: load_kernel_schema(name) for name in KERNEL_SCHEMA_NAMES}
    return {**_load_lab_schemas(directory), **kernel}


def _load_lab_schemas(directory: str | None) -> dict[str, dict[str, object]]:
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
    missing = set(LAB_SCHEMA_NAMES) - set(schemas)
    if missing:
        raise ContractsError(f"missing contract schemas: {sorted(missing)}")
    return schemas
