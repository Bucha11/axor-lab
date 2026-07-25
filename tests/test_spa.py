"""Serving the built web UI, and bridging it to the runtime API.

The two failures this guards against are the ones that made a `pip install`
look like an empty product: `/` answering with a sparse HTML index because the
build was never served, and a UI that renders but whose every run/compose/
replay call fails because `/jobs-api` went nowhere.

It also holds the line on the pages that must NOT become the app: `/catalog`
and `/e/{id}` stay server-rendered, because a publication is the citable,
no-JS, crawlable artifact.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lab_server import make_server, spa

INDEX = b"<!doctype html><title>Axor Lab</title><script src=/assets/app.js></script>"


def _dist(root: Path) -> Path:
    """A minimal build directory: an index and one asset."""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_bytes(INDEX)
    (dist / "assets" / "app.js").write_bytes(b"export const x = 1;\n")
    return dist


class TestFindDist(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_explicit_directory_with_an_index_is_the_build(self) -> None:
        dist = _dist(self.root)
        self.assertEqual(spa.find_dist(str(dist)), dist)

    def test_a_directory_without_an_index_is_not_a_build(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        self.assertIsNone(spa.find_dist(str(empty)))
        self.assertIsNone(spa.find_dist(str(self.root / "nope")))


class TestResolveAsset(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dist = _dist(self.root)

    def test_serves_a_built_asset_with_its_type(self) -> None:
        asset = spa.resolve_asset(self.dist, "/assets/app.js")
        assert asset is not None
        self.assertEqual(asset[0], b"export const x = 1;\n")
        self.assertEqual(asset[1], "text/javascript; charset=utf-8")

    def test_the_request_cannot_leave_the_build_directory(self) -> None:
        """A path is untrusted input, not a filesystem coordinate."""
        secret = self.root / "secret.json"
        secret.write_text('{"key": "hunter2"}')
        for path in ("/../secret.json", "/assets/../../secret.json",
                     "/assets/%2e%2e/../secret.json"):
            self.assertIsNone(spa.resolve_asset(self.dist, path), path)

    def test_a_symlink_out_of_the_build_is_not_followed(self) -> None:
        secret = self.root / "secret.json"
        secret.write_text('{"key": "hunter2"}')
        (self.dist / "leak.json").symlink_to(secret)
        self.assertIsNone(spa.resolve_asset(self.dist, "/leak.json"))

    def test_only_build_asset_types_are_served(self) -> None:
        (self.dist / "notes.txt").write_text("private")
        (self.dist / "server.py").write_text("print('x')")
        self.assertIsNone(spa.resolve_asset(self.dist, "/notes.txt"))
        self.assertIsNone(spa.resolve_asset(self.dist, "/server.py"))

    def test_missing_files_directories_and_the_root_are_not_assets(self) -> None:
        self.assertIsNone(spa.resolve_asset(self.dist, "/assets/gone.js"))
        self.assertIsNone(spa.resolve_asset(self.dist, "/assets/"))
        self.assertIsNone(spa.resolve_asset(self.dist, "/"))
        self.assertIsNone(spa.resolve_asset(self.dist, ""))


class _StubRuntime(BaseHTTPRequestHandler):
    """Stands in for the runtime API on its own port."""

    def log_message(self, *args: object) -> None:
        pass

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/boom":
            self._reply(409, {"error": "not deterministic"})
            return
        self._reply(200, {"path": self.path,
                          "auth": self.headers.get("Authorization")})

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length") or 0)
        self._reply(201, {"echo": json.loads(self.rfile.read(length) or b"{}")})


class _Serving:
    """Run an http server on an ephemeral port for the duration of a test."""

    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _get(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


class TestServedUI(unittest.TestCase):
    """The routing decisions, over real HTTP."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dist = _dist(self.root)
        runtime = _Serving(ThreadingHTTPServer(("127.0.0.1", 0), _StubRuntime))
        self.addCleanup(runtime.close)
        self.runtime = runtime

    def _lab(self, *, with_dist: bool = True, with_runtime: bool = True) -> str:
        server = make_server(
            self.root / "store", port=0,
            frontend_dist=str(self.dist) if with_dist else None,
            runtime_base=self.runtime.base if with_runtime else None,
        )
        serving = _Serving(server)
        self.addCleanup(serving.close)
        return serving.base

    def test_the_app_answers_the_root_and_its_assets(self) -> None:
        base = self._lab()
        status, body, content_type = _get(f"{base}/")
        self.assertEqual(status, 200)
        self.assertEqual(body, INDEX)
        self.assertIn("text/html", content_type)
        status, body, content_type = _get(f"{base}/assets/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"export const x = 1;\n")
        self.assertIn("text/javascript", content_type)

    def test_the_server_rendered_catalog_keeps_its_own_address(self) -> None:
        """`/catalog` is the no-JS index — the app at `/` must not shadow it."""
        status, body, _ = _get(f"{self._lab()}/catalog")
        self.assertEqual(status, 200)
        self.assertIn(b"Axor Lab", body)
        self.assertNotEqual(body, INDEX)

    def test_without_a_build_the_root_falls_back_to_the_catalog(self) -> None:
        status, body, _ = _get(f"{self._lab(with_dist=False)}/")
        self.assertEqual(status, 200)
        self.assertNotEqual(body, INDEX)
        self.assertIn(b"Axor Lab", body)

    def test_the_json_api_is_not_shadowed_by_the_app(self) -> None:
        status, body, _ = _get(f"{self._lab()}/api/publications")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"publications": []})

    def test_jobs_api_is_bridged_to_the_runtime_port(self) -> None:
        base = self._lab()
        status, body, _ = _get(f"{base}/jobs-api/catalog?suite=banking")
        self.assertEqual(status, 200)
        # the path arrives at the runtime API with the prefix stripped and the
        # query intact — a composer that loses its query returns the wrong suite
        self.assertEqual(json.loads(body)["path"], "/catalog?suite=banking")

    def test_the_runtime_status_code_survives_the_bridge(self) -> None:
        """A 409 that arrives as a 200 turns a refusal into a silent success."""
        status, body, _ = _get(f"{self._lab()}/jobs-api/boom")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "not deterministic"})

    def test_jobs_api_posts_are_bridged_with_their_body(self) -> None:
        request = urllib.request.Request(
            f"{self._lab()}/jobs-api/runs/local", data=b'{"repeats": 10}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 201)
            self.assertEqual(json.loads(response.read()), {"echo": {"repeats": 10}})

    def test_without_the_bridge_jobs_api_is_not_a_route(self) -> None:
        status, _, _ = _get(f"{self._lab(with_runtime=False)}/jobs-api/catalog")
        self.assertEqual(status, 404)

    def test_traversal_over_the_wire_reaches_nothing(self) -> None:
        """resolve_asset is unit-tested, but the wire is the real attack surface."""
        (self.root / "secret.json").write_text('{"key": "hunter2"}')
        base = self._lab()
        for path in ("/../secret.json", "/assets/../../secret.json",
                     "/%2e%2e/secret.json", "/assets/..%2f..%2fsecret.json"):
            status, body, _ = _get(base + path)
            self.assertNotIn(b"hunter2", body, path)
            self.assertNotEqual(status, 200, path)


class TestJobsProxy(unittest.TestCase):
    def test_an_unreachable_runtime_api_says_so_rather_than_a_bare_502(self) -> None:
        """`--no-runtime-api` is the usual cause; a bare 502 hides it."""
        with __import__("socket").socket() as sock:  # a port nothing is listening on
            sock.bind(("127.0.0.1", 0))
            dead = f"http://127.0.0.1:{sock.getsockname()[1]}"
        status, body, content_type = spa.JobsProxy(dead).forward(
            "GET", "/jobs-api/catalog", None, {},
        )
        self.assertEqual(status, 502)
        self.assertEqual(content_type, "application/json")
        self.assertIn("no-runtime-api", json.loads(body)["error"])

    def test_only_content_type_and_authorization_are_forwarded(self) -> None:
        runtime = _Serving(ThreadingHTTPServer(("127.0.0.1", 0), _StubRuntime))
        self.addCleanup(runtime.close)
        status, body, _ = spa.JobsProxy(runtime.base).forward(
            "GET", "/jobs-api/x", None,
            {"Authorization": "Bearer t", "Cookie": "session=secret",
             "Host": "example.invalid"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["auth"], "Bearer t")


if __name__ == "__main__":
    unittest.main()
