"""Strict verification outcome (review r15).

`integrity` is not `authenticity`: a receipt that claims `signed` but carries no
signature, or whose signature nobody could check, must NOT verify as a clean
pass. The state machine rejects structural inconsistency, and the CLI returns a
distinct nonzero exit for an unverifiable signature so automation cannot read
"unverified" as "verified".
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
from lab_contracts.signing import (
    SignatureInvalid,
    SignatureUnavailable,
    build_receipt,
    signed_ref,
    verify_receipt,
)
from lab_runner import run_experiment_suite
from lab_runner.cli import EXIT_FAILURE, EXIT_OK, EXIT_UNVERIFIED, main

_HAS_NACL = importlib.util.find_spec("nacl") is not None
CREATED = "2026-07-20T12:00:00+00:00"


def _bundle() -> dict:
    return _bundle_and_traces()[0]


def _bundle_and_traces() -> tuple[dict, dict]:
    scenario = support.banking_scenario()
    conditions = support.conditions()
    result = run_experiment_suite(
        [scenario], support.manifests(), conditions, support.kernel_registry(),
        repeats=8, run_id="r_vo",
    )
    pairs = result.pairs("ungoverned", "governed", metric="ASR")
    aggregates = [
        binary_aggregate("ASR", "ungoverned", sum(1 for b, _ in pairs if b), len(pairs)),
        binary_aggregate("ASR", "governed", sum(1 for _, t in pairs if t), len(pairs),
                         test=mcnemar_test(pairs, vs="ungoverned")),
    ]
    bundle = build_bundle(
        bundle_id="b_vo", created=CREATED, scenarios=[scenario], conditions=conditions,
        tool_manifests=list(support.manifests().values()), environment=support.environment(),
        trials=result.trials, aggregates=aggregates, traces=result.traces,
    )
    traces = {str(t["trace_id"]): t for t in result.traces.values()}
    return bundle, traces


def _write_pkg(tmp: str, bundle: dict, receipt: dict, traces: dict) -> Path:
    """Write a full VERSIONED reproduction envelope with the given (test-chosen)
    receipt injected — the receipt state machine is exercised INSIDE the envelope,
    since a bare {bundle,traces,receipt} JSON is no longer auto-accepted (r17)."""
    from lab_server.store import PublicationStore

    store = PublicationStore(root=Path(tmp) / "store")
    stored = store.publish(bundle, traces, question="q")
    path = Path(tmp) / "download.json"
    path.write_text(json.dumps({
        "schema_version": "axor-reproduction-package/v1",
        "publication": stored.publication,
        "bundle": stored.bundle,
        "traces": list(stored.traces.values()),
        "receipt": receipt,  # the test's chosen receipt, over the same bundle
        "acceptance": store.acceptance(stored),
    }))
    return path


class TestReceiptStateMachine(unittest.TestCase):
    def test_signed_integrity_without_signature_fails(self) -> None:
        bundle = _bundle()
        forged = {
            "algorithm": "sha256-content-hash", "integrity": "signed",
            "signed_ref": signed_ref(bundle), "author": "acme", "key_id": "acme",
            "signature": None,
        }
        with self.assertRaises(SignatureInvalid):
            verify_receipt(bundle, forged)

    def test_hash_verified_receipt_carrying_a_signature_is_rejected(self) -> None:
        bundle = _bundle()
        sneaky = build_receipt(bundle, integrity="hash_verified")
        sneaky["signature"] = "deadbeef"  # a hash-only claim must not carry auth
        with self.assertRaises(SignatureInvalid):
            verify_receipt(bundle, sneaky)

    def test_algorithm_integrity_mismatch_is_rejected(self) -> None:
        bundle = _bundle()
        bad = build_receipt(bundle, integrity="hash_verified")
        bad["algorithm"] = "ed25519"  # algorithm disagrees with integrity
        with self.assertRaises(SignatureInvalid):
            verify_receipt(bundle, bad)

    def test_signed_receipt_without_pubkey_is_unavailable_not_pass(self) -> None:
        bundle = _bundle()
        receipt = build_receipt(bundle, integrity="signed", author="acme",
                                key_id="acme", signature="ab" * 32)
        with self.assertRaises(SignatureUnavailable):
            verify_receipt(bundle, receipt)  # no pubkey → cannot verify → not a pass


class TestCliVerifyExitCodes(unittest.TestCase):
    def test_stripped_signature_signed_receipt_exits_nonzero(self) -> None:
        bundle, traces = _bundle_and_traces()
        forged = {
            "algorithm": "sha256-content-hash", "integrity": "signed",
            "signed_ref": signed_ref(bundle), "author": "acme", "key_id": "acme",
            "signature": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _write_pkg(tmp, bundle, forged, traces)
            # a forged signed receipt is INVALID regardless of the acceptance mode
            self.assertEqual(main(["verify", str(pkg), "--allow-unsigned-server"]), EXIT_FAILURE)

    def test_signed_receipt_over_hash_verified_publication_is_rejected(self) -> None:
        # _write_pkg publishes WITHOUT an author signature → a hash_verified
        # publication. Injecting a SIGNED author receipt over it is an integrity
        # mismatch (the store would never emit that pairing) and is now rejected
        # as a proof-inconsistency (review r18). The pure "signed receipt without
        # pubkey → UNVERIFIED" behaviour is unit-tested against verify_receipt
        # directly in TestReceiptStateMachine; here the package is inconsistent.
        bundle, traces = _bundle_and_traces()
        receipt = build_receipt(bundle, integrity="signed", author="acme",
                                key_id="acme", signature="ab" * 32)
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _write_pkg(tmp, bundle, receipt, traces)
            self.assertEqual(
                main(["verify", str(pkg), "--allow-unsigned-server"]), EXIT_FAILURE
            )

    def test_hash_verified_package_still_passes(self) -> None:
        bundle, traces = _bundle_and_traces()
        receipt = build_receipt(bundle, integrity="hash_verified")
        with tempfile.TemporaryDirectory() as tmp:
            pkg = _write_pkg(tmp, bundle, receipt, traces)
            self.assertEqual(main(["verify", str(pkg), "--allow-unsigned-server"]), EXIT_OK)


@unittest.skipUnless(_HAS_NACL, "PyNaCl not installed")
class TestSignedTrustAnchor(unittest.TestCase):
    def test_wrong_author_trust_anchor_is_rejected(self) -> None:
        from nacl.signing import SigningKey

        from lab_contracts.signing import sign_bundle

        bundle = _bundle()
        sk = SigningKey.generate()
        sig = sign_bundle(bundle, bytes(sk).hex())
        pub = bytes(sk.verify_key).hex()
        receipt = build_receipt(bundle, integrity="signed", author="acme",
                                key_id="acme", signature=sig)
        verify_receipt(bundle, receipt, pub, expected_author="acme")  # ok
        with self.assertRaises(SignatureInvalid):
            verify_receipt(bundle, receipt, pub, expected_author="someone-else")


if __name__ == "__main__":
    unittest.main()
