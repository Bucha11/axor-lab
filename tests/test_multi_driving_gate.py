"""Multi-driving-arg gate correctness (review round 6, P0).

An allowlisted driving arg supersedes the taint floor for ITSELF only; it must
not short-circuit ALLOW and leave a later tainted driving arg unexamined. And an
egress sink that declares no driving args cannot be provenance-checked, so it
fails closed instead of ALLOWing an unverifiable call.
"""

from __future__ import annotations

import unittest

from lab_runner import Kernel
from tests import support

KERNEL = Kernel(version=support.KERNEL_PINNED)
TRUSTED = "trusted@example.com"
POLICY = {"allowlist": [TRUSTED]}
UNTRUSTED = ("untrusted_derived",)
TRUSTED_LABELS = ("prompt_given",)


def _manifest(driving_args: list[str]) -> dict:
    return {"id": "send_email", "effect": {"default_class": "EXPORT", "driving_args": driving_args}}


class TestMultiDrivingGate(unittest.TestCase):
    def _decide(self, manifest, args, arg_labels, arg_bindings):
        return KERNEL.decide(
            enforcement="on", manifest=manifest, args=args, arg_labels=arg_labels,
            arg_bindings=arg_bindings, inputs={}, policy=POLICY,
        )

    def test_allowlisted_first_arg_does_not_hide_tainted_second_arg(self) -> None:
        # recipient is untrusted BUT allowlisted; body is untrusted and NOT
        # allowlisted (an exfiltrated secret). The call must DENY on body.
        decision = self._decide(
            _manifest(["recipient", "body"]),
            {"recipient": TRUSTED, "body": "sk-secret-123"},
            {"recipient": UNTRUSTED, "body": UNTRUSTED},
            {"recipient": "v_r", "body": "v_b"},
        )
        self.assertEqual(decision["verdict"], "DENY")
        self.assertIn("body", decision["reason"])

    def test_all_driving_args_clean_or_allowlisted_allows(self) -> None:
        decision = self._decide(
            _manifest(["recipient", "body"]),
            {"recipient": TRUSTED, "body": "hello landlord"},
            {"recipient": UNTRUSTED, "body": TRUSTED_LABELS},
            {"recipient": "v_r", "body": "v_b"},
        )
        self.assertEqual(decision["verdict"], "ALLOW")
        self.assertIn("enum-supersession", decision["reason"])  # recipient was superseded

    def test_egress_with_no_driving_args_is_fail_closed(self) -> None:
        decision = self._decide(
            _manifest([]), {"recipient": TRUSTED}, {}, {},
        )
        self.assertEqual(decision["verdict"], "DENY")
        self.assertIn("no driving_args", decision["reason"])

    def test_single_allowlisted_arg_still_allows(self) -> None:
        # regression: the banking slice's single-arg supersession still works
        decision = self._decide(
            _manifest(["recipient"]),
            {"recipient": TRUSTED}, {"recipient": UNTRUSTED}, {"recipient": "v_r"},
        )
        self.assertEqual(decision["verdict"], "ALLOW")

    def test_single_tainted_unallowlisted_arg_denies(self) -> None:
        decision = self._decide(
            _manifest(["recipient"]),
            {"recipient": "attacker@evil.com"}, {"recipient": UNTRUSTED}, {"recipient": "v_r"},
        )
        self.assertEqual(decision["verdict"], "DENY")


if __name__ == "__main__":
    unittest.main()
