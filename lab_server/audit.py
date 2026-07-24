"""Append-only audit log — the spine of the paid Security-Workspace features.

A hosted Private Lab records every workflow action (an incident imported, a
regression run, an approval granted, a report exported) as an immutable,
timestamped entry. The same log powers three Security-tier capabilities:

  * **history** — the ordered record of what happened, and when;
  * **approvals** — an approval is itself an entry, so who signed off is auditable;
  * **compliance export** — a period report is an aggregation over the log.

Persisted as JSON lines under ``<store_root>/audit/log.jsonl`` — dependency-free
and human-readable, in the same file-store spirit as the publication/incident
stores. Append-only: entries are never edited or deleted, so the log is evidence.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# The action types the workflow records. New types are additive — a reader that
# does not know a type still lists it (the log is the source of truth).
INCIDENT_IMPORTED = "incident_imported"
INCIDENT_PINNED = "incident_pinned"
REGRESSION_RUN = "regression_run"
APPROVAL_GRANTED = "approval_granted"
REPORT_EXPORTED = "report_exported"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class AuditLog:
    """An append-only, persisted action log. Thread-safe; loads on construction."""

    root: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _events: list[dict[str, object]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        path = self.root / "log.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._events.append(json.loads(line))
                except ValueError:
                    # one corrupt line skips that ONE entry, never the whole log
                    continue

    def append(
        self,
        action: str,
        *,
        actor: str = "system",
        target: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Record one action; return the persisted entry (with seq + timestamp)."""
        with self._lock:
            entry: dict[str, object] = {
                "seq": len(self._events),
                "ts": _utc_now_iso(),
                "action": action,
                "actor": actor,
            }
            if target is not None:
                entry["target"] = target
            if detail:
                entry["detail"] = detail
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "log.jsonl").open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
            self._events.append(entry)
            return entry

    def list(
        self, *, since: str | None = None, until: str | None = None
    ) -> list[dict[str, object]]:
        """The log, oldest first, optionally bounded by ISO-timestamp `since`
        (inclusive) / `until` (exclusive) — lexicographic compare works on the
        Z-suffixed ISO timestamps this log writes."""
        with self._lock:
            out = list(self._events)
        return [
            e
            for e in out
            if (since is None or str(e.get("ts", "")) >= since)
            and (until is None or str(e.get("ts", "")) < until)
        ]

    def approvals_for(self, target: str) -> list[dict[str, object]]:
        return [
            e
            for e in self.list()
            if e.get("action") == APPROVAL_GRANTED and e.get("target") == target
        ]

    def is_approved(self, target: str) -> bool:
        return bool(self.approvals_for(target))
