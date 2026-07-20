"""§6.2 — canonicalization is pinned and RFC8785-equal on the float-free subset.

Golden (bytes, sha256) vectors lock Lab's canonicalization so it cannot change
silently, and — when axor-core is present — prove it is byte-identical to the
production RFC8785 canonicalizer (the one the Control Plane signs with).
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from lab_contracts.canonical import canonical_json, content_hash

VECTORS = json.loads((Path(__file__).resolve().parent.parent / "contracts" /
                      "canonicalization-vectors.json").read_text())
_HAS_AXOR = importlib.util.find_spec("axor_core") is not None


class TestCanonicalizationVectors(unittest.TestCase):
    def test_golden_vectors_are_stable(self) -> None:
        for vector in VECTORS:
            self.assertEqual(canonical_json(vector["input"]), vector["canonical"])
            self.assertEqual(content_hash(vector["input"]), vector["sha256"])

    @unittest.skipUnless(_HAS_AXOR, "axor-core not installed")
    def test_byte_identical_to_axor_core_rfc8785(self) -> None:
        from axor_core.kernel import canonicalize

        for vector in VECTORS:
            self.assertEqual(
                canonical_json(vector["input"]).encode("utf-8"),
                canonicalize(vector["input"]),
                f"canonicalization differs from axor-core on {vector['input']}",
            )

    def test_nan_and_infinity_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            canonical_json({"x": float("nan")})

    def test_floats_use_rfc8785_ecmascript_form_not_python_repr(self) -> None:
        # the divergent cases where Python's repr is NOT RFC 8785 — a bundle's
        # aggregate floats must serialize the ECMAScript way so a TS/Rust
        # verifier computes the same hash (review r13)
        cases = {
            0.0: "0", -0.0: "0", 1.0: "1", 100.0: "100", 1.5: "1.5",
            0.0001: "0.0001", 1e-7: "1e-7", 1e21: "1e+21",
            1e16: "10000000000000000", 0.6: "0.6",
        }
        for value, expected in cases.items():
            self.assertEqual(canonical_json(value), expected, f"float {value!r}")
        # and a float 0.0 hashes identically to the int 0 (JSON has one number type)
        self.assertEqual(content_hash({"n": 0.0}), content_hash({"n": 0}))


if __name__ == "__main__":
    unittest.main()
