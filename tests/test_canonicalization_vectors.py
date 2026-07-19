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


if __name__ == "__main__":
    unittest.main()
