"""Acceptance test 9 — provenance is multidimensional.

The publication shows origin × integrity × reproductions independently; a
reproduction added later increments the count WITHOUT changing origin, and
the immutable publication body never changes.
"""

from __future__ import annotations

import json
import unittest

from tests import support
from lab_contracts import add_reproduction, build_publication, make_claim, provenance_axes
from lab_contracts.errors import ClaimTypingError

TRACE_REFS = frozenset({"sha256:tr07"})
AGG_REFS = frozenset({"agg:ASR:governed"})


def _publication() -> dict[str, object]:
    return build_publication(
        publication_id="e_axes", bundle_ref="sha256:bundle",
        question="Does carried taint localize damage?",
        origin="local", integrity="hash_verified",
        claims=[
            make_claim("exactly_replayable", "DENY on trace.", "sha256:tr07",
                       trace_refs=TRACE_REFS, aggregate_refs=AGG_REFS),
        ],
        license_id="CC-BY-4.0",
    )


def _attestation(kind: str) -> dict[str, object]:
    return {
        "schema_version": "attestation/v1",
        "publication_id": "e_axes",
        "by": "@ext-lab-mit",
        "kind": kind,
        "created": "2026-07-20T00:00:00Z",
        "result": {"estimate": 0.0},
    }


class TestProvenanceAxes(unittest.TestCase):
    def test_axes_are_independent(self) -> None:
        publication = _publication()
        axes = provenance_axes(publication, log=())
        self.assertEqual(axes["origin"], "local")
        self.assertEqual(axes["integrity"], "hash_verified")
        self.assertEqual(axes["reproductions"]["count"], 0)  # type: ignore[index]

    def test_reproduction_increments_count_without_changing_origin(self) -> None:
        publication = _publication()
        frozen = json.dumps(publication, sort_keys=True)
        log: tuple[dict[str, object], ...] = ()
        log = add_reproduction(log, _attestation("fresh_live"))
        log = add_reproduction(log, _attestation("exact_replay"))
        axes = provenance_axes(publication, log)
        self.assertEqual(axes["reproductions"]["count"], 2)  # type: ignore[index]
        self.assertEqual(axes["reproductions"]["kinds"], ["exact_replay", "fresh_live"])  # type: ignore[index]
        self.assertEqual(axes["origin"], "local")  # origin never changes
        # the immutable publication body did not change
        self.assertEqual(json.dumps(publication, sort_keys=True), frozen)

    def test_reproduction_kinds_are_typed(self) -> None:
        with self.assertRaises(ClaimTypingError):
            add_reproduction((), _attestation("vibes"))

    def test_attestation_is_schema_valid(self) -> None:
        self.assertEqual(support.schema_errors(_attestation("fresh_live"), "attestation"), [])

    def test_self_reported_never_upgrades_to_lab_infra(self) -> None:
        # a local-origin publication with 4 reproductions still reads origin=local
        publication = _publication()
        log = tuple(_attestation("fresh_live") for _ in range(4))
        self.assertEqual(provenance_axes(publication, log)["origin"], "local")


if __name__ == "__main__":
    unittest.main()
