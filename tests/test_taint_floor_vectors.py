"""Lab's reference `decide`, against the kernel's shared taint-floor vectors.

`axor_core/vectors/taint_floor.json` is the ecosystem's statement of what the
taint floor decides, read from the installed kernel — there is no copy of it
here. Until recently three implementations of that predicate existed — axor-core's gate, this reference kernel, and a hand-written mirror of
this one inside the Control Plane's incident export — with nothing holding them
in agreement and no shared file to check against.

This module is the Lab-side half of the fix. `Kernel.decide` no longer decides
the taint question itself; it asks `axor_core.policy.gates.taint_gate`. These
tests prove the translation into that call is faithful: Lab speaks in ledger
LABELS and manifest effect classes, the gate speaks in `CausalRoot` and
`egress_sinks`, and the two must reach the same verdict on every vector the
kernel publishes.

Vectors whose signal has no Lab equivalent are skipped explicitly rather than
quietly reinterpreted — Lab's reference covers the integrity axis on an egress
sink and nothing else, and pretending otherwise is how a conformance file starts
lying.
"""

from __future__ import annotations

import pytest

from axor_core.vectors import TAINT_FLOOR
from lab_runner.kernel import Kernel
from lab_runner.ledger import LABEL_SENSITIVE, LABEL_UNTRUSTED

CASES = TAINT_FLOOR()["vectors"]

# Signals this reference kernel has no way to express. It resolves an effect
# class from a manifest, so the normalizer's structural fields never reach it,
# and it has no confidentiality floor at all.
_NO_LAB_EQUIVALENT = {
    "writes_outside_workdir",
    "executes_generated_code",
}


def _egress(vec: dict) -> bool:
    """Whether Lab would class this call as an egress sink."""
    return bool(vec.get("egress_sinks")) or vec["normalized"].get(
        "destination_kind"
    ) in ("external_domain", "private_network")


def _labels(vec: dict) -> tuple[str, ...]:
    root = vec["root"]
    labels = [LABEL_UNTRUSTED] if root.get("sources") else ["trusted"]
    if root.get("sensitive"):
        labels.append(LABEL_SENSITIVE)
    return tuple(labels)


@pytest.mark.parametrize("vec", CASES, ids=[v["name"] for v in CASES])
def test_lab_reaches_the_same_verdict_as_the_kernel(vec: dict) -> None:
    if any(vec["normalized"].get(f) for f in _NO_LAB_EQUIVALENT):
        pytest.skip("signal has no representation in a Lab manifest")
    if vec.get("floor_active"):
        pytest.skip("the reference kernel has no confidentiality floor")

    tool = vec["tool"]
    manifest = {
        "id": tool,
        "effect": {
            "default_class": "EXPORT" if _egress(vec) else "READ",
            "driving_args": ["text"],
        },
    }
    decision = Kernel(version="reference_taint_floor_kernel").decide(
        enforcement="on",
        manifest=manifest,
        args={"text": "v"},
        arg_labels={"text": _labels(vec)},
        arg_bindings={"text": "v_1"},
        inputs={},
        # A vector marked superseded is one the operator allowlisted; Lab
        # expresses that as the value being IN the condition's allowlist.
        policy={"allowlist": ["v"]} if vec.get("integrity_superseded") else None,
    )
    expected = "ALLOW" if vec["expect"] == "allow" else "DENY"
    assert decision["verdict"] == expected, (
        f"kernel says {expected}, Lab says {decision['verdict']}: "
        f"{decision['reason']}"
    )


def test_the_suite_actually_exercised_both_verdicts() -> None:
    """A conformance run that skipped every DENY would pass and prove nothing."""
    exercised = [
        v for v in CASES
        if not any(v["normalized"].get(f) for f in _NO_LAB_EQUIVALENT)
        and not v.get("floor_active")
    ]
    assert {v["expect"] for v in exercised} == {"allow", "deny"}
