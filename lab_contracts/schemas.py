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


@lru_cache(maxsize=4)
def load_schemas(directory: str | None = None) -> dict[str, dict[str, object]]:
    """Load every contract schema, keyed by short name (e.g. 'trace')."""
    schemas_path = (Path(directory) if directory else contracts_dir()) / "schemas"
    if not schemas_path.is_dir():
        raise ContractsError(
            f"contract schemas directory not found at {schemas_path}; "
            f"set {ENV_CONTRACTS_DIR} to the contracts/ directory"
        )
    schemas: dict[str, dict[str, object]] = {}
    for path in sorted(schemas_path.glob(f"*{_SCHEMA_SUFFIX}")):
        schemas[path.name.removesuffix(_SCHEMA_SUFFIX)] = json.loads(path.read_text())
    missing = set(SCHEMA_NAMES) - set(schemas)
    if missing:
        raise ContractsError(f"missing contract schemas: {sorted(missing)}")
    return schemas
