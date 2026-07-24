"""Regression pins for imported incidents — the corpus behind the paid
incident→regression workflow.

A pin freezes an incident's recorded verdict sequence
(`lab_runner.regression.pin`); a regression run re-verifies every pinned
incident still reproduces under its recorded condition
(`lab_runner.regression.check_pins`) — the SAME code the CLI `axor-lab regress`
runs, so a server run and a CLI run cannot diverge. Pins persist as JSON lines
under `<store_root>/pins/pins.jsonl`; the corpus is small (a pilot), so each
change rewrites the file atomically.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MUST_BLOCK = "must_block"
MUST_PASS = "must_pass"


def side_for(verdict: str) -> str:
    """A DENY verdict must keep blocking (must_block); anything else must keep
    passing (must_pass) — the two sides of the corpus CI."""
    return MUST_BLOCK if verdict == "DENY" else MUST_PASS


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


@dataclass
class PinStore:
    """The incident regression corpus: at most one pin per trace_id (a re-pin
    replaces), persisted and thread-safe."""

    root: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pins: dict[str, dict[str, object]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        path = self.root / "pins.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                # last write wins per trace_id (the file is append-then-rewrite,
                # but tolerate a stray duplicate on cold load)
                self._pins[str(record["trace_id"])] = record

    def _flush(self) -> None:
        body = "".join(json.dumps(r) + "\n" for r in self._pins.values())
        _write_atomic(self.root / "pins.jsonl", body)

    def add(
        self,
        *,
        incident_id: str,
        trace_id: str,
        trace_ref: str,
        expected_verdict: str,
        expected_sequence: list[str],
        side: str,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "trace_id": trace_id,
            "incident_id": incident_id,
            "trace_ref": trace_ref,
            "expected_verdict": expected_verdict,
            "expected_sequence": expected_sequence,
            "side": side,
            "pinned_at": _utc_now_iso(),
        }
        with self._lock:
            self._pins[trace_id] = record
            self._flush()
        return record

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            return sorted(self._pins.values(), key=lambda r: str(r["trace_id"]))

    def by_incident(self, incident_id: str) -> dict[str, object] | None:
        with self._lock:
            for record in self._pins.values():
                if record.get("incident_id") == incident_id:
                    return record
        return None
