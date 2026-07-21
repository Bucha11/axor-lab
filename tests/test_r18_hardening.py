"""Round-18 persistence + resource + safety hardening.

"The primitive exists" is not "it holds on every path": a temp-file+rename write
that ignores a short os.write can still truncate; a replay that catches every
exception mislabels an internal bug as unsupported_kernel; a real-kernel gate
that skips redacted taint fails OPEN; an event loop that ignores an unknown type
silently drops evidence; and a run with no byte budget can pin unbounded memory
under a bounded event count. Each is closed here (review r18).
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tests import support
from lab_contracts import build_bundle
from lab_endpoint import EmittedEvent, assemble_and_gate, make_gateway
from lab_runner import (
    AxorKernel,
    axor_available,
    real_kernel_version,
    replay_bundle,
    resolve_kernel,
)
from lab_runner.errors import UnknownKernelError
from lab_runner.kernel import KernelRegistry
from lab_runner.replay import REPLAY_UNSUPPORTED_KERNEL
from lab_server.store import _write_atomic

CREATED = "2026-07-21T00:00:00Z"


def _real_condition() -> dict[str, object]:
    from lab_contracts import condition_config_hash

    version = real_kernel_version()
    policy = {"profile": "strict", "trust_model": "content-ledger"}
    return {
        "schema_version": "condition/v1", "id": "governed", "label": "governed (axor-core)",
        "enforcement": "on", "kernel": version, "policy": policy,
        "config_hash": condition_config_hash(version, policy),
    }


class TestWriteAtomicFullWrite(unittest.TestCase):
    def test_large_payload_is_written_in_full(self) -> None:
        # a payload far larger than a single write buffer must land completely —
        # os.write may return a short count and the loop must finish the job
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.json"
            payload = "x" * (5 * 1024 * 1024)
            _write_atomic(path, payload)
            self.assertEqual(path.read_text(), payload)

    def test_short_write_still_writes_every_byte(self) -> None:
        # force os.write to report a SHORT write (one byte at a time): the loop
        # must keep going until the whole buffer is on disk, never truncate
        import lab_server.store as store_mod

        real_write = store_mod.os.write

        def dribble(fd: int, data: object) -> int:
            return real_write(fd, bytes(data)[:1])  # write at most one byte

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "dribble.json"
            payload = "abcdefghij" * 500
            with mock.patch.object(store_mod.os, "write", side_effect=dribble):
                _write_atomic(path, payload)
            self.assertEqual(path.read_text(), payload)


class TestReplayNarrowException(unittest.TestCase):
    def _bundle(self) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        from lab_runner import run_experiment

        scenario = support.banking_scenario()
        result = run_experiment(
            scenario, support.manifests(), support.conditions(),
            support.kernel_registry(), repeats=4, run_id="r_narrow",
        )
        bundle = build_bundle(
            bundle_id="b_narrow", created=CREATED, scenarios=[scenario],
            conditions=support.conditions(), tool_manifests=list(support.manifests().values()),
            environment=support.environment(), trials=result.trials, aggregates=[],
            traces=result.traces,
        )
        return bundle, result.traces

    def test_unknown_kernel_is_a_status_not_a_crash(self) -> None:
        bundle, traces = self._bundle()
        kernels = {k.version: k for k in support.kernel_registry().kernels}
        with mock.patch(
            "lab_runner.axor_backend.resolve_kernel",
            side_effect=UnknownKernelError("pinned kernel unavailable"),
        ):
            report = replay_bundle(bundle, traces, kernels)
        self.assertFalse(report.bit_identical)
        self.assertTrue(all(s == REPLAY_UNSUPPORTED_KERNEL for _, s in report.statuses))

    def test_internal_error_propagates_not_masked_as_unsupported(self) -> None:
        # a KeyError/TypeError inside kernel resolution is an INTERNAL bug — it must
        # crash the replay, never be quietly relabelled unsupported_kernel and
        # counted as a non-reproduction
        bundle, traces = self._bundle()
        kernels = {k.version: k for k in support.kernel_registry().kernels}
        with mock.patch(
            "lab_runner.axor_backend.resolve_kernel",
            side_effect=KeyError("internal wiring bug"),
        ), self.assertRaises(KeyError):
            replay_bundle(bundle, traces, kernels)


@unittest.skipUnless(axor_available(), "axor-core not installed")
class TestRealKernelRedactedFailsClosed(unittest.TestCase):
    def _redacted_recipient_events(self) -> list[EmittedEvent]:
        # the driving arg (recipient) is bound to an UNTRUSTED value the client
        # REDACTED — bytes withheld, only a canonical_value_hash pinned
        return [
            EmittedEvent(type="tool_result", tool="read_txns", values=[
                {"value_id": "v_r", "labels": ["untrusted_derived", "sensitive"],
                 "canonical_value_hash": "sha256:" + "0" * 64,
                 "sources": [{"kind": "external_read", "origin_ref": "o"}]},
                {"value_id": "v_a", "decision_value": 1200, "labels": ["untrusted_derived"],
                 "sources": [{"kind": "external_read", "origin_ref": "o"}]},
            ]),
            EmittedEvent(type="tool_call_intent", tool="send_money",
                         arg_bindings={"recipient": "v_r", "amount": "v_a"}),
        ]

    def test_in_process_endpoint_denies_provenance_unavailable(self) -> None:
        condition = _real_condition()
        kernel = resolve_kernel(
            str(condition["kernel"]), support.manifests(), condition.get("policy"),
            KernelRegistry(kernels=()), support.banking_scenario()["inputs"],
        )
        self.assertIsInstance(kernel, AxorKernel)
        trace = assemble_and_gate(
            self._redacted_recipient_events(), condition, support.manifests(),
            support.banking_scenario()["inputs"], kernel, run_id="r", scenario_id="banking-exfil-01",
        )
        decision = next(e for e in trace["events"] if e.get("type") == "gate_decision")
        self.assertEqual(decision["decision"]["verdict"], "DENY")
        self.assertEqual(decision["decision"]["gate"], "provenance_unavailable")

    def test_http_gateway_denies_provenance_unavailable(self) -> None:
        server = make_gateway(
            _real_condition(), support.manifests(), support.banking_scenario()["inputs"],
            scenario_id="banking-exfil-01",
        )
        base = f"http://127.0.0.1:{server.server_address[1]}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def post(path: str, obj: dict, secret: str | None = None) -> dict:
                headers = {"Content-Type": "application/json"}
                if secret:
                    headers["Authorization"] = f"Bearer {secret}"
                req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                             headers=headers, method="POST")
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())

            opened = post("/runs", {})
            rid, secret = opened["run_id"], opened["run_secret"]
            post(f"/runs/{rid}/events", {
                "type": "tool_result", "tool": "read_txns",
                "values": [
                    {"value_id": "v_r", "labels": ["untrusted_derived", "sensitive"],
                     "canonical_value_hash": "sha256:" + "0" * 64,
                     "sources": [{"kind": "external_read", "origin_ref": "o"}]},
                    {"value_id": "v_a", "decision_value": 1200, "labels": ["untrusted_derived"],
                     "sources": [{"kind": "external_read", "origin_ref": "o"}]},
                ],
            }, secret=secret)
            decision = post(f"/runs/{rid}/events", {
                "type": "tool_call_intent", "tool": "send_money",
                "arg_bindings": {"recipient": "v_r", "amount": "v_a"},
            }, secret=secret)["decision"]
            self.assertEqual(decision["verdict"], "DENY")
            self.assertEqual(decision["gate"], "provenance_unavailable")
        finally:
            server.shutdown()
            server.server_close()


class TestInProcessUnknownEventType(unittest.TestCase):
    def test_unknown_emitted_event_type_is_rejected(self) -> None:
        # a mistyped event is NOT silently dropped (which would leave its taint
        # unregistered / its intent ungated — fail open); it raises
        condition = support.conditions()[0]
        kernel = support.kernel_registry().get(str(condition["kernel"]))
        with self.assertRaises(ValueError):
            assemble_and_gate(
                [EmittedEvent(type="tool_reslt", tool="read_txns")],  # typo'd type
                condition, support.manifests(), support.banking_scenario()["inputs"],
                kernel, run_id="r", scenario_id="banking-exfil-01",
            )


class TestGatewayByteQuota(unittest.TestCase):
    def test_run_byte_quota_rejects_oversized_accumulation(self) -> None:
        # a small max_run_bytes is exhausted by a fat value body even though the
        # event COUNT is tiny — the run byte quota returns 429
        server = make_gateway(
            support.conditions()[0], support.manifests(), support.banking_scenario()["inputs"],
            scenario_id="banking-exfil-01", max_run_bytes=2048,
        )
        base = f"http://127.0.0.1:{server.server_address[1]}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def post(path: str, obj: dict, secret: str | None = None):
                headers = {"Content-Type": "application/json"}
                if secret:
                    headers["Authorization"] = f"Bearer {secret}"
                req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                             headers=headers, method="POST")
                try:
                    with urllib.request.urlopen(req) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read())

            _, opened = post("/runs", {})
            rid, secret = opened["run_id"], opened["run_secret"]
            status, body = post(f"/runs/{rid}/events", {
                "type": "tool_result", "tool": "read_txns",
                "values": [{"value_id": "v_big", "decision_value": "z" * 4096,
                            "labels": ["untrusted_derived"],
                            "sources": [{"kind": "external_read", "origin_ref": "o"}]}],
            }, secret=secret)
            self.assertEqual(status, 429)
            self.assertIn("byte quota", body["error"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
