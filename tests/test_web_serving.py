"""The built app is served by the same process as the screen API.

`axor-lab serve` should give a working product, not an API plus instructions for
building a frontend somewhere else. What that costs is a static file handler in
a server that had none, and the two things a static handler gets wrong:

  - **Path traversal.** Filtering `..` out of a request path is a string check,
    and a symlink, a percent-encoded separator or an absolute path all walk past
    it. The containment check is on the RESOLVED path.
  - **A catch-all SPA fallback.** Serving `index.html` for anything that is not
    a file means a build that lost a chunk answers a `.js` request with HTML,
    and the browser reports a syntax error in a file that was never JavaScript.
    The app routes on the hash, so `/` is the only document path a browser ever
    requests and there is nothing to fall back FOR.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from lab_server.runtime_jobs import RuntimeJobStore, make_runtime_server
from lab_server.static import content_type, default_root, is_app_route, resolve

CONTROL = "control-token-for-tests"


class TestResolutionIsContained(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "dist"
        (self.root / "assets").mkdir(parents=True)
        (self.root / "index.html").write_text("<!doctype html>")
        (self.root / "assets" / "app.js").write_text("export {}")
        self.secret = Path(self.tmp.name) / "secret.txt"
        self.secret.write_text("not for the web")

    def test_a_normal_asset_resolves(self) -> None:
        self.assertEqual(resolve(self.root, "/assets/app.js"), self.root / "assets" / "app.js")

    def test_the_root_resolves_to_index(self) -> None:
        self.assertEqual(resolve(self.root, "/"), self.root / "index.html")

    def test_a_dotdot_escape_is_refused(self) -> None:
        self.assertIsNone(resolve(self.root, "/../secret.txt"))
        self.assertIsNone(resolve(self.root, "/assets/../../secret.txt"))

    def test_an_absolute_path_is_refused(self) -> None:
        self.assertIsNone(resolve(self.root, "//etc/passwd"))

    def test_a_symlink_pointing_outside_is_refused(self) -> None:
        """The case a textual `..` filter cannot see: the path contains no
        traversal at all."""
        link = self.root / "leak.txt"
        link.symlink_to(self.secret)
        self.assertTrue(link.is_file(), "the symlink itself resolves to a file")
        self.assertIsNone(resolve(self.root, "/leak.txt"))

    def test_a_query_string_does_not_become_part_of_the_name(self) -> None:
        self.assertEqual(
            resolve(self.root, "/assets/app.js?v=abc123"), self.root / "assets" / "app.js",
        )

    def test_a_missing_file_is_none_not_an_exception(self) -> None:
        self.assertIsNone(resolve(self.root, "/assets/gone.js"))


class TestContentTypes(unittest.TestCase):
    def test_text_carries_a_charset(self) -> None:
        """A bare `text/*` renders mojibake for any non-ASCII byte the app
        legitimately contains."""
        self.assertIn("charset=utf-8", content_type(Path("index.html")))
        self.assertIn("charset=utf-8", content_type(Path("app.css")))

    def test_javascript_is_named_so_the_browser_will_execute_it(self) -> None:
        self.assertIn("javascript", content_type(Path("app.js")))

    def test_an_unknown_extension_is_a_byte_stream_not_html(self) -> None:
        self.assertEqual(content_type(Path("x.unknown")), "application/octet-stream")


class TestAppRoutes(unittest.TestCase):
    def test_only_the_document_paths_are_app_routes(self) -> None:
        self.assertTrue(is_app_route("/"))
        self.assertTrue(is_app_route("/index.html"))

    def test_an_api_path_is_not_an_app_route(self) -> None:
        """`/runs`, `/suites` and `/home` were listed as app routes once. They
        are API routes that answer first, and the browser never requests them —
        it navigates on the hash."""
        for path in ("/runs", "/suites", "/home", "/evidence", "/artifacts"):
            with self.subTest(path=path):
                self.assertFalse(is_app_route(path))


class TestServedOverHttp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "assets").mkdir()
        (self.root / "index.html").write_text("<!doctype html><div id=root></div>")
        (self.root / "assets" / "app.js").write_text("export const ok = 1")
        self.server = make_runtime_server(
            port=0, control_token=CONTROL, store=RuntimeJobStore(), web_root=self.root,
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path: str, token: str | None = None) -> tuple[int, str, bytes]:
        request = urllib.request.Request(f"{self.base}{path}")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                return response.status, response.headers.get("Content-Type", ""), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()

    def test_the_app_is_served_without_a_token(self) -> None:
        """Gating the static files would mean the screen that collects the
        token needs the token."""
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"id=root", body)

    def test_assets_are_served(self) -> None:
        status, ctype, _ = self.get("/assets/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)

    def test_an_api_route_still_wins_over_the_static_handler(self) -> None:
        """`/suites` is both an API route and a name a file could have. The API
        answers, and it still requires the token."""
        self.assertEqual(self.get("/suites")[0], 401)
        status, ctype, _ = self.get("/suites", token=CONTROL)
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)

    def test_a_missing_asset_404s_rather_than_returning_the_app(self) -> None:
        status, ctype, _ = self.get("/assets/does-not-exist.js")
        self.assertEqual(status, 404)
        self.assertIn("application/json", ctype)

    def test_traversal_over_the_wire_is_refused(self) -> None:
        for path in ("/../pyproject.toml", "/%2e%2e/pyproject.toml"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)

    def test_a_server_with_no_build_still_serves_the_api(self) -> None:
        """The frontend is optional. A server without one answers every API
        route and simply has no browser surface — it never pretends otherwise."""
        server = make_runtime_server(
            port=0, control_token=CONTROL, store=RuntimeJobStore(),
            web_root=Path(self.tmp.name) / "nothing-here",
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://{server.server_address[0]}:{server.server_address[1]}"
        request = urllib.request.Request(f"{base}/suites")
        request.add_header("Authorization", f"Bearer {CONTROL}")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            self.assertEqual(json.loads(response.read())["suites"][0]["available"], True)
        try:
            urllib.request.urlopen(f"{base}/", timeout=10)  # noqa: S310
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)
        else:
            self.fail("a server with no build served something at /")


class TestTheRepoBuildIsWhatGetsServed(unittest.TestCase):
    def test_default_root_points_at_the_built_app_when_it_exists(self) -> None:
        """Skipped rather than failed when `web/` has not been built: the
        Python package must stay installable and testable without Node."""
        root = default_root()
        if root is None:
            self.skipTest("web/dist is not built (npm --prefix web run build)")
        self.assertTrue((root / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
