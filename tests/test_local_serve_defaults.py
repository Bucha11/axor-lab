"""`pip install axor-lab && axor-lab serve` is the local quickstart.

Two things made it not one: the wheel carried no web app, so a normal install
served the API alone; and storage defaulted to memory, so everything a new user
made was gone the next time they started Lab.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_runner import cli
from lab_server import static


def _args(**over: object) -> argparse.Namespace:
    base: dict[str, object] = dict(
        host="127.0.0.1", port=0, control_token=None, data_dir=None,
        database_url=None, web_root=None, billing_webhook_secret=None,
        plans_file=None, identity_jwks_url=None, identity_jwks_file=None,
        identity_issuer=None, guest_sessions=False, in_memory=False)
    base.update(over)
    return argparse.Namespace(**base)


class _Stop:
    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def shutdown(self) -> None:
        pass


def _serve_and_capture(args: argparse.Namespace) -> dict:
    seen: dict = {}

    def fake(**kwargs: object) -> _Stop:
        seen.update(kwargs)
        return _Stop()

    with mock.patch("lab_server.runtime_jobs.make_runtime_server", fake), \
            mock.patch.dict(os.environ, {}, clear=False), \
            contextlib.redirect_stdout(io.StringIO()):
        os.environ.pop("AXOR_LAB_DATA_DIR", None)
        os.environ.pop("AXOR_LAB_DATABASE_URL", None)
        cli._cmd_serve(args)
    return seen


class TestStorageDefault(unittest.TestCase):
    def test_a_local_run_keeps_its_work_in_the_home_directory(self) -> None:
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(Path, "home", return_value=Path(home)):
            seen = _serve_and_capture(_args())
        self.assertEqual(seen["data_dir"], str(Path(home) / ".axor-lab"))

    def test_in_memory_is_still_available_for_a_throwaway_run(self) -> None:
        seen = _serve_and_capture(_args(in_memory=True))
        self.assertIsNone(seen["data_dir"])

    def test_an_explicit_data_dir_wins(self) -> None:
        seen = _serve_and_capture(_args(data_dir="/srv/lab"))
        self.assertEqual(seen["data_dir"], "/srv/lab")

    def test_the_flag_parses(self) -> None:
        args = cli._build_parser().parse_args(["serve", "--in-memory"])
        self.assertTrue(args.in_memory)
        self.assertFalse(cli._build_parser().parse_args(["serve"]).in_memory)


class TestTheWheelCarriesTheApp(unittest.TestCase):
    def test_the_packaged_app_is_preferred_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packaged = Path(tmp)
            (packaged / "index.html").write_text("<!doctype html>")
            with mock.patch.object(static, "PACKAGED_ROOT", packaged):
                self.assertEqual(static.default_root(), packaged)

    def test_an_empty_packaged_dir_falls_back_to_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(static, "PACKAGED_ROOT", Path(tmp)):
            root = static.default_root()
        self.assertNotEqual(root, Path(tmp))

    def test_the_package_data_declares_it(self) -> None:
        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        self.assertIn('lab_server = ["_web/*", "_web/assets/*"]', text)
        manifest = (Path(__file__).resolve().parent.parent / "MANIFEST.in").read_text()
        self.assertIn("recursive-include lab_server/_web *", manifest)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
