"""A WRITE with driving args compiles to an axor-core integrity sink.

axor-core's ``integrity_sinks`` are state-changing calls whose driving args the
attacker must not choose (a password or profile update, a record written from
extracted fields): integrity check only, no confidentiality floor.
tool-manifest/v1 already describes one — a WRITE effect whose ``driving_args``
are "the arguments whose provenance the gate checks" — but the compilation
turned only EXPORT/EXEC into roles, so those args were never checked.

The key appears only when some tool is one, so the executable / runtime config
hash of every manifest set without one is unchanged. Lab and axor-wrap compile it
identically (their runtime config hashes must be byte-identical).
"""
from __future__ import annotations

import importlib.util
import unittest

from lab_contracts import compiled_governor_config, runtime_config_hash
from lab_capabilities.governance.axor_backend import governor_config

HAS_WRAP = importlib.util.find_spec("axor_wrap") is not None


def _manifest(tool_id: str, default_class: str, driving: list[str],
              resolve: list[dict] | None = None) -> dict[str, object]:
    effect: dict[str, object] = {"default_class": default_class, "driving_args": driving}
    if resolve:
        effect["resolve"] = resolve
    return {
        "schema_version": "tool-manifest/v1", "id": tool_id,
        "args_schema": {"type": "object", "properties": {}, "required": []},
        "effect": effect, "side_effecting": default_class != "READ",
    }


UPDATE = _manifest("update_password", "WRITE", ["password"])
SEND = _manifest("send", "EXPORT", ["to"])
TOUCH = _manifest("touch", "WRITE", [])
DRAFT_OR_SEND = _manifest("draft_or_send", "WRITE", ["to"],
                          resolve=[{"when": {"to": {"not_in": ["a@corp"]}}, "class": "EXPORT"}])


class TestTheCompilation(unittest.TestCase):
    def test_a_write_with_driving_args_is_an_integrity_sink(self) -> None:
        canon = compiled_governor_config("k", None, [UPDATE, SEND])
        self.assertEqual(canon["integrity_sinks"], ["update_password"])
        self.assertEqual(canon["egress_sinks"], ["send"])

    def test_no_driving_args_or_an_export_resolution_is_not_one(self) -> None:
        self.assertNotIn("integrity_sinks", compiled_governor_config("k", None, [TOUCH]))
        canon = compiled_governor_config("k", None, [DRAFT_OR_SEND])
        self.assertNotIn("integrity_sinks", canon)
        self.assertEqual(canon["egress_sinks"], ["draft_or_send"])

    def test_a_manifest_set_without_one_hashes_as_before(self) -> None:
        """The key is absent, not empty: the compiled form — and every hash over it
        — of a manifest set with no integrity sink is exactly what it was."""
        canon = compiled_governor_config("k", None, [SEND, TOUCH])
        self.assertEqual(set(canon), {
            "kernel", "egress_sinks", "untrusted_sources", "sensitive_sources",
            "untrusted_fields", "driving_args", "value_policies", "consequence_overrides",
        })

    def test_the_governor_receives_the_role(self) -> None:
        config = governor_config({"update_password": UPDATE, "send": SEND}, None)
        self.assertEqual(config["integrity_sinks"], {"update_password"})
        self.assertNotIn("integrity_sinks", governor_config({"send": SEND}, None))

    def test_the_taint_floor_off_variant_drops_it_too(self) -> None:
        import inspect

        from lab_service import evidence
        self.assertIn('cfg.pop("integrity_sinks", None)', inspect.getsource(evidence))

    def test_the_builtin_ingest_suite_record_is_one(self) -> None:
        """The ingest suite's record_write is a WRITE driven by `vendor` — now an
        integrity sink. Under the default (clean) integrity mode its verdicts are
        unchanged; under context mode an extracted vendor would need to be a
        trusted value."""
        from lab_suite.builtin.ingest import RECORD, _tools

        canon = compiled_governor_config("k", None, _tools())
        self.assertEqual(canon["integrity_sinks"], [RECORD])


@unittest.skipUnless(HAS_WRAP, "axor-wrap not installed")
class TestParityWithWrap(unittest.TestCase):
    def test_same_compilation_and_runtime_hash(self) -> None:
        from axor_wrap.compile import compile_manifests

        from axor_wrap.trace import content_hash

        manifests = [UPDATE, SEND, TOUCH]
        lab = compiled_governor_config("k", None, manifests)
        wrap = {"kernel": "k", **compile_manifests(manifests)}
        self.assertEqual(lab, wrap)
        # WrappedToolset.runtime_config_hash is content_hash over exactly this form
        self.assertEqual(runtime_config_hash("k", None, manifests, None), content_hash(wrap))
