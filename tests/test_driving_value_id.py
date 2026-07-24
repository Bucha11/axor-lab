"""`driving_value_id`: the real-governor path attributes its verdict to the
manifest's declared driving arg (`effect.driving_args`), not a hardcoded
`recipient`. So the live gate and replay pick the SAME value for any tool
(architecture rule 0), and a tool whose driving arg is not `recipient` is
honoured instead of silently reported as `v_none`. Pure function — no axor-core
needed."""

from __future__ import annotations

import unittest

from lab_runner.axor_backend import driving_value_id


class TestDrivingValueId(unittest.TestCase):
    def test_recipient_arg_banking(self) -> None:
        manifest = {"effect": {"default_class": "EXPORT", "driving_args": ["recipient"]}}
        self.assertEqual(
            driving_value_id(manifest, {"recipient": "v_r", "amount": "v_a"}), "v_r"
        )

    def test_non_recipient_driving_arg_is_honoured(self) -> None:
        # the old hardcode returned "v_none" here (no `recipient` bound); the
        # manifest-driven selection returns the actual driving value
        manifest = {"effect": {"default_class": "EXPORT", "driving_args": ["url"]}}
        self.assertEqual(driving_value_id(manifest, {"url": "v_url", "body": "v_b"}), "v_url")

    def test_declared_arg_not_bound_is_v_none(self) -> None:
        manifest = {"effect": {"driving_args": ["recipient"]}}
        self.assertEqual(driving_value_id(manifest, {"amount": "v_a"}), "v_none")

    def test_no_driving_args_is_v_none(self) -> None:
        self.assertEqual(driving_value_id({"effect": {"driving_args": []}}, {"x": "v_x"}), "v_none")
        self.assertEqual(driving_value_id({"effect": {}}, {"x": "v_x"}), "v_none")


if __name__ == "__main__":
    unittest.main()
