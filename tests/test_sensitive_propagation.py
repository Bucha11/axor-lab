"""Sensitive labels propagate through model_extraction (review round 6, P0).

The redaction of a sensitive SOURCE value was pointless if the model could copy
the secret into a sink argument: the derived model_extraction value inherited
only untrusted_derived and kept its raw preview + decision_value, re-exposing the
secret in the trace / bundle / EvidenceCase / server. The conservative join must
carry the whole security-label lattice — untrusted AND sensitive — and redact a
derived value that inherits sensitive.
"""

from __future__ import annotations

import json
import unittest

from lab_runner import ValueLedger

SECRET = "sk-secret-abc-123"


class TestSensitivePropagation(unittest.TestCase):
    def test_derived_value_inherits_sensitive_and_is_redacted(self) -> None:
        led = ValueLedger()
        v_secret = led.mint_external_read(SECRET, "tool_result:read_key:api_key", sensitive=True)
        # the model copies the secret verbatim into a sink argument
        v_derived = led.mint_model_extraction(SECRET, context_value_ids=(v_secret,))
        derived = led.get(v_derived)
        self.assertIn("untrusted_derived", derived["labels"])
        self.assertIn("sensitive", derived["labels"])  # inherited, not dropped
        self.assertEqual(derived["preview"], "[redacted]")
        self.assertNotIn("decision_value", derived)  # no raw value stored

    def test_the_raw_secret_appears_nowhere_in_the_serialized_ledger(self) -> None:
        led = ValueLedger()
        v_secret = led.mint_external_read(SECRET, "tool_result:read_key:api_key", sensitive=True)
        led.mint_model_extraction(SECRET, context_value_ids=(v_secret,))
        blob = json.dumps(led.values)
        self.assertNotIn(SECRET, blob)  # source AND derived both redacted

    def test_non_sensitive_untrusted_derived_is_not_redacted(self) -> None:
        # regression: the normal banking slice recipient (untrusted, NOT sensitive)
        # must keep its typed decision_value so replay stays exact
        led = ValueLedger()
        v_u = led.mint_external_read("attacker@example.com", "tool_result:read:desc")
        v_d = led.mint_model_extraction("attacker@example.com", context_value_ids=(v_u,))
        derived = led.get(v_d)
        self.assertIn("untrusted_derived", derived["labels"])
        self.assertNotIn("sensitive", derived["labels"])
        self.assertEqual(derived["preview"], "attacker@example.com")
        self.assertEqual(derived["decision_value"], "attacker@example.com")

    def test_clean_model_output_stays_clean(self) -> None:
        led = ValueLedger()
        v_clean = led.mint_constant("landlord@example.com", "prompt:landlord")
        v_d = led.mint_model_extraction("landlord@example.com", context_value_ids=(v_clean,))
        derived = led.get(v_d)
        self.assertNotIn("sensitive", derived["labels"])
        self.assertNotIn("untrusted_derived", derived["labels"])

    def test_real_kernel_registration_reads_runtime_value_not_serialized(self) -> None:
        # the exact pattern the real-kernel path uses: it must get the RAW value
        # even for a redacted sensitive value, where reading decision_value off
        # the serialized dict used to KeyError and fail the whole trial (r7)
        led = ValueLedger()
        vid = led.mint_external_read(SECRET, "tool_result:read:key", sensitive=True)
        with self.assertRaises(KeyError):
            _ = led.get(vid)["decision_value"]  # the OLD (crashing) access
        registrations = [("read_key", led.runtime_value(v)) for v in [vid]]  # the NEW access
        self.assertEqual(registrations[0][1], SECRET)  # kernel sees the raw value
        # ...but the serialized trace values still never carry the secret
        self.assertNotIn(SECRET, json.dumps(led.values))


if __name__ == "__main__":
    unittest.main()
