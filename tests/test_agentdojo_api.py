"""The benchmark's HTTP surface: one measurement, two front doors.

The CLI and the web both call `lab_adapters.agentdojo_bench`. These check that
the API is a thin shell over it — same numbers — and that a policy naming
something the suite does not have is refused with the offending name rather
than silently measured as something else.
"""

from __future__ import annotations

import unittest

from lab_adapters import agentdojo_bench as bench
from lab_server import agentdojo_api
from lab_server.errors import PublishRejected


class TestIndex(unittest.TestCase):
    def test_every_suite_is_offered_with_its_declarable_surface(self) -> None:
        status, body = agentdojo_api.handle_index()
        self.assertEqual(status, 200)
        suites = {s["suite"]: s for s in body["suites"]}
        self.assertEqual(set(suites), set(bench.SUITES))
        banking = suites["banking"]
        self.assertEqual(banking["user_tasks"], 16)
        # the candidates are what an operator may declare, not the whole tool
        # list — offering all eleven would bury the six that can ever fire
        self.assertLess(len(banking["secret_candidates"]), len(banking["tools"]))
        self.assertIn("get_balance", banking["secret_candidates"])

    def test_the_dataset_version_is_reported(self) -> None:
        _, body = agentdojo_api.handle_index()
        self.assertTrue(str(body["dataset_version"]).startswith("agentdojo@"))


class TestRun(unittest.TestCase):
    def test_the_api_returns_what_the_shared_measurement_returns(self) -> None:
        """One scoring loop. Two copies would be two sets of numbers."""
        _, body = agentdojo_api.handle_run({})
        direct = bench.matrix()
        self.assertEqual(
            [(r["suite"], r["denials"]) for r in body["rows"]],
            [(r["suite"], r["denials"]) for r in direct],
        )

    def test_travel_is_a_structural_zero_through_the_api_too(self) -> None:
        _, body = agentdojo_api.handle_run({})
        travel = next(r for r in body["rows"] if r["suite"] == "travel")
        self.assertEqual(travel["denials"], 0)

    def test_a_free_secret_declaration_costs_nothing(self) -> None:
        bare = next(r for r in agentdojo_api.handle_run({})[1]["rows"]
                    if r["suite"] == "banking")
        armed = next(
            r for r in agentdojo_api.handle_run(
                {"secrets": {"banking": ["get_balance", "get_iban", "get_user_info"]}}
            )[1]["rows"] if r["suite"] == "banking"
        )
        self.assertEqual(armed["denials"], bare["denials"])
        self.assertEqual(armed["utility"], bare["utility"])

    def test_the_policy_is_echoed_so_a_result_carries_its_own_configuration(self) -> None:
        # a matrix screenshotted without its policy is unreadable a week later
        _, body = agentdojo_api.handle_run({"allowlist": True})
        self.assertTrue(body["policy"]["allowlist"])
        self.assertEqual(body["policy"]["trust_model"], "content-ledger")


class TestValidation(unittest.TestCase):
    def test_a_tool_the_suite_does_not_have_is_refused_by_name(self) -> None:
        """Silently dropping it would report the cost of a policy nobody wrote."""
        with self.assertRaises(PublishRejected) as caught:
            agentdojo_api.handle_run({"secrets": {"banking": ["get_balance", "nope"]}})
        self.assertIn("nope", str(caught.exception))
        self.assertNotIn("get_balance", str(caught.exception))
        self.assertEqual(caught.exception.status, 400)

    def test_an_unknown_suite_names_the_ones_that_exist(self) -> None:
        for call in (lambda: agentdojo_api.handle_sweep({"suite": "nope"}),
                     lambda: agentdojo_api.handle_run({"secrets": {"nope": []}})):
            with self.assertRaises(PublishRejected) as caught:
                call()
            self.assertIn("banking", str(caught.exception))
            self.assertEqual(caught.exception.status, 400)

    def test_malformed_declarations_are_refused_not_coerced(self) -> None:
        for bad in ({"secrets": "banking"}, {"secrets": {"banking": "get_balance"}},
                    {"secrets": {"banking": [1, 2]}}):
            with self.assertRaises(PublishRejected):
                agentdojo_api.handle_run(bad)


class TestSweep(unittest.TestCase):
    def test_the_sweep_separates_free_declarations_from_costly_ones(self) -> None:
        _, body = agentdojo_api.handle_sweep({"suite": "banking"})
        free = {r["source"] for r in body["rows"] if r["cost_pp"] <= 0}
        costly = {r["source"] for r in body["rows"] if r["cost_pp"] > 0}
        self.assertEqual(free, {"get_balance", "get_iban", "get_user_info"})
        self.assertIn("read_file", costly)

    def test_the_combined_figure_is_measured_not_summed(self) -> None:
        """Sources read by the same tasks overlap, so the costs do not add."""
        _, body = agentdojo_api.handle_sweep({"suite": "banking"})
        summed = sum(r["cost_pp"] for r in body["rows"])
        base = body["baseline"]["utility"]
        combined = body["combined"]["utility"]
        actual = (100 * base["governed"] / base["base"]) - (
            100 * combined["governed"] / combined["base"])
        self.assertLess(actual, summed)


if __name__ == "__main__":
    unittest.main()
