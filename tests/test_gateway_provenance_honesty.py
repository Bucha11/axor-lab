"""The HTTP gateway does not let an untrusted client self-certify provenance
(review round 8, P1).

The gateway used to default labels_carried=True and stamp the trace
provenance_fidelity="explicit_flow_tracked" unless the client volunteered
labels_carried:false. That made a governance-grade claim ("we tracked the
explicit information flow") out of labels the untrusted agent simply asserted.

Now an untrusted client is always heuristic_attribution; explicit_flow_tracked
is granted only when the OPERATOR constructs the gateway as an attested
trusted_runtime, and even then the client's labels_carried can only downgrade.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request

from tests import support
from lab_endpoint import make_gateway


def _drive(server, *, labels_carried=None):
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
        result_event = {"type": "tool_result", "tool": "read_txns",
                        "values": [{"value_id": "v_r", "decision_value": support.ATTACKER_IBAN,
                                    "preview": support.ATTACKER_IBAN, "labels": ["untrusted_derived"],
                                    "sources": [{"kind": "external_read", "origin_ref": "o"}]}]}
        if labels_carried is not None:
            result_event["labels_carried"] = labels_carried
        post(f"/runs/{run_id}/events", result_event, secret=secret)
        post(f"/runs/{run_id}/finalize", {}, secret=secret)
        req = urllib.request.Request(base + f"/runs/{run_id}/trace",
                                     headers={"Authorization": f"Bearer {secret}"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    finally:
        server.shutdown()
        server.server_close()


class TestGatewayProvenanceHonesty(unittest.TestCase):
    def _gateway(self, **kw):
        return make_gateway(
            support.conditions()[1], support.manifests(),
            support.banking_scenario()["inputs"], scenario_id="banking-exfil-01", **kw,
        )

    def test_untrusted_client_is_heuristic_by_default(self) -> None:
        trace = _drive(self._gateway())
        self.assertEqual(trace["producer"]["provenance_fidelity"], "heuristic_attribution")

    def test_client_cannot_self_certify_explicit_flow_tracked(self) -> None:
        # even if the client explicitly claims labels_carried: true, an untrusted
        # gateway must NOT promote it to explicit_flow_tracked
        trace = _drive(self._gateway(), labels_carried=True)
        self.assertEqual(trace["producer"]["provenance_fidelity"], "heuristic_attribution")

    def test_operator_attested_runtime_may_be_explicit(self) -> None:
        trace = _drive(self._gateway(trusted_runtime=True))
        self.assertEqual(trace["producer"]["provenance_fidelity"], "explicit_flow_tracked")

    def test_trusted_runtime_client_can_still_downgrade(self) -> None:
        # labels_carried:false always downgrades, even for a trusted runtime
        trace = _drive(self._gateway(trusted_runtime=True), labels_carried=False)
        self.assertEqual(trace["producer"]["provenance_fidelity"], "heuristic_attribution")


if __name__ == "__main__":
    unittest.main()
