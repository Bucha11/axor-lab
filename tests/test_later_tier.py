"""Later-tier code slices (plan B5/B6/B7), each anchored to its contract.

B5 — instrumented endpoint produces a conformant governance-capable trace;
     black-box produces NO trace and is labeled evaluation-only; SSRF guard.
B6 — the sandbox policy layer blocks each attack class with an audit record.
B7 — a game's statistic is per-run: n = runs (never rounds), CI narrows with
     runs, a round-level unit is rejected.
"""

from __future__ import annotations

import sys
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
                    "decision_value": support.INJECTION_TEXT,
                    "labels": ["untrusted_derived"],
                    "sources": [{"kind": "external_read",
                                 "origin_ref": "tool_result:read_txns:transactions[1].description"}],
                }],
            ),
            EmittedEvent(
                type="tool_result", tool="read_txns",
                values=[{
                    "value_id": "v_recipient", "preview": support.ATTACKER_IBAN,
                    "decision_value": support.ATTACKER_IBAN,
                    "labels": ["untrusted_derived"], "transformations": ["model_extraction"],
                    "derived_from": ["v_inj"],
                    "sources": [{"kind": "external_read",
                                 "origin_ref": "tool_result:read_txns:transactions[1].description"}],
                }, {
                    "value_id": "v_amount", "preview": "1200", "decision_value": 1200,
                    "labels": ["prompt_given"], "sources": [{"kind": "constant"}],
                }],
            ),
            EmittedEvent(
                type="tool_call_intent", tool="send_money",
                arg_bindings={"recipient": "v_recipient", "amount": "v_amount"},
                args={"recipient": support.ATTACKER_IBAN, "amount": 1200},
            ),
        ]

    def test_instrumented_trace_is_conformant_and_gated(self) -> None:
        from lab_endpoint import assemble_and_gate

        kernel = support.kernel_registry().get(support.KERNEL_PINNED)
        scenario = support.banking_scenario()
        trace = assemble_and_gate(
            self._emitted(), support.conditions()[1], support.manifests(),
            scenario["inputs"], kernel,
            run_id="r_ep", scenario_id="banking-exfil-01",
            fixtures=scenario.get("fixtures", {}), trusted_runtime=True,
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


# ── B6 — REAL OS-level enforcement (subprocess + RLIMIT) ────────────────────
class TestSandboxRealExecutor(unittest.TestCase):
    """The sandbox policy layer backed by actual OS resource limits — the
    controls the kernel enforces today, without the full isolation runtime."""

    def setUp(self) -> None:
        from lab_sandbox import HAS_RESOURCE
        if not HAS_RESOURCE:
            self.skipTest("resource limits require a Unix host")

    def _limits(self):
        from lab_sandbox import ResourceLimits
        return ResourceLimits(cpu_seconds=1, mem_mb=300, disk_mb=1,
                              wall_seconds=4, output_kb=4, max_processes=48)

    def _cpu_limits(self):
        # A generous wall ceiling for the CPU-bound tests: a busy loop burns its
        # 1 CPU-second in ~1s of wall on an unthrottled core, so SIGXCPU fires
        # long before this backstop — but on a throttled/oversubscribed CI runner
        # accumulating 1 CPU-second can take several wall-seconds, and a tight
        # wall would win the race and mislabel the kill. 20s removes that race
        # without slowing the normal path (the process still exits at ~1s).
        from lab_sandbox import ResourceLimits
        return ResourceLimits(cpu_seconds=1, mem_mb=300, disk_mb=1,
                              wall_seconds=20, output_kb=4, max_processes=48)

    def test_benign_code_completes(self) -> None:
        from lab_sandbox import OUTCOME_COMPLETED, run_python
        result = run_python("print(6 * 7)", self._limits())
        self.assertEqual(result.outcome, OUTCOME_COMPLETED)
        self.assertEqual(result.stdout.strip(), "42")

    def test_cpu_bomb_is_killed_by_rlimit_cpu(self) -> None:
        from lab_sandbox import OUTCOME_KILLED_CPU, run_python
        result = run_python("while True: pass", self._cpu_limits())
        self.assertEqual(result.outcome, OUTCOME_KILLED_CPU)

    def test_wall_clock_overrun_is_killed(self) -> None:
        from lab_sandbox import OUTCOME_KILLED_WALL, run_python
        result = run_python("import time; time.sleep(30)", self._limits())
        self.assertEqual(result.outcome, OUTCOME_KILLED_WALL)

    def test_output_flood_is_capped(self) -> None:
        from lab_sandbox import OUTCOME_OUTPUT_CAPPED, run_python
        result = run_python("print('A' * 200000)", self._limits())
        self.assertEqual(result.outcome, OUTCOME_OUTPUT_CAPPED)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.stdout), 4 * 1024)

    def test_output_flood_does_not_accumulate_in_parent_memory(self) -> None:
        # a child that would print ~1 GiB: the executor reads incrementally and
        # kills the process group at the cap, so the PARENT never buffers it all
        # (the old capture_output would have). The returned string stays bounded.
        from lab_sandbox import OUTCOME_OUTPUT_CAPPED, run_python
        result = run_python(
            "import sys\n"
            "chunk = 'A' * 65536\n"
            "for _ in range(16384): sys.stdout.write(chunk)\n",
            self._limits(),
        )
        self.assertEqual(result.outcome, OUTCOME_OUTPUT_CAPPED)
        self.assertLessEqual(len(result.stdout), 4 * 1024)

    def test_stderr_is_also_capped(self) -> None:
        # stderr shares the same capped stream — it used to accumulate unbounded
        from lab_sandbox import OUTCOME_OUTPUT_CAPPED, run_python
        result = run_python(
            "import sys; sys.stderr.write('E' * 200000)", self._limits()
        )
        self.assertEqual(result.outcome, OUTCOME_OUTPUT_CAPPED)
        self.assertLessEqual(len(result.stdout), 4 * 1024)

    def test_disk_fill_is_contained(self) -> None:
        # RLIMIT_FSIZE (1 MB) prevents a 50 MB write; the run does NOT complete
        from lab_sandbox import OUTCOME_COMPLETED, run_python
        result = run_python(
            "open('/tmp/axor_sbx_probe','wb').write(b'A' * (50 * 1024 * 1024))",
            self._limits(),
        )
        self.assertNotEqual(result.outcome, OUTCOME_COMPLETED)  # contained

    def test_fsize_limit_is_actually_set_in_the_child(self) -> None:
        from lab_sandbox import run_python
        result = run_python(
            "import resource;print(resource.getrlimit(resource.RLIMIT_FSIZE)[0])",
            self._limits(),
        )
        self.assertEqual(result.stdout.strip(), str(1 * 1024 * 1024))  # 1 MB cap in effect

    @unittest.skipUnless(sys.platform.startswith("linux"), "RLIMIT_AS enforced on Linux")
    def test_memory_bomb_is_contained(self) -> None:
        from lab_sandbox import OUTCOME_COMPLETED, run_python
        result = run_python("x = bytearray(2_000_000_000)", self._limits())
        self.assertNotEqual(result.outcome, OUTCOME_COMPLETED)  # MemoryError, contained

    def test_child_cannot_raise_its_own_limits(self) -> None:
        # a hostile program that tries to lift RLIMIT_CPU still gets contained.
        # The claim under test is containment, not which signal delivers it:
        # raising the hard limit needs privilege we don't grant, so the child
        # stays capped and is killed — normally by SIGXCPU (RLIMIT_CPU), or by
        # the wall backstop if a throttled runner hasn't burned the CPU second
        # yet. Either way it does NOT complete.
        from lab_sandbox import (
            OUTCOME_COMPLETED,
            OUTCOME_KILLED_CPU,
            OUTCOME_KILLED_WALL,
            run_python,
        )
        code = (
            "import resource\n"
            "try:\n"
            "    resource.setrlimit(resource.RLIMIT_CPU, (9999, 9999))\n"
            "except Exception: pass\n"
            "while True: pass\n"
        )
        outcome = run_python(code, self._cpu_limits()).outcome
        self.assertNotEqual(outcome, OUTCOME_COMPLETED)  # contained
        self.assertIn(outcome, (OUTCOME_KILLED_CPU, OUTCOME_KILLED_WALL))


# ── B5 — the live gateway over real HTTP (governance-capable endpoint) ───────
class TestInstrumentedGateway(unittest.TestCase):
    def test_gateway_gates_the_sink_intent_synchronously(self) -> None:
        import json
        import threading
        import urllib.request

        from lab_endpoint import make_gateway

        server = make_gateway(
            support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], scenario_id="banking-exfil-01",
        )
        base = f"http://127.0.0.1:{server.server_address[1]}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def post(path, obj, secret=None):
                headers = {"Content-Type": "application/json"}
                if secret:
                    headers["Authorization"] = f"Bearer {secret}"
                req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                             headers=headers, method="POST")
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())

            opened = post("/runs", {})
            run_id, secret = opened["run_id"], opened["run_secret"]
            self.assertTrue(run_id.startswith("r_ep_"))
            self.assertGreater(len(run_id), 20)  # unpredictable, not sequential
            # emit the untrusted read value, then the sink intent bound to it
            post(f"/runs/{run_id}/events", {
                "type": "tool_result", "tool": "read_txns",
                "values": [{"value_id": "v_r", "preview": support.ATTACKER_IBAN,
                            "decision_value": support.ATTACKER_IBAN,
                            "labels": ["untrusted_derived"],
                            "sources": [{"kind": "external_read",
                                         "origin_ref": "tool_result:read_txns:transactions[1].description"}]},
                           {"value_id": "v_amt", "preview": "1200", "decision_value": 1200,
                            "labels": ["prompt_given"], "sources": [{"kind": "constant"}]}],
            }, secret=secret)
            resp = post(f"/runs/{run_id}/events", {
                "type": "tool_call_intent", "tool": "send_money",
                "arg_bindings": {"recipient": "v_r", "amount": "v_amt"},
                "args": {"recipient": support.ATTACKER_IBAN, "amount": 1200},
            }, secret=secret)
            # the tool proxy verdict came back BEFORE the tool would run
            self.assertEqual(resp["decision"]["verdict"], "DENY")

            # the trace is only published after an explicit finalize
            post(f"/runs/{run_id}/finalize", {}, secret=secret)
            req = urllib.request.Request(base + f"/runs/{run_id}/trace",
                                         headers={"Authorization": f"Bearer {secret}"})
            with urllib.request.urlopen(req) as r:
                trace = json.loads(r.read())
            self.assertEqual(trace["producer"]["mode"], "instrumented_endpoint")
            self.assertEqual(support.schema_errors(trace, "trace"), [])
        finally:
            server.shutdown()
            server.server_close()

    def test_gateway_requires_token_and_run_secret(self) -> None:
        import json
        import threading
        import urllib.error
        import urllib.request

        from lab_endpoint import make_gateway

        server = make_gateway(
            support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], scenario_id="banking-exfil-01",
            token="gwsecret",
        )
        base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def post(path, obj, headers=None):
                req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                             headers={"Content-Type": "application/json", **(headers or {})},
                                             method="POST")
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())

            # opening a run without the gateway token → 401
            self.assertEqual(post("/runs", {})[0], 401)
            status, opened = post("/runs", {}, {"Authorization": "Bearer gwsecret"})
            self.assertEqual(status, 201)
            run_id, secret = opened["run_id"], opened["run_secret"]
            # posting an event without the RUN secret → 401 (can't inject into another's run)
            self.assertEqual(post(f"/runs/{run_id}/events", {"type": "tool_result", "tool": "x", "values": []})[0], 401)
            self.assertEqual(
                post(f"/runs/{run_id}/events", {"type": "tool_result", "tool": "read_txns", "values": []},
                     {"Authorization": f"Bearer {secret}"})[0], 200)
        finally:
            server.shutdown()
            server.server_close()


# ── B7 federation + B8 population scale ──────────────────────────────────────
class TestFederationAndPopulation(unittest.TestCase):
    def _members(self, n: int, compromised: int = 1):
        from lab_games import Member
        from lab_games.runtime import tit_for_tat

        def coop(my_past, neighbor_signal):
            return neighbor_signal  # cooperate iff the (carried) signal is clean
        return [Member(f"m{i}", coop, compromised=(i < compromised)) for i in range(n)]

    def test_federation_unit_is_one_run_not_per_member(self) -> None:
        from lab_games import game_rate_aggregate, run_federation

        # n=20 runs of a 10-member federation; unit = run of the federation
        values = [
            run_federation(self._members(10), rounds=15, run_id=f"f{i}").cooperation_rate()
            for i in range(20)
        ]
        agg = game_rate_aggregate("cooperation", "governed", values)
        self.assertEqual(agg["n"], 20)               # runs, NOT 20*10 members
        self.assertEqual(agg["unit_of_analysis"], "run")

    def test_carried_taint_contains_a_compromised_member(self) -> None:
        from lab_games import run_federation

        governed = run_federation(self._members(20, compromised=1), rounds=10,
                                  topology="ring", carried_taint=True)
        ungoverned = run_federation(self._members(20, compromised=1), rounds=10,
                                    topology="ring", carried_taint=False)
        # governance contains the blast radius; without it the defection spreads
        self.assertIsNotNone(governed.contained_at)
        self.assertLess(governed.compromised_spread(), ungoverned.compromised_spread())

    def test_population_scales_to_many_members(self) -> None:
        from lab_games import run_federation

        run = run_federation(self._members(200, compromised=1), rounds=5,
                             topology="star", carried_taint=True)
        self.assertEqual(len(run.member_moves), 200)  # town of N
        # a single compromise stays contained even at population scale
        self.assertLessEqual(run.compromised_spread(), 2)

    def test_topologies_are_supported(self) -> None:
        from lab_games import TOPOLOGY_COMPLETE, TOPOLOGY_RING, TOPOLOGY_STAR, run_federation

        for topology in (TOPOLOGY_RING, TOPOLOGY_STAR, TOPOLOGY_COMPLETE):
            run = run_federation(self._members(8), rounds=6, topology=topology)
            self.assertEqual(len(run.member_moves), 8)


# ── P0.6 — gateway/kernel fail closed on missing/forged provenance ──────────
class TestGatewayFailClosed(unittest.TestCase):
    def test_kernel_denies_egress_arg_with_no_resolvable_provenance(self) -> None:
        from lab_runner.kernel import Kernel

        kernel = Kernel(version=support.KERNEL_PINNED)
        # an egress send_money whose recipient binding resolves to NO labels
        decision = kernel.decide(
            enforcement="on",
            manifest=support.manifests()["send_money"],
            args={"recipient": support.ATTACKER_IBAN, "amount": 1200},
            arg_labels={},  # recipient has no resolvable provenance
            arg_bindings={"recipient": "v_unknown"},
            inputs=support.banking_scenario()["inputs"],
            policy={"profile": "strict"},
        )
        self.assertEqual(decision["verdict"], "DENY")
        self.assertIn("fail-closed", decision["reason"])

    def test_gateway_rejects_intent_binding_unknown_value(self) -> None:
        import json
        import threading
        import urllib.error
        import urllib.request

        from lab_endpoint import make_gateway

        server = make_gateway(
            support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], scenario_id="banking-exfil-01",
        )
        base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def post(path, obj, secret=None):
                headers = {"Content-Type": "application/json"}
                if secret:
                    headers["Authorization"] = f"Bearer {secret}"
                req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                             headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())

            opened = post("/runs", {})[1]
            run_id, secret = opened["run_id"], opened["run_secret"]
            # intent references a value the client never emitted → 400, not ALLOW
            status, body = post(f"/runs/{run_id}/events", {
                "type": "tool_call_intent", "tool": "send_money",
                "arg_bindings": {"recipient": "v_forged"},
                "args": {"recipient": support.ATTACKER_IBAN, "amount": 1200},
            }, secret=secret)
            self.assertEqual(status, 400)
            self.assertIn("unknown value", body["error"])
        finally:
            server.shutdown()
            server.server_close()

    def test_gateway_rejects_duplicate_value_id(self) -> None:
        import json
        import threading
        import urllib.error
        import urllib.request

        from lab_endpoint import make_gateway

        server = make_gateway(
            support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], scenario_id="banking-exfil-01",
        )
        base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def post(path, obj, secret=None):
                headers = {"Content-Type": "application/json"}
                if secret:
                    headers["Authorization"] = f"Bearer {secret}"
                req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                             headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())

            opened = post("/runs", {})[1]
            run_id, secret = opened["run_id"], opened["run_secret"]
            v = {"value_id": "v_dup", "decision_value": support.ATTACKER_IBAN,
                 "labels": ["untrusted_derived"], "sources": [{"kind": "external_read"}]}
            self.assertEqual(post(f"/runs/{run_id}/events",
                                  {"type": "tool_result", "tool": "read_txns", "values": [v]}, secret=secret)[0], 200)
            # re-emitting the same value_id (redefining its lineage) is rejected
            status, body = post(f"/runs/{run_id}/events",
                               {"type": "tool_result", "tool": "read_txns", "values": [v]}, secret=secret)
            self.assertEqual(status, 400)
            self.assertIn("duplicate", body["error"])
        finally:
            server.shutdown()
            server.server_close()
