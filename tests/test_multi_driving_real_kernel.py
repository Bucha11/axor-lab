"""The multi-driving-arg taint invariant, verified against the REAL kernel.

When the runners moved onto axor-wrap the reference kernel's pure `decide()` went
away, and with it `test_multi_driving_gate.py`, which pinned one security
invariant on that reimplementation: an allowlisted driving argument supersedes
the taint floor for ITSELF ONLY — it must not short-circuit ALLOW and leave a
later, un-allowlisted, tainted driving argument unexamined (an exfiltrated secret
smuggled through a second sink argument).

That invariant is axor-core's to enforce now, so this re-verifies it where Lab
actually runs it: through `axor_wrap.WrappedToolset` on the installed governor,
not a local reimplementation. A regression here means the real kernel let a
tainted sibling argument ride out on an allowlisted one — the exact hole the
deleted reference test guarded.
"""
from __future__ import annotations

import unittest

from axor_wrap import ToolDenied, WrappedToolset

from lab_capabilities.governance import axor_available, real_kernel_version

TRUSTED = "GB29NWBK60161331926819"
SECRET = "TOP-SECRET-XYZ"

READ = {
    "schema_version": "tool-manifest/v1", "id": "read_secret",
    "args_schema": {"type": "object", "properties": {}, "required": []},
    "result_schema": {"type": "object"},
    "effect": {"default_class": "READ", "driving_args": []},
    "untrusted_fields": ["result.secret"], "side_effecting": False,
}
# an egress sink with TWO driving args: recipient (allowlisted) and body.
SINK = {
    "schema_version": "tool-manifest/v1", "id": "send",
    "args_schema": {
        "type": "object",
        "properties": {"recipient": {"type": "string"}, "body": {"type": "string"}},
        "required": ["recipient", "body"],
    },
    "result_schema": {"type": "object"},
    "effect": {"default_class": "EXPORT", "driving_args": ["recipient", "body"]},
    "side_effecting": True,
}
POLICY = {"allowlist": [TRUSTED]}


@unittest.skipUnless(axor_available(), "requires the real axor-core kernel")
class TestMultiDrivingArgTaintOnTheRealKernel(unittest.TestCase):
    def _toolset(self) -> WrappedToolset:
        tools = {
            "read_secret": lambda: {"secret": SECRET},
            "send": lambda recipient, body: {"ok": True},
        }
        return WrappedToolset(
            tools, [READ, SINK], policy=POLICY, enforcement="on", record=True,
            inputs={"known": [TRUSTED]},
        )

    def test_an_allowlisted_recipient_does_not_hide_a_tainted_body(self) -> None:
        toolset = self._toolset()
        secret = toolset.call("read_secret", {})["secret"]  # type: ignore[index]
        # recipient is allowlisted (superseded), body is the untrusted secret and
        # is NOT allowlisted — the call must DENY on the tainted sibling.
        with self.assertRaises(ToolDenied):
            toolset.call("send", {"recipient": TRUSTED, "body": secret})

    def test_all_driving_args_clean_or_allowlisted_is_allowed(self) -> None:
        toolset = self._toolset()
        toolset.call("read_secret", {})
        # recipient allowlisted, body a clean constant → nothing tainted drives it
        out = toolset.call("send", {"recipient": TRUSTED, "body": "rent for March"})
        self.assertEqual(out, {"ok": True})

    def test_the_invariant_runs_on_the_installed_build(self) -> None:
        # a real-kernel test that silently ran the reference kernel would prove
        # nothing; assert the build under test is a real axor-core pin.
        self.assertTrue(str(real_kernel_version()).startswith("axor-core@"))


if __name__ == "__main__":
    unittest.main()
