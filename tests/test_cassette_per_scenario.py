"""A per-scenario cassette is keyed by scenario name, not task text (r11 P1).

FileCassetteAgent documented a {scenario_name: [records]} format, but
_records_for looked up the TASK TEXT (a long free-form string) which never
matched a scenario_name key — so every scenario silently fell through to the
first dict entry, and two different scenarios could share one transcript while
the results looked deterministic and valid.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_agent.wrapped import FileCassetteAgent
from lab_agent import ProtocolViolation

SINK = support.send_money_manifest()


def _cassette(tmp: str, data) -> FileCassetteAgent:
    path = Path(tmp) / "c.json"
    path.write_text(json.dumps(data))
    return FileCassetteAgent(path=path)


class TestCassettePerScenario(unittest.TestCase):
    def test_two_scenarios_get_their_own_transcripts(self) -> None:
        data = {
            "banking-exfil-01": [{"tool": "send_money", "args": {"recipient": "AAA", "amount": 1}}],
            "banking-exfil-02": [{"tool": "send_money", "args": {"recipient": "BBB", "amount": 2}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            agent = _cassette(tmp, data)
            a = agent.decide_sink_call("some long task text", "read", {}, SINK,
                                       scenario_id="banking-exfil-01")
            b = agent.decide_sink_call("some long task text", "read", {}, SINK,
                                       scenario_id="banking-exfil-02")
            self.assertEqual(a.recipient, "AAA")   # keyed by scenario name, not task
            self.assertEqual(b.recipient, "BBB")   # the two do NOT collapse to one

    def test_missing_scenario_without_default_raises(self) -> None:
        data = {"banking-exfil-01": [{"tool": "send_money", "args": {"recipient": "AAA", "amount": 1}}]}
        with tempfile.TemporaryDirectory() as tmp:
            agent = _cassette(tmp, data)
            with self.assertRaises(ProtocolViolation):
                agent.decide_sink_call("t", "read", {}, SINK, scenario_id="banking-exfil-99")

    def test_default_key_is_used_when_scenario_absent(self) -> None:
        data = {"default": [{"tool": "send_money", "args": {"recipient": "DEF", "amount": 9}}]}
        with tempfile.TemporaryDirectory() as tmp:
            agent = _cassette(tmp, data)
            d = agent.decide_sink_call("t", "read", {}, SINK, scenario_id="anything")
            self.assertEqual(d.recipient, "DEF")

    def test_plain_list_applies_to_every_scenario(self) -> None:
        data = [{"tool": "send_money", "args": {"recipient": "LIST", "amount": 5}}]
        with tempfile.TemporaryDirectory() as tmp:
            agent = _cassette(tmp, data)
            for sid in ("banking-exfil-01", "banking-exfil-02"):
                self.assertEqual(agent.decide_sink_call("t", "r", {}, SINK, scenario_id=sid).recipient, "LIST")


if __name__ == "__main__":
    unittest.main()
