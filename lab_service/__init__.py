"""lab_service — the product's verbs, with no face attached.

`validate`, `run`, `replay`, `pin`, `regress`, `evidence`, `publish`,
`export-cp`, `verify` are what Axor Lab DOES. They belonged to
`lab_runner/cli.py`, so having a shell was a precondition for using half the
product: the hosted face could not export a Control Plane handoff, verify one,
verify a reproduction package, or import an incident, because the logic was not
reachable from anywhere but argv.

A verb here takes loaded domain objects and explicit arguments, and returns a
frozen result carrying an `Outcome`. It never prints, never reads `sys.argv`,
never calls `sys.exit`, and never speaks HTTP. Rendering belongs to the faces:
`lab_runner/cli.py` turns a result into text and an exit code, `lab_server` into
a payload and a status. Neither is privileged, and neither can drift from the
other, because there is one implementation between them.
"""

from __future__ import annotations

from .checks import Check, CheckStatus
from .errors import OutputRefused, ServiceError
from .packages import PackageVerifyResult, verify_package, verify_package_document
from .incidents import IncidentImportResult, import_incident
from .suites import (
    BenchmarkImportResult,
    SuitePlan,
    SuiteRunResult,
    build_agentdojo_experiment,
    execute_suite_run,
    plan_suite_run,
    resolve_suite_target,
)
from .publishing import (
    LocalPublishResult,
    UploadResult,
    build_local_publication,
    check_publishable,
    default_acceptance_path,
    upload_publication,
)
from .evidence import (
    EvidenceResult,
    PinResult,
    RegressionResult,
    build_evidence,
    check_regression,
    enforcing_condition,
    first_denied_trace,
    pin_trace,
    scenario_for,
)
from .experiments import (
    ReplayResult,
    RunPlan,
    RunResult,
    ValidationResult,
    agent_is_deterministic,
    build_environment,
    compute_aggregates,
    derive_run_id,
    effective_design,
    execute_run,
    plan_run,
    repin_to_real_kernel,
    replay_source,
    validate_experiment,
)
from .handoff import (
    CPExportPackage,
    CPExportResult,
    CPVerifyResult,
    build_cp_export_files,
    bundle_files,
    export_cp_package,
    read_bundle_files,
    verify_cp_files,
    verify_cp_package,
)
from .outcomes import Outcome

__all__ = [
    "bundle_files",
    "verify_cp_files",
    "read_bundle_files",
    "build_cp_export_files",
    "CPExportPackage",
    "resolve_suite_target",
    "plan_suite_run",
    "execute_suite_run",
    "build_agentdojo_experiment",
    "SuiteRunResult",
    "SuitePlan",
    "BenchmarkImportResult",
    "upload_publication",
    "default_acceptance_path",
    "check_publishable",
    "build_local_publication",
    "UploadResult",
    "LocalPublishResult",
    "scenario_for",
    "pin_trace",
    "first_denied_trace",
    "enforcing_condition",
    "check_regression",
    "build_evidence",
    "RegressionResult",
    "PinResult",
    "EvidenceResult",
    "validate_experiment",
    "replay_source",
    "repin_to_real_kernel",
    "plan_run",
    "execute_run",
    "effective_design",
    "derive_run_id",
    "compute_aggregates",
    "build_environment",
    "agent_is_deterministic",
    "ValidationResult",
    "RunResult",
    "RunPlan",
    "ReplayResult",
    "CPExportResult",
    "CPVerifyResult",
    "Check",
    "IncidentImportResult",
    "CheckStatus",
    "Outcome",
    "PackageVerifyResult",
    "OutputRefused",
    "ServiceError",
    "export_cp_package",
    "import_incident",
    "verify_cp_package",
    "verify_package",
    "verify_package_document",
]
