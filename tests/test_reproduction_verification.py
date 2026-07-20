"""Reproductions split into verified vs self-reported, re-verified on load
(review round 8, P1).

The reproduction count was a single number: unsigned self-reports counted the
same as cryptographically verified reproductions, the badge showed one
"reproduced ×N", and on restart reproductions.json was loaded raw — no schema
re-check, no signature re-verification, no dedup — so a hand-edit could add
duplicates, invalid kinds, or a forged `verified: true`.

Now: `verified` is EARNED only from a valid signature by a known key (never an
input flag); provenance_axes splits verified/unverified; the public badge counts
only verified; and the persisted log is re-folded through add_reproduction on
every read, so a forged/duplicate/invalid on-disk entry is dropped, not trusted.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from lab_contracts import provenance_axes
from lab_contracts.publication import add_reproduction, rebuild_reproduction_log

_HAS_NACL = importlib.util.find_spec("nacl") is not None
_PUB = {"origin": "local", "integrity": "hash_verified"}


def _att(by="@ext", kind="fresh_live", **extra):
    a = {"schema_version": "attestation/v1", "publication_id": "e_1", "by": by,
         "kind": kind, "created": "2026-07-20T00:00:00Z", "result": {"estimate": 0.0}}
    a.update(extra)
    return a


class TestReproductionVerification(unittest.TestCase):
    def test_unsigned_attestation_is_unverified(self) -> None:
        log = add_reproduction((), _att())
        axes = provenance_axes(_PUB, log)["reproductions"]
        self.assertEqual(axes["count"], 1)
        self.assertEqual(axes["verified"], 0)
        self.assertEqual(axes["unverified"], 1)

    def test_caller_supplied_verified_flag_is_ignored(self) -> None:
        # an unsigned attestation that simply CLAIMS verified:true is not trusted
        log = add_reproduction((), _att(verified=True))
        self.assertIsNot(log[0].get("verified"), True)
        self.assertEqual(provenance_axes(_PUB, log)["reproductions"]["verified"], 0)

    def test_reload_drops_a_forged_verified_flag(self) -> None:
        # simulate a hand-edited reproductions.json: an unsigned entry with a
        # forged verified flag. Rebuilding re-derives verified from crypto (none),
        # so the forgery does not survive.
        forged = (_att(verified=True) | {"verified": True},)
        rebuilt = rebuild_reproduction_log(forged)
        self.assertEqual(provenance_axes(_PUB, rebuilt)["reproductions"]["verified"], 0)

    def test_reload_dedups_and_drops_invalid_kind(self) -> None:
        raw = (
            _att(by="@a"), _att(by="@a"),               # duplicate (by,kind,pub)
            _att(by="@b", kind="not_a_real_kind"),      # invalid kind
            _att(by="@c"),
        )
        rebuilt = rebuild_reproduction_log(raw)
        bys = sorted(str(a["by"]) for a in rebuilt)
        self.assertEqual(bys, ["@a", "@c"])  # dedup collapsed @a, dropped bad kind

    def test_reload_binds_attestation_to_expected_publication(self) -> None:
        # a valid attestation whose publication_id names a DIFFERENT publication
        # cannot be transplanted into this one's log (review r9)
        raw = (_att(by="@x", publication_id="e_OTHER"), _att(by="@y", publication_id="e_1"))
        rebuilt = rebuild_reproduction_log(raw, expected_publication_id="e_1")
        self.assertEqual([str(a["by"]) for a in rebuilt], ["@y"])

    def test_reload_drops_schema_invalid_junk(self) -> None:
        # a hand-added junk field is forbidden by additionalProperties:false
        raw = (_att(by="@a", publication_id="e_1", junk="x"), _att(by="@b", publication_id="e_1"))
        rebuilt = rebuild_reproduction_log(raw, expected_publication_id="e_1")
        self.assertEqual([str(a["by"]) for a in rebuilt], ["@b"])

    @unittest.skipUnless(_HAS_NACL, "PyNaCl required for signed-attestation path")
    def test_signed_attestation_is_verified_and_survives_reload(self) -> None:
        from nacl.signing import SigningKey
        from lab_contracts.canonical import canonical_json

        sk = SigningKey.generate()
        pub_hex = sk.verify_key.encode().hex()
        known = {"@signer": pub_hex}
        body = _att(by="@signer")
        signed_bytes = canonical_json({"content_hashes": body}).encode()
        signature = sk.sign(signed_bytes).signature.hex()
        log = add_reproduction((), body | {"signature": signature}, known)
        self.assertIs(log[0]["verified"], True)
        self.assertIn("signature", log[0])  # signature retained for re-verification
        axes = provenance_axes(_PUB, log)["reproductions"]
        self.assertEqual((axes["verified"], axes["unverified"]), (1, 0))
        # persist → reload: the signature re-verifies, verified is recomputed
        rebuilt = rebuild_reproduction_log(tuple(log), known)
        self.assertIs(rebuilt[0]["verified"], True)
        # ...but WITHOUT the known key (a fresh server missing it) → not verified
        dropped = rebuild_reproduction_log(tuple(log), {})
        self.assertEqual(len(dropped), 0)  # unknown-key signed entry is dropped

    def test_badge_counts_only_verified(self) -> None:
        from lab_server.html import _provenance_badges

        log = add_reproduction((), _att())  # unsigned
        badges = _provenance_badges(provenance_axes(_PUB, log))
        self.assertIn("verified reproductions &times;0", badges)
        self.assertIn("1 unverified self-report", badges)


class TestReproductionReloadOverStore(unittest.TestCase):
    def test_store_reload_ignores_hand_edited_reproductions(self) -> None:
        # write a forged reproductions.json directly and confirm reproductions_of
        # re-derives a trusted (empty-verified) view
        from lab_contracts import build_bundle
        from lab_runner import run_experiment_suite
        from lab_server.store import PublicationStore
        from tests import support

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            scenario = support.banking_scenario()
            result = run_experiment_suite(
                [scenario], support.manifests(), support.conditions(),
                support.kernel_registry(), repeats=4, run_id="r_rv",
            )
            bundle = build_bundle(
                bundle_id="b_rv", created="2026-07-19T12:00:00+00:00", scenarios=[scenario],
                conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
                environment=support.environment(), trials=result.trials, aggregates=[],
                traces=result.traces,
            )
            traces = {str(t["trace_id"]): t for t in result.traces.values()}
            store = PublicationStore(root=root)
            stored = store.publish(bundle, traces, question="q", visibility="public")
            pid = str(stored.publication["publication_id"])
            # forge the on-disk log: two dupes + a forged verified flag
            forged = [
                _att(by="@x", publication_id=pid, verified=True),
                _att(by="@x", publication_id=pid),
            ]
            (root / pid / "reproductions.json").write_text(json.dumps(forged))
            fresh = PublicationStore(root=root)
            axes = fresh.get(pid).axes()["reproductions"]
            self.assertEqual(axes["count"], 1)      # dedup collapsed the pair
            self.assertEqual(axes["verified"], 0)   # forged verified not trusted


if __name__ == "__main__":
    unittest.main()
