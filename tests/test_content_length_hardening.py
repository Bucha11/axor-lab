"""The publication server rejects a bad Content-Length cleanly (review r8, P2).

_read_json did `int(self.headers.get("Content-Length", "0"))` with no guard: a
non-numeric value raised an uncaught ValueError (500), and a negative value
slipped past the size cap and made rfile.read(-1) block reading to EOF. The
gateway already handled this; the publication server now does too.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from lab_server import make_server


class TestContentLengthHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server(Path(cls.tmp.name) / "store", host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _raw_post(self, content_length: str) -> str:
        # send a hand-built request with an attacker-chosen Content-Length; a
        # short timeout means a hang (the old read(-1) path) fails the test
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(5)
            body = b"{}"
            request = (
                f"POST /api/publications HTTP/1.1\r\n"
                f"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
                f"Content-Length: {content_length}\r\nConnection: close\r\n\r\n"
            ).encode() + body
            sock.sendall(request)
            return sock.recv(4096).decode("latin-1", "replace")

    def test_negative_content_length_is_400_not_a_hang(self) -> None:
        status_line = self._raw_post("-1").splitlines()[0]
        self.assertIn("400", status_line)

    def test_non_numeric_content_length_is_400_not_500(self) -> None:
        status_line = self._raw_post("notanumber").splitlines()[0]
        self.assertIn("400", status_line)


if __name__ == "__main__":
    unittest.main()
