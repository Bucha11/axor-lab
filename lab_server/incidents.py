"""Incident intake over HTTP — the Control Plane → Lab cross-link.

An incident package (`axor-lab-incident/v1`) is the JSON envelope the Control
Plane's "Open in Lab" flow ships: the production trace, its scenario, the tool
manifests, the EXACT recorded condition (verbatim), and an optional `source`
pointer back to the CP run. `POST /api/incidents` runs the SAME core the CLI
`import-incident` runs (lab_runner.incident.import_incident — schema, semantic
and cross-reference validation, config-hash check, replay under the recorded
condition BEFORE anything is written), then persists the package + the built
trace-replay bundle under `<store_root>/incidents/` — a separate namespace
from publications, never listed in the catalog.

Cold load re-runs the same import core over every persisted incident, so a
hand-assembled record dropped into the directory (or a tampered one) is
quarantined on restart rather than trusted — the same posture the publication
store takes (its review r8).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from lab_contracts import content_hash
from lab_runner.errors import IncidentImportError
from lab_runner.incident import import_incident

from .errors import NotFound, PublishRejected
from .store import _write_atomic

if TYPE_CHECKING:
    from collections.abc import Callable

INCIDENT_SCHEMA = "axor-lab-incident/v1"
# `source` is an optional pointer back to the producing product (the Control
# Plane run). Only these keys are kept, each a short string — the server never
# stores an arbitrary uploader-controlled blob under a trusted-looking field.
_SOURCE_KEYS = ("product", "run_id", "url")
_SOURCE_VALUE_MAX = 512


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class StoredIncident:
    incident_id: str
    package: dict[str, object]  # the axor-lab-incident/v1 envelope, as accepted
    bundle: dict[str, object]  # the trace-replay bundle the shared core built
    imported_at: str

    @property
    def trace(self) -> dict[str, object]:
        return self.package["trace"]  # type: ignore[return-value]

    @property
    def trace_id(self) -> str:
        return str(self.trace["trace_id"])

    @property
    def scenario_id(self) -> str:
        return str(self.package["scenario"]["name"])  # type: ignore[index]

    def summary(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "trace_id": self.trace_id,
            "scenario_id": self.scenario_id,
            "source": self.package.get("source"),
            "imported_at": self.imported_at,
        }


def _clean_source(source: object) -> dict[str, str] | None:
    """Normalize the optional `source` block to known string keys only."""
    if source is None:
        return None
    if not isinstance(source, dict):
        raise PublishRejected("incident source must be a JSON object", status=400)
    cleaned = {
        k: str(source[k])[:_SOURCE_VALUE_MAX] for k in _SOURCE_KEYS if source.get(k) is not None
    }
    return cleaned or None


def _envelope(payload: dict[str, object]) -> tuple[
    dict[str, object], dict[str, object], list[dict[str, object]],
    dict[str, object], dict[str, str] | None,
]:
    """Unpack + shape-check an incident package. Deep validation (schemas,
    semantics, cross-refs, config hash, replay) is the shared core's job."""
    if str(payload.get("schema_version", "")) != INCIDENT_SCHEMA:
        raise PublishRejected(
            f"not an {INCIDENT_SCHEMA} package (schema_version="
            f"{payload.get('schema_version')!r})"
        )
    trace = payload.get("trace")
    scenario = payload.get("scenario")
    manifests = payload.get("manifests")
    condition = payload.get("condition")
    if not isinstance(trace, dict) or not isinstance(scenario, dict) \
            or not isinstance(condition, dict):
        raise PublishRejected(
            "incident trace, scenario and condition must each be a JSON object", status=400
        )
    if not isinstance(manifests, list) or not all(isinstance(m, dict) for m in manifests):
        raise PublishRejected("incident manifests must be a list of JSON objects", status=400)
    return trace, scenario, manifests, condition, _clean_source(payload.get("source"))


@dataclass
class IncidentStore:
    """File-backed store of imported incidents (`<root>/incidents/<id>/`).

    Layout per incident: `incident.json` (the accepted envelope + incident_id +
    imported_at) and `bundle.json` (the built trace-replay bundle). The id is
    content-derived from the trace, so a re-import of the same incident is
    idempotent rather than a duplicate."""

    root: Path
    clock: Callable[[], str] = _utc_now_iso
    _cache: dict[str, StoredIncident] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in sorted(self.root.glob("*/")):
            # one corrupt incident skips that ONE record, never the whole store
            try:
                self._load(directory)
            except (OSError, ValueError, TypeError, KeyError, AttributeError,
                    IncidentImportError, PublishRejected):
                continue

    @staticmethod
    def derive_id(trace: dict[str, object]) -> str:
        """Content-address the incident by its trace — the same hash the shared
        core stamps into the bundle_id, so both surfaces agree on identity."""
        return "i_" + content_hash(trace).removeprefix("sha256:")[:32]

    # -- import (the HTTP twin of `axor-lab import-incident`) --------------

    def import_package(self, payload: dict[str, object]) -> tuple[StoredIncident, bool]:
        """Validate + replay + persist an incident package.

        Returns (stored, created): created=False means the identical incident
        was already imported (idempotent re-import). Raises IncidentImportError
        / IncidentReplayMismatch (from the shared core) or PublishRejected (bad
        envelope) — nothing is persisted on any failure."""
        trace, scenario, manifests, condition, source = _envelope(payload)
        # the SAME core the CLI runs — full validation + replay, no write yet
        result = import_incident(trace, scenario, manifests, condition)
        incident_id = self.derive_id(trace)
        package: dict[str, object] = {
            "schema_version": INCIDENT_SCHEMA,
            "trace": trace, "scenario": scenario,
            "manifests": manifests, "condition": condition,
        }
        if source:
            package["source"] = source
        with self._lock:
            existing = self._cache.get(incident_id)
            if existing is not None:
                return existing, False
            stored = StoredIncident(
                incident_id=incident_id, package=package,
                bundle=result.bundle, imported_at=self.clock(),
            )
            directory = self.root / incident_id
            _write_atomic(
                directory / "incident.json",
                json.dumps({
                    "incident_id": incident_id,
                    "imported_at": stored.imported_at,
                    "package": package,
                }, indent=2),
            )
            _write_atomic(directory / "bundle.json", json.dumps(result.bundle, indent=2))
            self._cache[incident_id] = stored
            return stored, True

    # -- reads --------------------------------------------------------------

    def get(self, incident_id: str) -> StoredIncident:
        stored = self._cache.get(incident_id)
        if stored is None:
            raise NotFound(f"incident {incident_id} not found")
        return stored

    def list(self) -> list[StoredIncident]:
        return sorted(self._cache.values(), key=lambda s: s.incident_id)

    def incidents_with_trace(self, trace_id: str) -> list[str]:
        return sorted(s.incident_id for s in self._cache.values() if s.trace_id == trace_id)

    # -- cold load -----------------------------------------------------------

    def _load(self, directory: Path) -> None:
        record = json.loads((directory / "incident.json").read_text())
        package: dict[str, object] = record["package"]
        trace, scenario, manifests, condition, _ = _envelope(package)
        incident_id = str(record["incident_id"])
        # the directory name and the persisted id must both re-derive from the
        # trace content — a renamed/transplanted record is not trusted
        if incident_id != directory.name or incident_id != self.derive_id(trace):
            return
        stored_bundle = json.loads((directory / "bundle.json").read_text())
        # re-run the FULL import core (validation + replay) and require the
        # rebuilt bundle to be byte-equal to the persisted one — a tampered
        # trace/condition/bundle is quarantined, exactly like the publication
        # store's semantic re-check on load
        result = import_incident(
            trace, scenario, manifests, condition,
            created=str(stored_bundle.get("created", "")) or None,
        )
        if result.bundle != stored_bundle:
            return
        self._cache[incident_id] = StoredIncident(
            incident_id=incident_id, package=package, bundle=result.bundle,
            imported_at=str(record.get("imported_at", "")),
        )
