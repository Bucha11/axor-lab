"""The browser verifier accepts a genuine package and catches every tamper.

`axor-lab verify` could not move to the server — its whole value is that no
server is trusted, and a "the server checked itself" button would have deleted
that guarantee rather than moved it. It moved into the browser, where the
reader's own machine does the work.

A verifier is only worth having if BOTH halves hold: it must pass honest
packages (one that cries wolf gets ignored, which is worse than none) and fail
every doctored one. This drives the real TypeScript module over a real published
package, then over five specific attacks.

The downgrade case is the subtle one: detection cannot key on the PRESENCE of
proof objects, because an attacker strips the envelope and every proof together
and the result reads as an honest bare file. The envelope marker's ABSENCE is
what carries meaning.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from lab_server import make_runtime_server
from lab_server.app import make_server

REPO = Path(__file__).resolve().parent.parent
VERIFY_TS = REPO / "frontend" / "src" / "verify.ts"

_HARNESS = """
import { verifyPackage } from %(module)s;
import { readFileSync } from "node:fs";

const pkg = JSON.parse(readFileSync(%(package)s, "utf8"));
const clone = () => JSON.parse(JSON.stringify(pkg));
const out = {};
const failed = (r) => ({
  passed: r.passed,
  failures: r.checks.filter((c) => c.status === "fail").map((c) => c.name),
  statuses: Object.fromEntries(r.checks.map((c) => [c.name, c.status])),
});

out.genuine = failed(await verifyPackage(pkg));
out.downgraded = failed(await verifyPackage({ bundle: pkg.bundle, traces: pkg.traces }));
out.bare_allowed = failed(
  await verifyPackage({ bundle: pkg.bundle, traces: pkg.traces }, { allowBare: true }));

const tampered = clone();
tampered.traces[0].events[0].seq = 999;
out.tampered_trace = failed(await verifyPackage(tampered));

const swapped = clone();
swapped.publication.bundle_ref = "sha256:" + "0".repeat(64);
out.publication_swapped = failed(await verifyPackage(swapped));

const lifted = clone();
lifted.receipt.signed_ref = "sha256:" + "1".repeat(64);
out.receipt_lifted = failed(await verifyPackage(lifted));

const stripped = clone();
delete stripped.acceptance;
out.acceptance_stripped = failed(await verifyPackage(stripped));

process.stdout.write(JSON.stringify(out));
"""


def _publish_a_package(root: Path) -> dict:
    """Run the example, publish it, and download its reproduction package."""
    catalog = make_server(root, host="127.0.0.1", port=0)
    jobs = make_runtime_server(
        host="127.0.0.1", port=0, control_token=None, store_root=root / "rj",
    )
    for server in (catalog, jobs):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    cat_url = f"http://127.0.0.1:{catalog.server_address[1]}"
    job_url = f"http://127.0.0.1:{jobs.server_address[1]}"

    def call(base: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            base + path, data=data, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read())

    try:
        run = call(job_url, "/runs/local", {})
        assembled = call(job_url, f"/runs/{run['run_id']}/bundle")
        published = call(cat_url, "/api/publications", {
            "bundle": assembled["bundle"], "traces": assembled["traces"],
            "question": "does governance stop the exfil?", "visibility": "public",
        })
        return call(cat_url, f"/api/publications/{published['publication_id']}/bundle")
    finally:
        for server in (catalog, jobs):
            server.shutdown()
            server.server_close()


class BrowserVerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        node = shutil.which("node")
        if node is None:
            raise unittest.SkipTest("node is not installed")
        cls.node = node

        cls._tmp = TemporaryDirectory()
        root = Path(cls._tmp.name) / "store"
        package = _publish_a_package(root)
        package_path = Path(cls._tmp.name) / "package.json"
        package_path.write_text(json.dumps(package))

        harness = _HARNESS % {
            "module": json.dumps(VERIFY_TS.as_uri()),
            "package": json.dumps(str(package_path)),
        }
        result = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-"],
            input=harness, capture_output=True, text=True, cwd=REPO, timeout=300,
        )
        if result.returncode != 0:
            if "strip-types" in result.stderr or "Unknown" in result.stderr:
                raise unittest.SkipTest("this node cannot run TypeScript directly")
            raise AssertionError(f"the TS harness failed: {result.stderr[:600]}")
        cls.out = json.loads(result.stdout)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def test_a_genuine_package_passes(self) -> None:
        # The half people forget. A verifier that flags honest packages gets
        # switched off, and then it protects nobody.
        genuine = self.out["genuine"]
        self.assertTrue(genuine["passed"], genuine["failures"])
        self.assertEqual(genuine["failures"], [])
        # unsigned receipt → skipped, never "pass": integrity is not authenticity
        self.assertEqual(genuine["statuses"]["receipt signature"], "skipped")

    def test_a_downgraded_package_cannot_pass_as_bare(self) -> None:
        # Envelope and proofs stripped together. Keying detection on the presence
        # of proofs would read this as an honest bare file.
        downgraded = self.out["downgraded"]
        self.assertFalse(downgraded["passed"])
        self.assertIn("envelope", downgraded["failures"])

    def test_a_genuinely_bare_file_verifies_when_asked_for(self) -> None:
        bare = self.out["bare_allowed"]
        self.assertTrue(bare["passed"], bare["failures"])

    def test_a_tampered_trace_is_caught(self) -> None:
        tampered = self.out["tampered_trace"]
        self.assertFalse(tampered["passed"])
        self.assertIn("trace binding", tampered["failures"])

    def test_a_publication_committing_elsewhere_is_caught(self) -> None:
        swapped = self.out["publication_swapped"]
        self.assertFalse(swapped["passed"])
        self.assertIn("publication", swapped["failures"])

    def test_a_receipt_lifted_from_another_package_is_caught(self) -> None:
        lifted = self.out["receipt_lifted"]
        self.assertFalse(lifted["passed"])
        self.assertIn("receipt binding", lifted["failures"])

    def test_stripping_the_server_acceptance_is_caught(self) -> None:
        stripped = self.out["acceptance_stripped"]
        self.assertFalse(stripped["passed"])
        self.assertIn("acceptance", stripped["failures"])


if __name__ == "__main__":
    unittest.main()
