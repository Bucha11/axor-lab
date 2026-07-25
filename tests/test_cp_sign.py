"""The CP export tree, signed by vault custody — Lab never holds a key.

The last capability that lived only in the CLI, and the one where "move it to the
server" was wrong twice: a Lab server must not hold your signing key, and a
browser is a worse place for it than a terminal — a private key that vouches for
a production config does not belong in a web form.

The Control Plane already solved this class of problem. Its signing vault SIGNS
and never surrenders: `sign(operator, key_id, payload)` returns a signature over
bytes you submit, the private half never leaves, and every request is authorised
and audited. An export signature says "this named author vouches for this
production config" — an operator action, and CP already treats operator actions
that way.

The load-bearing assertion here is the byte one: what Lab hands the vault must be
EXACTLY what `lab_contracts.signing.sign_bundle` would sign locally. If those
differ, a vault signature verifies nowhere and the whole path is theatre.

The stub vault below signs with a digest rather than Ed25519 — PyNaCl is not
installed in every environment, and what needs proving here is the plumbing and
the payload, not that Ed25519 works.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lab_contracts import canonical_json
from lab_server import cp_sign, local_run


class _StubVault:
    """Stands in for POST /v1/vault/signing/sign, and records what it was asked."""

    def __init__(self, status: int = 200) -> None:
        self.seen: dict[str, object] = {}
        self.calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                outer.calls += 1
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                outer.seen = {
                    "path": self.path,
                    "payload": base64.b64decode(body["payload_b64"]),
                    "operator": body.get("operator"),
                    "key_id": body.get("key_id"),
                    "token": self.headers.get("X-Vault-Signing-Token"),
                }
                if status != 200:
                    message = json.dumps({"detail": "not authorised"}).encode()
                    self.send_response(status)
                    self.send_header("Content-Length", str(len(message)))
                    self.end_headers()
                    self.wfile.write(message)
                    return
                signature = hashlib.sha256(outer.seen["payload"]).hexdigest()  # type: ignore[arg-type]
                out = json.dumps({"signature_hex": signature, "key_id": body.get("key_id")}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        executed = local_run.run_local(local_run.load_example())
        cls.bundle = executed["bundle"]
        cls.traces = executed["traces"]


class UnsignedExportTest(_Base):
    def test_no_vault_yields_an_export_that_says_it_is_unsigned(self) -> None:
        # The degradation has to be loud. An export that quietly claims an
        # authority it does not have is the failure this subsystem exists to stop.
        export = cp_sign.build_export(self.bundle, self.traces)
        self.assertFalse(export["signed"])
        self.assertIn("UNSIGNED", export["signature_note"])
        self.assertNotIn("signature", export["manifest"])

    def test_the_tree_is_complete_and_manifest_bound(self) -> None:
        export = cp_sign.build_export(self.bundle, self.traces)
        files = export["files"]
        self.assertIn("cp-deploy.json", files)
        self.assertIn("production-todo.md", files)
        self.assertIn("manifest.json", files)
        self.assertIn("source-bundle/bundle.json", files)
        # the manifest binds every file EXCEPT itself
        bound = set(export["manifest"]["files"])
        self.assertEqual(bound, set(files) - {"manifest.json"})


class VaultSignedExportTest(_Base):
    def setUp(self) -> None:
        self.vault = _StubVault()
        self.addCleanup(self.vault.close)

    def _signed(self) -> dict:
        return cp_sign.build_export(self.bundle, self.traces, signing={
            "cp_url": self.vault.url, "operator": "op_dmitrii",
            "key_id": "prod-1", "token": "vault-token",
        })

    def test_the_bytes_signed_are_exactly_sign_bundles_bytes(self) -> None:
        """The one that matters.

        `sign_bundle` signs the manifest MINUS its `signature`, canonicalized. If
        Lab hands the vault anything else, the resulting signature verifies
        nowhere — and an export would carry a signature that fails every check.
        """
        export = self._signed()
        manifest = export["manifest"]
        expected = canonical_json(
            {k: v for k, v in manifest.items() if k != "signature"}
        ).encode("utf-8")
        self.assertEqual(self.vault.seen["payload"], expected)

    def test_the_author_is_inside_what_gets_signed(self) -> None:
        # The signature must cover WHO signed it; otherwise the author line is
        # swappable without breaking anything.
        export = self._signed()
        self.assertEqual(export["manifest"]["author"], "op_dmitrii")
        self.assertIn(b"op_dmitrii", self.vault.seen["payload"])  # type: ignore[arg-type]

    def test_it_reaches_the_vault_with_the_operator_and_key(self) -> None:
        self._signed()
        self.assertEqual(self.vault.seen["path"], "/v1/vault/signing/sign")
        self.assertEqual(self.vault.seen["operator"], "op_dmitrii")
        self.assertEqual(self.vault.seen["key_id"], "prod-1")
        self.assertEqual(self.vault.seen["token"], "vault-token")

    def test_the_signature_lands_in_the_written_manifest(self) -> None:
        export = self._signed()
        self.assertTrue(export["signed"])
        written = json.loads(export["files"]["manifest.json"])
        self.assertEqual(written["signature"], export["manifest"]["signature"])
        self.assertEqual(written["author"], "op_dmitrii")
        self.assertIn("never left the vault", export["signature_note"])

    def test_no_private_key_appears_anywhere_in_the_request(self) -> None:
        # Lab hands over bytes and an identity. Nothing else.
        self._signed()
        self.assertEqual(
            set(self.vault.seen) - {"path", "payload", "token"},
            {"operator", "key_id"},
        )


class VaultFailureTest(_Base):
    def test_a_refusing_vault_is_surfaced_not_swallowed(self) -> None:
        # An export must never come back looking signed because the signing step
        # failed quietly.
        vault = _StubVault(status=403)
        self.addCleanup(vault.close)
        with self.assertRaises(cp_sign.CpSignRefused) as caught:
            cp_sign.build_export(self.bundle, self.traces, signing={
                "cp_url": vault.url, "operator": "nobody", "key_id": "prod-1",
                "token": None,
            })
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("refused to sign", caught.exception.message)

    def test_an_unreachable_vault_is_a_502(self) -> None:
        with self.assertRaises(cp_sign.CpSignRefused) as caught:
            cp_sign.build_export(self.bundle, self.traces, signing={
                "cp_url": "http://127.0.0.1:1", "operator": "op", "key_id": "k",
                "token": None,
            })
        self.assertEqual(caught.exception.status, 502)
        self.assertIn("unreachable", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
