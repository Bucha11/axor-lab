"""A verification step and what it concluded.

Verifying a package is a SEQUENCE of distinct guarantees — integrity is not
authenticity, and neither is derivability — and a face has to be able to report
each separately or it teaches the reader to conflate them. The CLI used to do
that by printing as it went, which is exactly why the checks could not be run
from anywhere else.

A verb returns the checks it ran, in order, and a face renders them: the CLI
prints an `OK` to stdout and everything else to stderr, a hosted face turns the
same sequence into a payload. A check that never ran is absent — a report only
claims what it actually did.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CheckStatus(Enum):
    """How one verification step came out."""

    OK = "ok"
    INVALID = "invalid"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Check:
    """One named guarantee and the finding behind it."""

    name: str
    status: CheckStatus
    message: str

    @property
    def failed(self) -> bool:
        return self.status is CheckStatus.INVALID
