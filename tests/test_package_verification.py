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
from lab_runner.cli import EXIT_FAILURE, EXIT_OK, EXIT_UNVERIFIED, main
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

    def test_intact_server_package_verifies(self) -> None:
        self.assertEqual(main(["verify", str(self._write(self.pkg))]), EXIT_OK)

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

    def test_bare_bundle_package_still_passes_without_proofs(self) -> None:
        # a minimal {bundle, traces} package (no schema_version/publication/
        # acceptance) is NOT a server package — no proofs required
        pkg = {"bundle": self.bundle, "traces": list(self.traces.values())}
        self.assertEqual(main(["verify", str(self._write(pkg))]), EXIT_OK)


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


if __name__ == "__main__":
    unittest.main()
