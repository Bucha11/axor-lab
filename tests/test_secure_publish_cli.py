"""Secure hosted publish via the CLI (review round 6, Patch 20).

A token-protected server rejected the CLI publish because the CLI sent no
Authorization header. The CLI now reads the write token from an ENV VAR (never a
CLI arg → not in the process list / shell history) and sends the bearer header,
so a secured server is reachable with the shipped CLI.
"""

from __future__ import annotations

import argparse
import tempfile
import threading
import unittest
from pathlib import Path

from tests import support
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_runner.cli import _publish_to_server
from lab_server import make_server

CREATED = "2026-07-19T12:00:00+00:00"


def _bundle_and_traces():
    scenario = support.banking_scenario()
    result = run_experiment_suite(
        [scenario], support.manifests(), support.conditions(), support.kernel_registry(),
        repeats=6, run_id="r_sec",
    )
    bundle = build_bundle(
        bundle_id="b_sec", created=CREATED, scenarios=[scenario],
        conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
        environment=support.environment(), trials=result.trials, aggregates=[],
        traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


def _args(server: str, **kw) -> argparse.Namespace:
    base = {
        "server": server, "question": "does governance stop exfil?", "license": "CC-BY-4.0",
        "visibility": "unlisted", "token_env": None, "author": None, "signature_file": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestSecurePublishCli(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = make_server(
            Path(self.tmp.name) / "store", host="127.0.0.1", port=0, write_token="s3cret",
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.bundle, self.traces = _bundle_and_traces()

    def test_publish_without_token_is_rejected(self) -> None:
        rc = _publish_to_server(_args(self.base), self.bundle, self.traces)
        self.assertNotEqual(rc, 0)  # 401 from the token-protected server

    def test_publish_with_token_from_env_succeeds(self) -> None:
        import os
        os.environ["AXOR_LAB_TOKEN_TEST"] = "s3cret"
        self.addCleanup(lambda: os.environ.pop("AXOR_LAB_TOKEN_TEST", None))
        rc = _publish_to_server(
            _args(self.base, token_env="AXOR_LAB_TOKEN_TEST"), self.bundle, self.traces
        )
        self.assertEqual(rc, 0)

    def test_missing_env_var_is_a_clean_error(self) -> None:
        from lab_runner.errors import RunnerError
        with self.assertRaises(RunnerError):
            _publish_to_server(
                _args(self.base, token_env="DEFINITELY_NOT_SET_ENV"), self.bundle, self.traces
            )


if __name__ == "__main__":
    unittest.main()
