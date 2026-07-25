"""The browser verifier canonicalizes byte-identically to the Python one.

`axor-lab verify` is the one capability that could not move to the server: its
whole value is that no server is trusted, so "the server checked itself" would
have destroyed the guarantee rather than moved it. It moved into the BROWSER
instead — the reader's own machine, over bytes the reader already holds.

That only works if the two canonicalizers agree exactly. A content hash the
browser computes differently from the server is worse than no check: it reports
tampering on honest packages and, in the other direction, could pass an altered
one. This runs the SAME `contracts/canonicalization-vectors.json` the Python
implementation is pinned against through the TypeScript one, and compares both
the canonical string and the digest.

RFC 8785 was written around ECMAScript semantics, which is why the TS side is
short: JSON.stringify already emits the required number form and JavaScript
string comparison is already by UTF-16 code unit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VECTORS = REPO / "contracts" / "canonicalization-vectors.json"
VERIFY_TS = REPO / "frontend" / "src" / "verify.ts"

# Node runs the .ts directly with type stripping (Node 22+). Absent or too old,
# the test skips — the same posture as the optional Ed25519/BYOK paths.
_HARNESS = """
import { canonicalJson } from %(module)s;
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const vectors = JSON.parse(readFileSync(%(vectors)s, "utf8"));
const out = vectors.map((v) => {
  const canonical = canonicalJson(v.input);
  return {
    canonical,
    sha256: "sha256:" + createHash("sha256").update(canonical, "utf8").digest("hex"),
  };
});
process.stdout.write(JSON.stringify(out));
"""


def _node() -> str | None:
    return shutil.which("node")


class BrowserCanonicalizationTest(unittest.TestCase):
    def test_the_typescript_canonicalizer_matches_the_python_vectors(self) -> None:
        node = _node()
        if node is None:
            self.skipTest("node is not installed")

        vectors = json.loads(VECTORS.read_text())
        self.assertTrue(vectors, "the vector file must not be empty")

        harness = _HARNESS % {
            "module": json.dumps(VERIFY_TS.as_uri()),
            "vectors": json.dumps(str(VECTORS)),
        }
        result = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-"],
            input=harness, capture_output=True, text=True, cwd=REPO, timeout=120,
        )
        if result.returncode != 0:
            if "strip-types" in result.stderr or "Unknown" in result.stderr:
                self.skipTest(f"this node cannot run TypeScript directly: {result.stderr[:200]}")
            self.fail(f"the TS harness failed: {result.stderr[:500]}")

        produced = json.loads(result.stdout)
        self.assertEqual(len(produced), len(vectors))
        for index, (vector, got) in enumerate(zip(vectors, produced, strict=True)):
            with self.subTest(vector=index):
                # the canonical STRING, not just the digest: a digest match with a
                # different string would mean the two agree by luck on this input
                self.assertEqual(got["canonical"], vector["canonical"])
                self.assertEqual(got["sha256"], vector["sha256"])

    def test_python_still_agrees_with_its_own_vectors(self) -> None:
        # The other half of the pin: if the vectors ever drift from the Python
        # implementation, the cross-language test above would be comparing the TS
        # side against a stale file and quietly prove nothing.
        from lab_contracts import canonical_json, content_hash

        for index, vector in enumerate(json.loads(VECTORS.read_text())):
            with self.subTest(vector=index):
                self.assertEqual(canonical_json(vector["input"]), vector["canonical"])
                self.assertEqual(content_hash(vector["input"]), vector["sha256"])


if __name__ == "__main__":
    unittest.main()
