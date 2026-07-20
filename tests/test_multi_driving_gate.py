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
        # no provenance value exists → null driving_value_id + a typed reason,
        # NOT a fake "v_none" ledger id that would fail trace validation (r14)
        self.assertIsNone(decision["driving_value_id"])
        self.assertEqual(decision["driving_unresolved"], {"kind": "no_driving_args"})

    def test_unresolved_driving_arg_is_null_with_typed_reason(self) -> None:
        # a driving arg with no resolvable provenance and no binding → null + arg
        decision = self._decide(
            _manifest(["recipient"]), {"recipient": "x@y.com"}, {"recipient": ()}, {},
        )
        self.assertEqual(decision["verdict"], "DENY")
        self.assertIsNone(decision["driving_value_id"])
        self.assertEqual(decision["driving_unresolved"],
                         {"kind": "unresolved_argument", "arg": "recipient"})

    def test_non_egress_allow_with_no_driving_value_carries_a_typed_reason(self) -> None:
        # a non-egress tool with no driving args ALLOWs, but has no provenance
        # value — its null driving_value_id must still carry a typed reason so the
        # decision validates as a trace event (review r14)
        manifest = {"id": "read_doc", "effect": {"default_class": "READ", "driving_args": []}}
        decision = self._decide(manifest, {}, {}, {})
        self.assertEqual(decision["verdict"], "ALLOW")
        self.assertIsNone(decision["driving_value_id"])
        self.assertEqual(decision["driving_unresolved"], {"kind": "no_driving_args"})

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
