"""GET /version: which build is actually answering.

An operator who rolls a tag has had no way to confirm it took. `docker compose
ps` names the image the daemon was asked to run; it does not say which kernel
the process imported. This route answers from the installed distributions, so
it can disagree with the intended tag — which is the only reason to have it.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

import axor_core

from lab_server.runtime_jobs import make_runtime_server


def _serve(**kwargs) -> tuple[object, str]:  # noqa: ANN003
    server = make_runtime_server(port=0, **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _get(base: str, path: str, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(f"{base}{path}", method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class TestVersionSurface(unittest.TestCase):
    def test_reports_the_kernel_this_process_imported(self) -> None:
        """Not a constant: the number has to come from the live install.

        If these could disagree, the route is reporting something other than
        what the running process would actually govern with.
        """
        server, base = _serve(control_token=None)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        status, payload = _get(base, "/version")
        self.assertEqual(status, 200)
        self.assertEqual(payload["versions"]["axor-core"], axor_core.__version__)

    def test_every_package_is_named_even_when_it_is_not_installed(self) -> None:
        """The key is always there; the VALUE is what varies.

        An earlier version of this test asserted `axor-lab` is never null, and
        CI's real-kernel job disproved it: that job installs the kernel, wrap
        and eval and runs the suite straight out of the checkout, so Lab itself
        has no distribution metadata to read. Null there is correct.

        What a reader of this payload needs is the distinction between "this
        deployment does not have that package" and "this server has never heard
        of it" — so the key set is fixed and the value carries the answer.
        """
        server, base = _serve(control_token=None)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        _status, payload = _get(base, "/version")
        self.assertEqual(
            sorted(payload["versions"]),
            sorted(["axor-lab", "axor-core", "axor-wrap", "axor-eval"]),
        )

    def test_a_hosted_server_does_not_answer_anonymously(self) -> None:
        """/auth/status is open next door; this one is not.

        A version list is the set of advisories to try. The operator reading it
        is signed in already, so nothing is lost by gating it.
        """
        server, base = _serve(control_token="ctl")
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        status, _payload = _get(base, "/version")
        self.assertEqual(status, 401)
        status, payload = _get(base, "/version", token="ctl")
        self.assertEqual(status, 200)
        self.assertEqual(payload["versions"]["axor-core"], axor_core.__version__)

    def test_an_uninstalled_package_is_null_not_an_error(self) -> None:
        """Lab runs from a checkout as well as from a wheel."""
        from lab_server.runtime_jobs import _installed_version

        self.assertIsNone(_installed_version("axor-not-a-real-distribution"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
