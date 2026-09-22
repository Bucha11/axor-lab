"""`serve`'s deploy wiring: the axor-identity JWKS options, and --web-root.

Written as TestCases. These assertions used to be module-level `def test_…`
functions, which `python -m unittest discover` — the command this repository
runs — does not collect: it loads TestCase subclasses and nothing else. They
had never executed.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from lab_runner import cli
from lab_runner.cli import _build_parser


class TestServeFlags(unittest.TestCase):
    def test_serve_accepts_identity_flags(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["serve", "--identity-jwks-url", "http://id.example/jwks",
             "--identity-issuer", "acme-identity"])
        self.assertEqual(args.identity_jwks_url, "http://id.example/jwks")
        self.assertEqual(args.identity_issuer, "acme-identity")

        file_args = parser.parse_args(
            ["serve", "--identity-jwks-file", "/etc/jwks.json"])
        self.assertEqual(file_args.identity_jwks_file, "/etc/jwks.json")

    def test_identity_flags_default_off(self) -> None:
        args = _build_parser().parse_args(["serve"])
        self.assertIsNone(args.identity_jwks_url)
        self.assertIsNone(args.identity_jwks_file)
        self.assertIsNone(args.identity_issuer)

    def test_serve_accepts_a_database_url(self) -> None:
        args = _build_parser().parse_args(
            ["serve", "--database-url", "postgresql://h/db"])
        self.assertEqual(args.database_url, "postgresql://h/db")

    def test_database_url_defaults_off(self) -> None:
        """No DSN means the file/in-memory backend — the CLI and a laptop
        deployment must keep working with no database installed."""
        self.assertIsNone(_build_parser().parse_args(["serve"]).database_url)

    def test_serve_accepts_a_web_root(self) -> None:
        args = _build_parser().parse_args(["serve", "--web-root", "/srv/app"])
        self.assertEqual(args.web_root, "/srv/app")

    def test_web_root_defaults_off(self) -> None:
        self.assertIsNone(_build_parser().parse_args(["serve"]).web_root)


class TestServeFindsTheWebApp(unittest.TestCase):
    """`serve` had no way to say where the built app is.

    `static.default_root()` resolves `lab_server/../../web/dist`, which exists
    only in a source checkout. A normal install puts `lab_server` in
    site-packages, where that path is a directory that is not there — so a
    container built the obvious way served the API with no UI and no error.
    The image worked around it with an editable install; `--web-root` removes
    the need to.
    """

    def _args(self, **over: object) -> argparse.Namespace:
        base: dict[str, object] = dict(
            host="127.0.0.1", port=0, control_token="t", data_dir=None,
            database_url=None, web_root=None, billing_webhook_secret=None,
            plans_file=None,
            identity_jwks_url=None, identity_jwks_file=None,
            identity_issuer=None, guest_sessions=False)
        base.update(over)
        return argparse.Namespace(**base)

    def test_an_explicit_root_with_no_app_is_a_validation_error(self) -> None:
        """Loud, not silent: an operator who passed a path asked for the UI to
        be there, and a deployment that quietly serves an API instead is the
        failure this flag exists to prevent."""
        with tempfile.TemporaryDirectory() as empty:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = cli._cmd_serve(self._args(web_root=empty))
        self.assertEqual(code, cli.EXIT_VALIDATION)
        self.assertIn("holds no index.html", err.getvalue())

    def test_the_env_var_is_read_when_the_flag_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict(os.environ, {"AXOR_LAB_WEB_ROOT": empty}):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    code = cli._cmd_serve(self._args())
        self.assertEqual(code, cli.EXIT_VALIDATION)
        self.assertIn(empty, err.getvalue())

    def test_the_flag_wins_over_the_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as flag_dir, \
                tempfile.TemporaryDirectory() as env_dir:
            with mock.patch.dict(os.environ, {"AXOR_LAB_WEB_ROOT": env_dir}):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    cli._cmd_serve(self._args(web_root=flag_dir))
        message = err.getvalue()
        self.assertIn(flag_dir, message)
        self.assertNotIn(env_dir, message)

    def test_a_good_root_reaches_the_server_and_is_reported(self) -> None:
        """The root must be PASSED to the server, not merely printed. The bug
        this guards is a startup line claiming the app is served while the
        handler looks somewhere else."""
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "index.html").write_text("<!doctype html>")
            captured: dict[str, object] = {}

            class _Stub:
                def serve_forever(self) -> None:
                    raise KeyboardInterrupt

                def shutdown(self) -> None:
                    pass

            def _fake(**kwargs: object) -> _Stub:
                captured.update(kwargs)
                return _Stub()

            with mock.patch("lab_server.runtime_jobs.make_runtime_server", _fake):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    code = cli._cmd_serve(self._args(web_root=root))

        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(captured["web_root"], pathlib.Path(root).resolve())
        self.assertIn(root, out.getvalue())


if __name__ == "__main__":
    unittest.main()
