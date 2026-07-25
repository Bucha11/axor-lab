"""`python -m lab_server --runtime-port` — the runtime-jobs API beside the catalog.

Runs the real module entrypoint as a subprocess and checks: with the flag, one
process serves BOTH the catalog and the runtime-jobs control surface (the
latter gated by `--control-token` / AXOR_LAB_CONTROL_TOKEN); without the flag,
only the catalog line is printed and no runtime server exists.
"""

from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_URL_RE = re.compile(r"http://127\.0\.0\.1:(\d+)")
_LINE_TIMEOUT = 30.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestMainRuntimePort(unittest.TestCase):
    def _spawn(self, extra: list[str], env_extra: dict[str, str] | None = None) -> queue.Queue[str]:
        """Start `python -m lab_server` and return a queue of its stdout lines."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {**os.environ, **(env_extra or {})}
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "lab_server",
             "--root", tmp.name, "--port", "0", *extra],
            cwd=REPO_ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.wait)
        self.addCleanup(process.terminate)
        lines: queue.Queue[str] = queue.Queue()

        def _pump() -> None:
            for line in process.stdout:  # type: ignore[union-attr]
                lines.put(line)

        threading.Thread(target=_pump, daemon=True).start()
        return lines

    def _line(self, lines: queue.Queue[str], timeout: float = _LINE_TIMEOUT) -> str:
        return lines.get(timeout=timeout)

    def _line_containing(
        self, lines: queue.Queue[str], needle: str, timeout: float = _LINE_TIMEOUT,
    ) -> str:
        """Wait for the startup line that says `needle`.

        Reading by POSITION made these tests break whenever startup gained a line
        — as it did when the server started reporting which UI it serves. What
        each test cares about is that a particular line appears, not that it is
        the second one.
        """
        deadline = time.monotonic() + timeout
        seen: list[str] = []
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            seen.append(line)
            if needle in line:
                return line
        raise AssertionError(f"never saw a startup line containing {needle!r}; saw {seen}")

    def _get(self, url: str, token: str | None = None) -> tuple[int, str]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_runtime_port_serves_the_jobs_api_beside_the_catalog(self) -> None:
        runtime_port = _free_port()
        lines = self._spawn(["--runtime-port", str(runtime_port), "--control-token", "ctl"])

        catalog_line = self._line_containing(lines, "axor-lab server on")
        catalog_match = _URL_RE.search(catalog_line)
        assert catalog_match is not None, catalog_line
        catalog_port = int(catalog_match.group(1))

        runtime_line = self._line_containing(lines, "axor-lab runtime-jobs on")
        self.assertIn(f":{runtime_port}", runtime_line)
        self.assertIn("token-gated", runtime_line)

        # the catalog answers on its port …
        status, body = self._get(f"http://127.0.0.1:{catalog_port}/api/publications")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"publications": []})
        # … and the runtime-jobs control surface answers on its own, token-gated
        status, _ = self._get(f"http://127.0.0.1:{runtime_port}/runtimes")
        self.assertEqual(status, 401)
        status, body = self._get(f"http://127.0.0.1:{runtime_port}/runtimes", token="ctl")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"runtimes": []})

    def test_control_token_falls_back_to_the_environment(self) -> None:
        runtime_port = _free_port()
        lines = self._spawn(
            ["--runtime-port", str(runtime_port)],
            env_extra={"AXOR_LAB_CONTROL_TOKEN": "envtok"},
        )
        runtime_line = self._line_containing(lines, "axor-lab runtime-jobs on")
        self.assertIn("token-gated", runtime_line)
        status, _ = self._get(f"http://127.0.0.1:{runtime_port}/runtimes", token="wrong")
        self.assertEqual(status, 401)
        status, _ = self._get(f"http://127.0.0.1:{runtime_port}/runtimes", token="envtok")
        self.assertEqual(status, 200)

    def test_runtime_server_is_on_by_default(self) -> None:
        """The default start command must bring up the WHOLE product.

        This assertion is inverted from what it once was: the runtime API used to
        default to off, so the documented start command served the catalog and
        left the builder, runs, results and agent ingest dark against a server
        that looked healthy. Half a product behind an undocumented flag is worse
        than a missing flag.
        """
        lines = self._spawn([])
        self.assertIn("axor-lab server on", self._line(lines))
        runtime_line = self._line_containing(lines, "axor-lab runtime-jobs on")
        runtime_match = _URL_RE.search(runtime_line)
        assert runtime_match is not None, runtime_line
        status, body = self._get(f"http://127.0.0.1:{int(runtime_match.group(1))}/runtimes")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"runtimes": []})

    def test_no_runtime_api_opts_out(self) -> None:
        lines = self._spawn(["--no-runtime-api"])
        self._line_containing(lines, "axor-lab server on")
        # the opt-out says what it costs, then serves the catalog alone
        self._line_containing(lines, "runtime-jobs API disabled")
        with self.assertRaises(queue.Empty):
            lines.get(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
