"""What a verb concluded, independent of who asked.

`lab_runner/cli.py` has always carried these six categories as exit codes, and
`main()` mapped every domain exception onto one of them. That mapping was the
service contract written in CLI terms: the categories say what HAPPENED, not how
to print it. They live here so both faces read the same answer — the CLI maps an
``Outcome`` to its exit code, an HTTP face to a status — and neither owns it.

`OK` is not the only non-error outcome. A regression that DIFFERS from its pin
is a real, expected result the user asked the question to get (policy is allowed
to change); an UNVERIFIED package is a definite answer too — integrity is not
authenticity, and refusing to conflate them is the point.
"""

from __future__ import annotations

from enum import Enum


class Outcome(Enum):
    """The six conclusions any verb can reach."""

    OK = "ok"
    FAILURE = "failure"
    VALIDATION = "validation"
    UNCONFIRMED = "unconfirmed"
    REGRESSION_DIFFERS = "regression_differs"
    UNVERIFIED = "unverified"
