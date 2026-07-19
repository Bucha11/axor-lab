"""lab_adapters — benchmark imports (MVP contract item 2).

Materializes external benchmarks as `scenario/v1` objects. The curated
AgentDojo banking suite is the MVP's one import; more suites and live-import
adapters are later plan phases.
"""

from .agentdojo import (
    DATASET_VERSION,
    available_suites,
    build_experiment_document,
    import_suite,
    manifests,
)
from .errors import AdapterError, UnknownSuiteError

__all__ = [
    "AdapterError",
    "DATASET_VERSION",
    "UnknownSuiteError",
    "available_suites",
    "build_experiment_document",
    "import_suite",
    "manifests",
]
