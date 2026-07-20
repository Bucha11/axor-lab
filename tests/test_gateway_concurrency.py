"""Endpoint gateway concurrency + body handling (review round 3, Patch 9).

The gateway is a ThreadingHTTPServer, so its per-run state must be guarded:
concurrent run creation must mint unique ids, concurrent events must get unique
monotonic seqs (no two events sharing seq=4), the trace must not be readable
mid-write, and a hostile Content-Length must not hang or silently truncate.
"""

from __future__ import annotations

import json
import socket
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from tests import support
from lab_endpoint import make_gateway


class _GatewayHarness:
    def __init__(self) -> None:
        self.server = make_gateway(
            support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], scenario_id="banking-exfil-01",
        )
        self.host, self.port = self.server.server_address[0], self.server.server_address[1]
        self.base = f"http://{self.host}:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def post(self, path: str, obj: dict, secret: str | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get(self, path: str, secret: str) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {secret}"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def raw(self, request_bytes: bytes) -> bytes:
        s = socket.create_connection((self.host, self.port), timeout=5)
        try:
            s.sendall(request_bytes)
            s.settimeout(5)
            chunks = []
            while True:
                try:
                    b = s.recv(4096)
                except socket.timeout:
                    break
                if not b:
                    break
                chunks.append(b)
                if b"\r\n\r\n" in b"".join(chunks):
                    break
            return b"".join(chunks)
        finally:
            s.close()


class TestGatewayConcurrency(unittest.TestCase):
    def setUp(self) -> None:
        self.gw = _GatewayHarness()
        self.addCleanup(self.gw.close)

    def test_concurrent_run_creation_has_unique_ids(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.gw.post("/runs", {}), range(40)))
        ids = [body["run_id"] for status, body in results if status == 201]
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40)  # no collisions under contention

    def test_concurrent_events_get_unique_monotonic_seq(self) -> None:
        _, opened = self.gw.post("/runs", {})
        run_id, secret = opened["run_id"], opened["run_secret"]

        def emit(i: int) -> None:
            self.gw.post(f"/runs/{run_id}/events", {
                "type": "tool_result", "tool": "read_txns",
                "values": [{"value_id": f"v{i}", "preview": "x", "decision_value": "x",
                            "labels": ["trusted"],
                            "sources": [{"kind": "external_read", "origin_ref": f"o{i}"}]}],
            }, secret=secret)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(emit, range(30)))
        self.gw.post(f"/runs/{run_id}/finalize", {}, secret=secret)
        _, trace = self.gw.get(f"/runs/{run_id}/trace", secret)
        seqs = [e["seq"] for e in trace["events"]]
        self.assertEqual(len(seqs), 30)
        self.assertEqual(sorted(seqs), list(range(30)))  # unique + gapless, no dup seq

    def test_trace_cannot_be_read_before_finalize(self) -> None:
        _, opened = self.gw.post("/runs", {})
        run_id, secret = opened["run_id"], opened["run_secret"]
        status, body = self.gw.get(f"/runs/{run_id}/trace", secret)
        self.assertEqual(status, 409)

    def test_expected_seq_mismatch_is_409(self) -> None:
        _, opened = self.gw.post("/runs", {})
        run_id, secret = opened["run_id"], opened["run_secret"]
        status, _ = self.gw.post(f"/runs/{run_id}/events", {
            "type": "tool_result", "tool": "read_txns", "expected_seq": 5,
            "values": [{"value_id": "v", "preview": "x", "labels": ["clean"],
                        "sources": [{"kind": "external_read", "origin_ref": "o"}]}],
        }, secret=secret)
        self.assertEqual(status, 409)

    def test_negative_content_length_is_rejected_not_hung(self) -> None:
        _, opened = self.gw.post("/runs", {})
        run_id, secret = opened["run_id"], opened["run_secret"]
        req = (
            f"POST /runs/{run_id}/events HTTP/1.1\r\n"
            f"Host: {self.gw.host}\r\n"
            f"Authorization: Bearer {secret}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: -1\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        resp = self.gw.raw(req)
        self.assertTrue(resp, "server hung / no response to negative Content-Length")
        self.assertIn(b"400", resp.split(b"\r\n", 1)[0])

    def test_oversized_body_returns_413(self) -> None:
        _, opened = self.gw.post("/runs", {})
        run_id, secret = opened["run_id"], opened["run_secret"]
        huge = 9 * 1024 * 1024  # > 8 MiB cap
        req = (
            f"POST /runs/{run_id}/events HTTP/1.1\r\n"
            f"Host: {self.gw.host}\r\n"
            f"Authorization: Bearer {secret}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {huge}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        resp = self.gw.raw(req)
        self.assertIn(b"413", resp.split(b"\r\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
