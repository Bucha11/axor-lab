"""Safe visibility defaults + crypto-absent handling (review round 4, Patch 15).

- An upload with no visibility must default to unlisted, not public — a single
  publish command should never silently make an artifact world-listed.
- A signed publish on a server without PyNaCl must be a clean PublishRejected,
  not an unhandled SignatureUnavailable that 500s the request handler.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_server.errors import PublishRejected
from lab_server.store import PublicationStore

CREATED = "2026-07-19T12:00:00+00:00"
_HAS_NACL = importlib.util.find_spec("nacl") is not None


def _bundle() -> tuple[dict, dict]:
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=6, run_id="r_vis",
    )
    bundle = build_bundle(
        bundle_id="b_vis", created=CREATED, scenarios=[scenario],
        conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials, aggregates=[],
        traces=result.traces,
    )
    return bundle, result.traces


class TestVisibilityDefault(unittest.TestCase):
    def test_upload_without_visibility_defaults_unlisted(self) -> None:
        bundle, traces = _bundle()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            stored = store.publish(bundle, traces, question="q")  # no visibility given
            self.assertEqual(stored.publication["visibility"], "unlisted")
            # unlisted is capability-URL reachable but NEVER in the public catalog
            self.assertNotIn(stored, store.catalog())

    def test_public_requires_explicit_request(self) -> None:
        bundle, traces = _bundle()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp))
            stored = store.publish(bundle, traces, question="q", visibility="public")
            self.assertEqual(stored.publication["visibility"], "public")
            self.assertIn(stored, store.catalog())


class TestSignedPublishWithoutCrypto(unittest.TestCase):
    @unittest.skipIf(_HAS_NACL, "PyNaCl present — this tests the absent path")
    def test_signed_publish_without_pynacl_is_clean_rejection(self) -> None:
        bundle, traces = _bundle()
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicationStore(root=Path(tmp), known_keys={"acme": "00" * 32})
            with self.assertRaises(PublishRejected) as ctx:
                store.publish(bundle, traces, question="q",
                              signature="deadbeef", author="acme")
            self.assertIn("signature", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
