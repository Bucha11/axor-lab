"""Wrap API — the "upload agent code" ingest surface (lab_server/wrap_api.py).

The UI posts agent source files to /wrap/scan; axor-wrap statically detects the
tools and guesses effect classes. After a human classifies every UNKNOWN, the
UI posts the reviewed tools to /wrap/manifests and gets tool-manifest/v1 files
plus a GovernanceConfig-loadable YAML back. Real HTTP against the runtime-jobs
server, same style as test_runtime_jobs.py.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_server import make_runtime_server

try:
    import axor_wrap  # noqa: F401 — the optional wrap engine
    HAS_AXOR_WRAP = True
except ImportError:
    HAS_AXOR_WRAP = False

# a fixture langchain agent: a read tool that ingests the web, and a send tool
LANGCHAIN_AGENT = '''\
from langchain_core.tools import tool


@tool
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for a query."""
    return "results"


@tool("send_email")
def _send(to: str, subject: str, body: str) -> bool:
    """Send an email to a recipient."""
    return True


@tool
def frobnicate(blob: str) -> str:
    """Adjusts the frobnicator."""
    return blob
'''


class _Base(unittest.TestCase):
    control_token: str | None = "ctl"

    def setUp(self) -> None:
        self.server: ThreadingHTTPServer = make_runtime_server(
            host="127.0.0.1", port=0, control_token=self.control_token,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _req(self, method: str, path: str, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _scan(self, files, token="ctl"):
        return self._req("POST", "/wrap/scan", {"files": files}, token=token)


@unittest.skipUnless(HAS_AXOR_WRAP, "axor-wrap not installed")
class TestWrapScan(_Base):
    def test_scan_langchain_agent_detects_tools_with_guesses(self) -> None:
        status, out = self._scan([{"path": "agent.py", "content": LANGCHAIN_AGENT}])
        self.assertEqual(status, 200, out)
        by_id = {t["id"]: t for t in out["tools"]}
        self.assertEqual(set(by_id), {"search_web", "send_email", "frobnicate"})

        search = by_id["search_web"]
        self.assertEqual(search["framework"], "langchain")
        self.assertIn("agent.py:", search["source"])
        self.assertEqual(search["guess"]["default_class"], "READ")
        # a web-ingesting read carries the coarse untrusted candidate
        self.assertEqual(search["guess"]["untrusted_fields"], ["result.*"])
        self.assertIn("query", search["args_schema"]["properties"])

        send = by_id["send_email"]
        self.assertEqual(send["guess"]["default_class"], "EXPORT")
        self.assertEqual(send["guess"]["confidence"], "high")
        self.assertIn("to", send["guess"]["driving_args"])
        self.assertTrue(send["guess"]["reason"])

        # nothing matches "frobnicate" — UNKNOWN is an honest first-class outcome
        self.assertEqual(by_id["frobnicate"]["guess"]["default_class"], "UNKNOWN")

    def test_scan_requires_the_control_token(self) -> None:
        status, _ = self._scan([{"path": "a.py", "content": "x = 1"}], token=None)
        self.assertEqual(status, 401)
        status, _ = self._scan([{"path": "a.py", "content": "x = 1"}], token="wrong")
        self.assertEqual(status, 401)

    def test_path_traversal_and_absolute_paths_are_rejected(self) -> None:
        for bad in ("../evil.py", "a/../../evil.py", "/etc/evil.py",
                    "..\\evil.py", "C:\\evil.py"):
            status, out = self._scan([{"path": bad, "content": "x = 1"}])
            self.assertEqual(status, 400, (bad, out))

    def test_non_python_files_are_rejected(self) -> None:
        status, out = self._scan([{"path": "notes.txt", "content": "hi"}])
        self.assertEqual(status, 400, out)
        self.assertIn(".py", out["error"])

    def test_oversized_payload_is_413(self) -> None:
        big = "# " + "x" * (2 * 1024 * 1024 + 100)
        status, out = self._scan([{"path": "big.py", "content": big}])
        self.assertEqual(status, 413, out)

    def test_malformed_bodies_are_clean_400s(self) -> None:
        self.assertEqual(self._req("POST", "/wrap/scan", {}, token="ctl")[0], 400)
        self.assertEqual(self._scan("nope")[0], 400)
        self.assertEqual(self._scan([{"path": "a.py"}])[0], 400)  # no content

    def test_nested_relative_paths_are_allowed(self) -> None:
        status, out = self._scan([{"path": "pkg/tools.py", "content": LANGCHAIN_AGENT}])
        self.assertEqual(status, 200, out)
        self.assertIn("pkg/tools.py:", out["tools"][0]["source"])


@unittest.skipUnless(HAS_AXOR_WRAP, "axor-wrap not installed")
class TestWrapManifests(_Base):
    def _scanned_tools(self):
        status, out = self._scan([{"path": "agent.py", "content": LANGCHAIN_AGENT}])
        self.assertEqual(status, 200, out)
        return out["tools"]

    def test_classified_tools_become_valid_manifests_and_yaml(self) -> None:
        tools = self._scanned_tools()
        # the human reviewed the scan: guesses accepted, UNKNOWN classified READ
        reviewed = []
        for t in tools:
            cls = t["guess"]["default_class"]
            reviewed.append({
                **{k: t[k] for k in ("id", "source", "description", "args_schema",
                                     "framework", "schema_confidence")},
                "effect": {
                    "default_class": "READ" if cls == "UNKNOWN" else cls,
                    "driving_args": t["guess"]["driving_args"],
                    "untrusted_fields": t["guess"]["untrusted_fields"],
                    "sensitive_fields": [],
                },
            })
        status, out = self._req("POST", "/wrap/manifests", {"tools": reviewed}, token="ctl")
        self.assertEqual(status, 200, out)

        manifests = {m["id"]: m for m in out["manifests"]}
        self.assertEqual(set(manifests), {"search_web", "send_email", "frobnicate"})
        for manifest in manifests.values():
            self.assertEqual(manifest["schema_version"], "tool-manifest/v1")
            # each returned manifest validates against the embedded schema
            from axor_wrap import validate_manifest
            self.assertEqual(validate_manifest(manifest), [])
        self.assertEqual(manifests["send_email"]["effect"]["default_class"], "EXPORT")
        self.assertTrue(manifests["send_email"]["side_effecting"])
        self.assertFalse(manifests["search_web"]["side_effecting"])
        self.assertEqual(manifests["search_web"]["untrusted_fields"], ["result.*"])

        self.assertTrue(out["governance_yaml"].strip())
        self.assertIn("egress_sinks", out["governance_yaml"])
        self.assertIn("send_email", out["governance_yaml"])

        summary = out["wrap"]
        self.assertEqual(summary["tools"], 3)
        self.assertEqual(summary["egress_sinks"], ["send_email"])
        self.assertEqual(summary["untrusted_sources"], ["search_web"])
        self.assertEqual(summary["driving_args"]["send_email"], ["to"])

    def test_sensitive_fields_survive_into_the_manifest(self) -> None:
        status, out = self._req("POST", "/wrap/manifests", {"tools": [{
            "id": "read_txns", "args_schema": {"type": "object"},
            "effect": {"default_class": "READ", "driving_args": [],
                       "sensitive_fields": ["result.transactions[].amount"]},
        }]}, token="ctl")
        self.assertEqual(status, 200, out)
        self.assertEqual(out["manifests"][0]["sensitive_fields"],
                         ["result.transactions[].amount"])
        self.assertEqual(out["wrap"]["sensitive_sources"], ["read_txns"])

    def test_unknown_class_is_rejected(self) -> None:
        # UNKNOWN must never reach manifest building — classification is a
        # human decision, and this endpoint only accepts reviewed tools
        status, out = self._req("POST", "/wrap/manifests", {"tools": [{
            "id": "frobnicate",
            "effect": {"default_class": "UNKNOWN", "driving_args": []},
        }]}, token="ctl")
        self.assertEqual(status, 400, out)
        self.assertIn("UNKNOWN", out["error"])

    def test_missing_or_bad_effect_is_400(self) -> None:
        self.assertEqual(self._req("POST", "/wrap/manifests",
                                   {"tools": [{"id": "x"}]}, token="ctl")[0], 400)
        self.assertEqual(self._req("POST", "/wrap/manifests",
                                   {"tools": []}, token="ctl")[0], 400)
        status, _ = self._req("POST", "/wrap/manifests", {"tools": [{
            "id": "x", "effect": {"default_class": "EXPORT", "driving_args": "to"},
        }]}, token="ctl")
        self.assertEqual(status, 400)

    def test_manifests_require_the_control_token(self) -> None:
        status, _ = self._req("POST", "/wrap/manifests", {"tools": []}, token=None)
        self.assertEqual(status, 401)


class TestWrapEngineMissing(_Base):
    """Both endpoints answer an honest 501 when axor-wrap is not importable.

    The lazy import in wrap_api._engine resolves per request, so blocking the
    module in sys.modules (None entries make `import axor_wrap` raise
    ImportError) simulates a server without the extra installed.
    """

    def _block_axor_wrap(self) -> None:
        self._saved = {k: v for k, v in sys.modules.items()
                       if k == "axor_wrap" or k.startswith("axor_wrap.")}
        for k in self._saved:
            sys.modules[k] = None  # type: ignore[assignment]
        sys.modules.setdefault("axor_wrap", None)  # type: ignore[arg-type]

        def restore() -> None:
            for k in [k for k in sys.modules
                      if k == "axor_wrap" or k.startswith("axor_wrap.")]:
                del sys.modules[k]
            sys.modules.update(self._saved)
        self.addCleanup(restore)

    def test_wrap_endpoints_answer_501_with_an_install_hint(self) -> None:
        self._block_axor_wrap()
        status, out = self._scan([{"path": "a.py", "content": "x = 1"}])
        self.assertEqual(status, 501, out)
        self.assertEqual(out["error"], "axor-wrap is not installed")
        self.assertEqual(out["hint"], "pip install axor-wrap")
        status, out = self._req("POST", "/wrap/manifests", {"tools": [
            {"id": "x", "effect": {"default_class": "READ"}}]}, token="ctl")
        self.assertEqual(status, 501, out)
        self.assertEqual(out["error"], "axor-wrap is not installed")


if __name__ == "__main__":
    unittest.main()
