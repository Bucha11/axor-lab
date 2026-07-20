"""lab_contracts — the contract layer of Axor Lab (Phase 0 of the plan).

Owns everything data-level: schema loading and validation (the contracts'
own subset validator, cwd-independent), semantic checks JSON Schema can't
express, canonical JCS hashing, bundle assembly/verification, and typed
publication claims. Execution lives in lab_runner; statistics in
lab_analysis.
"""

from .bundle import build_bundle, verify_bundle
from .canonical import canonical_json, condition_config_hash, content_hash, world_digest
from .errors import (
    BundleIntegrityError,
    ClaimTypingError,
    ContractsError,
    ScenarioValidationError,
    SchemaValidationError,
    UnresolvedInputError,
)
from .publication import add_reproduction, build_publication, make_claim, provenance_axes
from .schemas import contracts_dir, load_schemas
from .semantics import (
    EGRESS_CLASSES,
    KNOWN_MATCHERS,
    SINK_CLASSES,
    trace_semantics,
    validate_artifact,
    validate_scenario,
)

__all__ = [
    "BundleIntegrityError",
    "ClaimTypingError",
    "ContractsError",
    "EGRESS_CLASSES",
    "KNOWN_MATCHERS",
    "SINK_CLASSES",
    "ScenarioValidationError",
    "SchemaValidationError",
    "UnresolvedInputError",
    "add_reproduction",
    "build_bundle",
    "build_publication",
    "canonical_json",
    "condition_config_hash",
    "content_hash",
    "world_digest",
    "contracts_dir",
    "load_schemas",
    "make_claim",
    "provenance_axes",
    "trace_semantics",
    "validate_artifact",
    "validate_scenario",
    "verify_bundle",
]
