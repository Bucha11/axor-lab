"""Error hierarchy for lab_sandbox."""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for every lab_sandbox error."""


class SandboxDenied(SandboxError):
    """The sandbox policy denied an action (egress, resource, mount, ...).

    Every denial is audited; the reason is actionable.
    """

    def __init__(self, control: str, reason: str) -> None:
        super().__init__(f"[{control}] {reason}")
        self.control = control
        self.reason = reason
