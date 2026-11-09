"""The `serve` banner reaches a log file while the server is running.

Redirected to a file, stdout is block-buffered: the banner saying which URL,
where the data lives and whether auth is on stayed in the buffer for as long as
the server ran. CI's wheel job found it — its own log of `serve` was empty.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class TestBannerIsFlushed(unittest.TestCase):
    def test_a_redirected_banner_is_written_before_the_server_stops(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "serve.log"
            env["HOME"] = tmp
            with log.open("w") as out:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "lab_runner.cli", "serve", "--port", "0",
                     "--in-memory"],
                    stdout=out, stderr=subprocess.STDOUT, env=env)
            try:
                deadline = time.time() + 20
                while time.time() < deadline and "axor-lab on" not in log.read_text():
                    time.sleep(0.2)
                self.assertIn("axor-lab on http://", log.read_text())
                self.assertIsNone(proc.poll(), "the server should still be running")
            finally:
                proc.terminate()
                proc.wait(timeout=10)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
