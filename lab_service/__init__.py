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
from .handoff import CPExportResult, CPVerifyResult, export_cp_package, verify_cp_package
from .outcomes import Outcome

__all__ = [
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
