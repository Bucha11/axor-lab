"""Lab and axor-wrap compile the same manifests into the same governor config.

Both turn tool manifests into kwargs for the same `ToolCallGovernor`, and
axor-wrap's module docstring states outright that its mapping is the SAME as
Lab's canonical compilation. Nothing checked it, and it was not: Lab never
emitted `sensitive_sources`.

That is not a cosmetic difference. A sensitive source arms the CONFIDENTIALITY
FLOOR, which restricts egress for the rest of the session whether or not the
sink argument derives from the secret. With it omitted, the same kernel reading
the same manifests reached opposite verdicts:

    secret read -> egress to an attacker URL
      Lab   (no sensitive_sources):  floor inactive -> ALLOW
      wrap  (sensitive_sources set): floor armed    -> DENY

So a whole gate was switched off in Lab's real-kernel runs, and an experiment
measuring secret leakage would have reported governance failing to contain it.
It also made the executable config hash lie: two manifests differing only in
`sensitive_fields` govern differently and hashed identically.
"""

from __future__ import annotations

import importlib.util
import unittest

from lab_contracts import compiled_governor_config
from lab_capabilities.governance.axor_backend import governor_config

HAS_WRAP = importlib.util.find_spec("axor_wrap") is not None

SECRET_READ = {
    "schema_version": "tool-manifest/v1", "id": "read_secret",
    "args_schema": {"type": "object", "properties": {}, "required": []},
    "result_schema": {"type": "object"},
    "effect": {"default_class": "READ", "driving_args": []},
    "untrusted_fields": ["result.token"],
    "sensitive_fields": ["result.token"],
    "side_effecting": False,
}
EGRESS = {
    "schema_version": "tool-manifest/v1", "id": "send",
    "args_schema": {"type": "object", "properties": {"to": {"type": "string"}},
                    "required": ["to"]},
    "result_schema": {"type": "object"},
    "effect": {"default_class": "EXPORT", "driving_args": ["to"]},
    "side_effecting": True,
}
MANIFESTS = [SECRET_READ, EGRESS]


def _normalized(config: dict[str, object]) -> dict[str, object]:
    return {
        key: sorted(value) if isinstance(value, (set, list)) else value
        for key, value in config.items()
    }


class TestTheCompiledConfigDeclaresSensitiveSources(unittest.TestCase):
    def test_a_sensitive_field_makes_its_tool_a_sensitive_source(self) -> None:
        canon = compiled_governor_config("k", None, MANIFESTS)
        self.assertEqual(canon["sensitive_sources"], ["read_secret"])

    def test_it_reaches_the_governor_kwargs(self) -> None:
        self.assertEqual(
            governor_config({m["id"]: m for m in MANIFESTS}, None)["sensitive_sources"],
            {"read_secret"},
        )

    def test_a_manifest_without_sensitive_fields_declares_none(self) -> None:
        canon = compiled_governor_config("k", None, [EGRESS])
        self.assertEqual(canon["sensitive_sources"], [])

    def test_sensitive_fields_change_the_config_identity(self) -> None:
        """They change verdicts, so they must change the fingerprint. Hashing
        two differently-governing configs identically is what lets a bundle
        claim to pin the control it ran under while pinning something else."""
        from lab_contracts import content_hash

        without = {**SECRET_READ}
        without.pop("sensitive_fields")
        self.assertNotEqual(
            content_hash(compiled_governor_config("k", None, [SECRET_READ, EGRESS])),
            content_hash(compiled_governor_config("k", None, [without, EGRESS])),
        )


class TestTheFloorActuallyArms(unittest.TestCase):
    def _drive(self, config: dict[str, object]):
        from axor_core.governor import ToolCallGovernor

        governor = ToolCallGovernor(**config)  # type: ignore[arg-type]
        read = governor.evaluate("read_secret", {})
        governor.register_output(read, "sk-live-DEADBEEF")
        egress = governor.evaluate("send", {"to": "https://attacker.example/x"})
        return governor.confidentiality_floor_active(), egress.allowed

    def test_a_secret_read_arms_the_floor_and_the_egress_is_denied(self) -> None:
        armed, allowed = self._drive(governor_config({m["id"]: m for m in MANIFESTS}, None))
        self.assertTrue(armed)
        self.assertFalse(allowed)

    def test_without_the_declaration_the_same_kernel_allows_it(self) -> None:
        """The regression this file exists to prevent, stated as a measurement."""
        config = governor_config({m["id"]: m for m in MANIFESTS}, None)
        config.pop("sensitive_sources")
        armed, allowed = self._drive(config)
        self.assertFalse(armed)
        self.assertTrue(allowed)


class TestCriticalityOverridesReachTheGovernor(unittest.TestCase):
    """`condition/v1` declares them and the config hash covers them. They were
    then dropped on the way to the governor, so a condition declaring them ran
    without them — and the real-kernel branch is precisely the one exempted
    from the "hashed but ignored" check, on the premise that a real axor-core
    build executes its own policy. True of the kernel; false of what Lab handed
    it."""

    POLICY = {"criticality_overrides": {"send": "CATASTROPHIC"}}

    def _verdict(self, policy: dict[str, object] | None) -> bool:
        from axor_core.governor import ToolCallGovernor

        config = governor_config({"send": EGRESS}, policy)
        return ToolCallGovernor(**config).evaluate("send", {"to": "GB00SAFE"}).allowed  # type: ignore[arg-type]

    def test_an_override_actually_denies(self) -> None:
        self.assertTrue(self._verdict(None), "clean baseline: allowed")
        self.assertFalse(self._verdict(self.POLICY), "the override must bite")

    def test_it_is_compiled_into_the_config_identity(self) -> None:
        canon = compiled_governor_config("k", self.POLICY, [EGRESS])
        self.assertEqual(canon["consequence_overrides"], {"send": "CATASTROPHIC"})

    def test_an_unrecognised_class_is_refused_not_dropped(self) -> None:
        from lab_runner.errors import UnknownKernelError

        with self.assertRaises(UnknownKernelError):
            governor_config({"send": EGRESS},
                            {"criticality_overrides": {"send": "VERY_BAD"}})


class TestEveryDenialCanBeWrittenIntoATrace(unittest.TestCase):
    """`decision.gate` accepts only a gate name. Lab kept a private
    category→gate map keyed on `ssrf`, `consequence`, `positional`, `carrier` —
    names the kernel does not emit — so seven of eleven categories fell through
    unmapped, the raw category landed in the field, and any denial outside the
    taint floor wrote a trace that failed Lab's own schema. Nothing caught it
    because no test denied for those reasons.

    The map now lives in axor-core, which owns both vocabularies. This checks
    the two ends still meet."""

    def _gate_enum(self) -> set[str]:
        # through the loader, not off disk: trace/v1 is axor-core's and is not a
        # file in this repo any more. Reading the owner's schema from the owner
        # is the point — a path here would be the fourth copy.
        from lab_contracts import load_schemas

        schema = load_schemas()["trace"]
        return set(schema["$defs"]["decision"]["properties"]["gate"]["enum"])

    def test_every_gate_the_kernel_can_name_is_a_valid_trace_gate(self) -> None:
        from axor_core.governor import GATE_OF_CATEGORY

        missing = set(GATE_OF_CATEGORY.values()) - self._gate_enum()
        self.assertEqual(
            missing, set(),
            f"the kernel can deny with gate(s) {sorted(missing)} that trace/v1 "
            f"rejects — such a denial cannot be recorded at all",
        )

    # `test_the_runtime_schema_mirror_agrees` lived here: it checked that the
    # package-data copy of trace/v1 listed the same gates as the one under
    # contracts/. Neither copy exists now — axor-core owns the schema and the
    # loader serves it — so there are no two things left to agree. The stronger
    # statement replacing it is that no copy may reappear at all, which
    # `tests/test_packaging.py::TestTheKernelSchemasAreNotKeptHere` asserts.

    def test_a_real_denial_records_a_schema_valid_gate(self) -> None:
        """End to end rather than by inspection: deny for a non-taint reason and
        check what actually lands in the decision."""
        from axor_core.contracts.canonical import ConsequenceClass
        from axor_core.governor import ToolCallGovernor

        from lab_capabilities.governance.axor_backend import gate_with_governor

        config = {"egress_sinks": {"send"}, "untrusted_sources": {"read"},
                  "driving_args": {"send": ["to"]},
                  "consequence_overrides": {"send": ConsequenceClass.CATASTROPHIC}}
        self.assertFalse(ToolCallGovernor(**config).evaluate("send", {"to": "x"}).allowed)
        decision = gate_with_governor(config, "on", [], "send", {"to": "x"}, "v_1")
        self.assertEqual(decision["verdict"], "DENY")
        self.assertIn(decision["gate"], self._gate_enum())
        self.assertEqual(decision["gate"], "consequence")


@unittest.skipUnless(HAS_WRAP, "axor-wrap not installed")
class TestParityWithWrap(unittest.TestCase):
    def test_both_compile_the_same_manifests_identically(self) -> None:
        from axor_wrap.compile import governor_kwargs

        self.assertEqual(
            _normalized(governor_config({m["id"]: m for m in MANIFESTS}, None)),
            _normalized(governor_kwargs(MANIFESTS, None)),
        )

    def test_parity_holds_for_every_policy_field_condition_v1_declares(self) -> None:
        """Not just the fields that happen to agree. A policy field one side
        compiles and the other drops is a control the two runtimes disagree
        about — which is how the same kernel reached opposite verdicts."""
        from axor_wrap.compile import governor_kwargs

        policies: list[dict[str, object]] = [
            {"allowlist": ["GB00KNOWN0000000000000"]},
            {"criticality_overrides": {"send": "CATASTROPHIC"}},
            {"profile": "strict", "trust_model": "content-ledger",
             "allowlist": ["GB00KNOWN0000000000000"],
             "criticality_overrides": {"send": "CATASTROPHIC"}},
        ]
        for policy in policies:
            with self.subTest(policy=sorted(policy)):
                self.assertEqual(
                    _normalized(governor_config({m["id"]: m for m in MANIFESTS}, policy)),
                    _normalized(governor_kwargs(MANIFESTS, policy)),
                )


if __name__ == "__main__":
    unittest.main()
