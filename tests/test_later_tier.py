"""Later-tier code slices (plan B5/B6/B7), each anchored to its contract.

B5 — instrumented endpoint produces a conformant governance-capable trace;
     black-box produces NO trace and is labeled evaluation-only; SSRF guard.
B6 — the sandbox policy layer blocks each attack class with an audit record.
B7 — a game's statistic is per-run: n = runs (never rounds), CI narrows with
     runs, a round-level unit is rejected.
"""

from __future__ import annotations

import unittest

from tests import support


# ── B5 — instrumented endpoint ──────────────────────────────────────────────
class TestInstrumentedEndpoint(unittest.TestCase):
    def _emitted(self):
        from lab_endpoint import EmittedEvent

        # the agent emits: a read whose result value is untrusted, then a
        # send_money intent binding recipient to that untrusted value
        return [
            EmittedEvent(
                type="tool_result", tool="read_txns",
                values=[{
                    "value_id": "v_inj", "preview": support.INJECTION_TEXT,
                    "labels": ["untrusted_derived"],
                    "sources": [{"kind": "external_read",
                                 "origin_ref": "tool_result:read_txns:transactions[1].description"}],
                }],
            ),
            EmittedEvent(
                type="tool_result", tool="read_txns",
                values=[{
                    "value_id": "v_recipient", "preview": support.ATTACKER_IBAN,
                    "labels": ["untrusted_derived"], "transformations": ["model_extraction"],
                    "derived_from": ["v_inj"],
                    "sources": [{"kind": "external_read",
                                 "origin_ref": "tool_result:read_txns:transactions[1].description"}],
                }],
            ),
            EmittedEvent(
                type="tool_call_intent", tool="send_money",
                arg_bindings={"recipient": "v_recipient"},
                args={"recipient": support.ATTACKER_IBAN, "amount": 1200},
            ),
        ]

    def test_instrumented_trace_is_conformant_and_gated(self) -> None:
        from lab_endpoint import assemble_and_gate

        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        trace = assemble_and_gate(
            self._emitted(), support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], kernel,
            run_id="r_ep", scenario_id="banking-exfil-01",
        )
        self.assertEqual(trace["producer"]["mode"], "instrumented_endpoint")
        self.assertEqual(trace["producer"]["provenance_fidelity"], "explicit_flow_tracked")
        self.assertEqual(support.schema_errors(trace, "trace"), [])
        decision = next(e for e in trace["events"] if e.get("type") == "gate_decision")
        self.assertEqual(decision["decision"]["verdict"], "DENY")

    def test_uninstrumented_events_are_flagged_heuristic(self) -> None:
        from lab_endpoint import assemble_and_gate

        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        trace = assemble_and_gate(
            self._emitted(), support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], kernel,
            run_id="r_ep", scenario_id="banking-exfil-01", labels_carried=False,
        )
        self.assertEqual(trace["producer"]["provenance_fidelity"], "heuristic_attribution")


class TestBlackBoxIsEvaluationOnly(unittest.TestCase):
    def test_black_box_produces_no_trace_and_is_labeled(self) -> None:
        from lab_endpoint import BLACK_BOX_LABEL, score_black_box

        result = score_black_box(
            "pay the rent", endpoint=lambda t: "paid", scorer=lambda o: 1.0 if o == "paid" else 0.0,
        )
        self.assertIsNone(result.trace)
        self.assertFalse(result.governance_available)
        self.assertEqual(result.label, BLACK_BOX_LABEL)
        self.assertIn("not governance", result.label)


class TestEndpointSafety(unittest.TestCase):
    def test_public_ip_allowed(self) -> None:
        from lab_endpoint import ssrf_check

        ssrf_check("https://example.com/run", resolved_ips=["93.184.216.34"])

    def test_private_and_loopback_blocked(self) -> None:
        from lab_endpoint import ssrf_check
        from lab_endpoint.errors import UnsafeEndpoint

        for url, ips in [
            ("http://127.0.0.1/x", None),
            ("http://169.254.169.254/latest/meta-data", None),  # cloud metadata
            ("https://evil.example/x", ["10.0.0.5"]),           # rebinding to private
        ]:
            with self.assertRaises(UnsafeEndpoint):
                ssrf_check(url, resolved_ips=ips)

    def test_unresolved_name_is_refused(self) -> None:
        from lab_endpoint import ssrf_check
        from lab_endpoint.errors import UnsafeEndpoint

        with self.assertRaises(UnsafeEndpoint):
            ssrf_check("https://api.example.com/run")  # no resolved_ips → refuse


# ── B6 — sandbox policy layer ───────────────────────────────────────────────
class TestSandboxRedTeam(unittest.TestCase):
    def _policy(self):
        from lab_sandbox import ResourceLimits, SandboxPolicy

        return SandboxPolicy(
            egress_allowlist=frozenset({"api.anthropic.com"}),
            limits=ResourceLimits(disk_mb=256, max_processes=64, output_kb=1024),
        )

    def test_egress_deny_by_default(self) -> None:
        from lab_sandbox import SandboxDenied

        policy = self._policy()
        policy.check_egress("api.anthropic.com")  # allowlisted → ok
        with self.assertRaises(SandboxDenied) as ctx:
            policy.check_egress("attacker.example")  # exfiltration attempt
        self.assertEqual(ctx.exception.control, "egress")
        self.assertTrue(any(a["control"] == "egress" and not a["allowed"] for a in policy.audit))

    def test_fork_bomb_and_disk_fill_capped(self) -> None:
        from lab_sandbox import SandboxDenied

        policy = self._policy()
        with self.assertRaises(SandboxDenied):
            policy.check_resource("processes", 100_000)   # fork bomb
        with self.assertRaises(SandboxDenied):
            policy.check_resource("disk_mb", 10_000)       # disk fill
        with self.assertRaises(SandboxDenied):
            policy.check_resource("output_kb", 5_000_000)  # output flood

    def test_host_mount_denied(self) -> None:
        from lab_sandbox import SandboxDenied

        with self.assertRaises(SandboxDenied):
            self._policy().check_mount("/etc/passwd")

    def test_secret_injected_without_persisting_the_value(self) -> None:
        policy = self._policy()
        value = policy.read_secret("ANTHROPIC_API_KEY", resolver=lambda name: "sk-secret-123")
        self.assertEqual(value, "sk-secret-123")
        self.assertEqual(policy.secret_reads(), 1)
        # the audit records the access by NAME, never the secret value
        secret_records = [a for a in policy.audit if a["control"] == "secret"]
        self.assertEqual(secret_records[0]["target"], "ANTHROPIC_API_KEY")
        self.assertNotIn("sk-secret-123", str(policy.audit))

    def test_every_denial_is_audited(self) -> None:
        from lab_sandbox import SandboxDenied

        policy = self._policy()
        for attempt in (lambda: policy.check_egress("evil.example"),
                        lambda: policy.check_resource("disk_mb", 99999),
                        lambda: policy.check_mount("/")):
            with self.assertRaises(SandboxDenied):
                attempt()
        denied = [a for a in policy.audit if not a["allowed"]]
        self.assertEqual(len(denied), 3)


# ── B7 — multi-agent games ──────────────────────────────────────────────────
class TestGamePerRunStatistics(unittest.TestCase):
    def _run_values(self, n: int) -> list[float]:
        from lab_games import IteratedGame, Player, run_game
        from lab_games.runtime import always_defect, tit_for_tat

        game = IteratedGame(
            a=Player("tft", tit_for_tat), b=Player("defect", always_defect), rounds=20,
        )
        # deterministic strategies → identical runs; jitter the rate a touch by
        # varying rounds so the CI is meaningful across n
        values = []
        for i in range(n):
            g = IteratedGame(a=game.a, b=game.b, rounds=10 + (i % 11))
            values.append(run_game(g, run_id=f"run_{i}").cooperation_rate())
        return values

    def test_unit_is_run_not_round(self) -> None:
        from lab_games import game_rate_aggregate

        agg = game_rate_aggregate("cooperation", "governed", self._run_values(30))
        self.assertEqual(agg["unit_of_analysis"], "run")
        self.assertEqual(agg["n"], 30)  # n = runs, NOT 30*rounds

    def test_round_level_unit_is_rejected(self) -> None:
        from lab_games import game_rate_aggregate
        from lab_analysis.errors import UnitOfAnalysisError

        with self.assertRaises(UnitOfAnalysisError):
            game_rate_aggregate("cooperation", "governed", [0.5, 0.5], unit_of_analysis="round")

    def test_ci_narrows_with_more_runs(self) -> None:
        from lab_games import game_rate_aggregate

        small = game_rate_aggregate("cooperation", "g", self._run_values(10), seed=1)
        large = game_rate_aggregate("cooperation", "g", self._run_values(100), seed=1)
        small_width = small["interval"]["high"] - small["interval"]["low"]
        large_width = large["interval"]["high"] - large["interval"]["low"]
        self.assertLess(large_width, small_width)


if __name__ == "__main__":
    unittest.main()
