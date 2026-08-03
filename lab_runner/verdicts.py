"""Reading a recorded decision: what was DECIDED vs what was OBEYED.

These are two different questions and conflating them is what made an
observe-only arm useless. A `gate_decision` carries:

  - ``verdict``  — what the kernel decided over this call's provenance;
  - ``enforced`` — whether the runtime acted on that verdict.

An observe-only (``enforcement: off``) arm records the REAL verdict — including
DENY — and executes anyway. So "did this call run?" is not "is the verdict
ALLOW"; it is ALLOW *or* not enforced. Every consumer that asks that question
must ask it through here, because a consumer that reads `verdict` alone scores
an ungoverned run as if governance had contained it.

``enforced`` is absent in traces recorded before it existed. Those runs were all
enforced, so absence reads as ``True`` — the reading that leaves a legacy
governed trace scored exactly as it was.
"""

from __future__ import annotations


def was_enforced(decision: dict[str, object]) -> bool:
    """Whether the runtime obeyed this verdict. Absent ⇒ True (legacy)."""
    return bool(decision.get("enforced", True))


def executed_under(decision: dict[str, object]) -> bool:
    """Whether the call this decision governs actually ran."""
    return str(decision.get("verdict")) == "ALLOW" or not was_enforced(decision)


def contained(decision: dict[str, object]) -> bool:
    """Whether this decision actually STOPPED something — a denial that was
    obeyed. The honest measure of governance: an unenforced DENY is evidence of
    what governance would have caught, not of anything it prevented."""
    return str(decision.get("verdict")) == "DENY" and was_enforced(decision)
