"""Strict reproduction-package verification (review r16, P0/P1).

A server-issued reproduction package carries the bundle, traces, author receipt,
publication body, and server acceptance. The offline verifier must require EVERY
proof object for such a package and bind them: stripping the receipt, or editing
the publication claims or the acceptance, must fail — not silently pass.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_analysis import binary_aggregate, mcnemar_test
from lab_contracts import build_bundle
from lab_runner import run_experiment_suite
from lab_runner.cli import EXIT_FAILURE, EXIT_OK, EXIT_UNVERIFIED, EXIT_VALIDATION, main
from lab_server.store import PublicationStore

_HAS_NACL = importlib.util.find_spec("nacl") is not None
CREATED = "2026-07-20T12:00:00+00:00"


def _publishable():
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_pkg",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_pkg", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


def _server_package(store: PublicationStore, bundle, traces) -> dict:
    stored = store.publish(bundle, traces, question="does governance stop exfil?")
    return {
        "schema_version": "axor-reproduction-package/v1",
        "publication": stored.publication,
        "bundle": stored.bundle,
        "traces": list(stored.traces.values()),
        "receipt": stored.receipt(),
        "acceptance": store.acceptance(stored),
    }


class TestServerPackageVerification(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = PublicationStore(root=Path(self.tmp.name) / "store")
        self.bundle, self.traces = _publishable()
        self.pkg = _server_package(self.store, self.bundle, self.traces)

    def _write(self, pkg: dict) -> Path:
        p = Path(self.tmp.name) / "download.json"
        p.write_text(json.dumps(pkg))
        return p

    def test_intact_but_unsigned_server_package_is_unverified(self) -> None:
        # the default store has no signing key → an UNSIGNED acceptance. It proves
        # only internal self-consistency, not that a real server ran the checks, so
        # it is UNVERIFIED (not a pass) unless the caller opts into local-dev mode
        self.assertEqual(main(["verify", str(self._write(self.pkg))]), EXIT_UNVERIFIED)

    def test_unsigned_server_acceptance_returns_unverified(self) -> None:
        self.assertEqual(main(["verify", str(self._write(self.pkg))]), EXIT_UNVERIFIED)

    def test_unsigned_server_acceptance_passes_with_allow_unsigned(self) -> None:
        self.assertEqual(
            main(["verify", str(self._write(self.pkg)), "--allow-unsigned-server"]), EXIT_OK
        )

    def test_stripping_receipt_from_server_package_fails(self) -> None:
        pkg = dict(self.pkg)
        del pkg["receipt"]
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_FAILURE)

    def test_stripping_acceptance_from_server_package_fails(self) -> None:
        pkg = dict(self.pkg)
        del pkg["acceptance"]
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_FAILURE)

    def test_package_verify_rejects_modified_claims(self) -> None:
        pkg = json.loads(json.dumps(self.pkg))  # deep copy
        pkg["publication"]["claims"].append(
            {"kind": "exactly_replayable", "text": "fabricated claim",
             "evidence_ref": "x", "trace_refs": [], "aggregate_refs": []}
        )
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_FAILURE)

    def test_package_verify_rejects_modified_acceptance_report(self) -> None:
        pkg = json.loads(json.dumps(self.pkg))
        pkg["acceptance"]["semantic_report"]["replay"] = "fabricated"
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_FAILURE)

    def test_package_verify_checks_publication_id(self) -> None:
        pkg = json.loads(json.dumps(self.pkg))
        pkg["publication"]["publication_id"] = "e_deadbeefdeadbeefdeadbeefdeadbeef"
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_FAILURE)

    def test_verify_json_requires_versioned_envelope_by_default(self) -> None:
        # a bare {bundle, traces} JSON has no versioned envelope; by default it is
        # REFUSED so a server package cannot be downgraded to bare silently
        pkg = {"bundle": self.bundle, "traces": list(self.traces.values())}
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_VALIDATION)

    def test_bare_package_requires_explicit_allow_bare(self) -> None:
        pkg = {"bundle": self.bundle, "traces": list(self.traces.values())}
        # without the flag → refused; with it → integrity+replay only, passes
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_VALIDATION)
        self.assertEqual(main(["verify", str(self._write(pkg)), "--allow-bare"]), EXIT_OK)

    def test_stripping_all_server_package_markers_does_not_downgrade_to_bare(self) -> None:
        # strip the envelope AND every proof at once — the classic downgrade. With
        # no versioned schema_version, the file is refused (not read as honest bare)
        pkg = {"bundle": self.pkg["bundle"], "traces": self.pkg["traces"]}
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_VALIDATION)

    def test_acceptance_integrity_must_match_publication_integrity(self) -> None:
        pkg = json.loads(json.dumps(self.pkg))
        # the publication is hash_verified; an acceptance claiming `signed` over it
        # is a mismatched record and must fail (even though nothing else changed)
        pkg["acceptance"]["integrity"] = "signed"
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_FAILURE)

    def test_receipt_integrity_must_match_publication_integrity(self) -> None:
        pkg = json.loads(json.dumps(self.pkg))
        # the author receipt claiming a DIFFERENT integrity than the publication is
        # a proof-downgrade signal and must fail (review r18)
        pkg["receipt"]["integrity"] = "signed"
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_FAILURE)

    def test_damaged_persisted_acceptance_is_reminted_and_verifies(self) -> None:
        # v0.3 collapses the reacceptance/history machinery: when a persisted
        # acceptance is found DAMAGED (its semantic_report no longer binds), the
        # store discards it and mints a FRESH acceptance/v1 from the current bundle
        # on load — no reacceptance/v1, no history chain. The freshly minted
        # acceptance binds correctly and verifies.
        root = Path(self.tmp.name) / "store"
        pid = str(self.pkg["publication"]["publication_id"])
        acc_file = root / pid / "acceptance.json"
        tampered = json.loads(acc_file.read_text())
        tampered["semantic_report"]["verified"].append("FABRICATED_CHECK")
        acc_file.write_text(json.dumps(tampered))
        store = PublicationStore(root=root)
        reloaded = store.get(pid)
        acc = store.acceptance(reloaded)
        self.assertEqual(acc["schema_version"], "axor-lab-acceptance/v1")
        self.assertNotIn("FABRICATED_CHECK", acc["semantic_report"]["verified"])
        pkg = json.loads(json.dumps(self.pkg))
        pkg["acceptance"] = acc
        self.assertEqual(
            main(["verify", str(self._write(pkg)), "--allow-unsigned-server"]), EXIT_OK
        )


@unittest.skipUnless(_HAS_NACL, "PyNaCl not installed")
class TestSignedPackageVerification(unittest.TestCase):
    def test_signed_acceptance_verifies_and_wrong_key_fails(self) -> None:
        from nacl.signing import SigningKey

        sk = SigningKey.generate()
        priv, pub = bytes(sk).hex(), bytes(sk.verify_key).hex()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = PublicationStore(
            root=Path(tmp.name) / "store", server_id="lab.example.com",
            server_key_id="lab-prod-2026", server_signing_key=priv,
        )
        bundle, traces = _publishable()
        pkg = _server_package(store, bundle, traces)
        p = Path(tmp.name) / "download.json"
        p.write_text(json.dumps(pkg))
        # correct server key + trust anchor → verified
        self.assertEqual(main([
            "verify", str(p), "--server-pubkey", pub,
            "--server", "lab.example.com", "--server-key-id", "lab-prod-2026",
        ]), EXIT_OK)
        # no server key → the signed acceptance cannot be checked → UNVERIFIED(5)
        self.assertEqual(main(["verify", str(p)]), EXIT_UNVERIFIED)
        # wrong server trust anchor → INVALID
        self.assertEqual(main([
            "verify", str(p), "--server-pubkey", pub, "--server", "evil.example.com",
        ]), EXIT_FAILURE)

    def _signed_publication_package(self, tmp: str):
        """A package whose publication earned integrity=signed via an author key."""
        from nacl.signing import SigningKey

        from lab_contracts.signing import sign_bundle

        author_sk = SigningKey.generate()
        author_priv, author_pub = bytes(author_sk).hex(), bytes(author_sk.verify_key).hex()
        store = PublicationStore(root=Path(tmp) / "store", known_keys={"acme": author_pub})
        bundle, traces = _publishable()
        sig = sign_bundle(bundle, author_priv)
        stored = store.publish(bundle, traces, question="q", signature=sig, author="acme")
        self.assertEqual(stored.publication["integrity"], "signed")
        pkg = {
            "schema_version": "axor-reproduction-package/v1",
            "publication": stored.publication,
            "bundle": stored.bundle,
            "traces": list(stored.traces.values()),
            "receipt": stored.receipt(),
            "acceptance": store.acceptance(stored),
        }
        path = Path(tmp) / "signed.json"
        path.write_text(json.dumps(pkg))
        return pkg, path, author_pub

    def test_signed_publication_without_author_pubkey_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, path, _ = self._signed_publication_package(tmp)
            # a signed publication whose author signature we hold no key for is
            # UNVERIFIED (the server acceptance is a separate attestation)
            self.assertEqual(main(["verify", str(path), "--allow-unsigned-server"]), EXIT_UNVERIFIED)
            # with the author key it verifies
            _, path2, author_pub = self._signed_publication_package(tmp)
            self.assertEqual(
                main(["verify", str(path2), "--pubkey", author_pub, "--allow-unsigned-server"]),
                EXIT_OK,
            )

    def test_signed_publication_rejects_hash_only_author_receipt(self) -> None:
        from lab_contracts.signing import build_receipt

        with tempfile.TemporaryDirectory() as tmp:
            pkg, _, author_pub = self._signed_publication_package(tmp)
            # strip the author signature: swap in a valid HASH-ONLY receipt. The
            # publication still says signed, so this proof downgrade must FAIL
            pkg["receipt"] = build_receipt(pkg["bundle"], integrity="hash_verified")
            path = Path(tmp) / "downgraded.json"
            path.write_text(json.dumps(pkg))
            self.assertEqual(
                main(["verify", str(path), "--pubkey", author_pub, "--allow-unsigned-server"]),
                EXIT_FAILURE,
            )


if __name__ == "__main__":
    unittest.main()
