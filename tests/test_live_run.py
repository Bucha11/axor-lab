"""Paid runs from the browser — what has to hold before any money moves.

Comparing the same conditions across several models is a real Lab question, and
the one the connected-runtime path does not answer: that path is about *your*
agent, singular, and needs an integration. So live runs exist — but `/runs/local`
still refuses to spend anything, and this separate surface carries the CLI's
safeguards rather than waiving them.

These tests are the guard layer, which is what the web adds. The provider loop
itself is covered by the repo's cassette-driven BYOK tests; nothing here calls a
provider, and nothing here needs a key that works.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from lab_server import make_runtime_server
from lab_server.live_run import MAX_LIVE_TRIALS, LiveRunRefused, plan

_SELECTION = {"compose": {"suite": "banking", "repeats": 1}}
_AGENT = {"provider": "anthropic", "model": "claude-sonnet-5"}
_BUDGET = {"max_usd": 2.0}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.server: ThreadingHTTPServer = make_runtime_server(
            host="127.0.0.1", port=0, control_token=None,
        )
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _call(self, path: str, body: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _plan(self, **over: object) -> dict:
        body = {**_SELECTION, "agent": _AGENT, "budget": _BUDGET, **over}
        status, planned = self._call("/runs/live/plan", body)
        self.assertEqual(status, 200, planned)
        return planned


class BudgetIsRequiredTest(_Base):
    def test_an_unbounded_live_run_is_refused(self) -> None:
        # The CLI may run unbounded because a human is watching the terminal.
        # Nothing in an HTTP request plays that role.
        status, body = self._call("/runs/live/plan", {**_SELECTION, "agent": _AGENT})
        self.assertEqual(status, 400)
        self.assertIn("budget", body["error"])

    def test_a_zero_ceiling_is_not_a_budget(self) -> None:
        # `max_usd: 0` reads as "spend nothing" but is either a no-op or a
        # mistake; accepting it would stop the run before any trial while looking
        # like a successful empty run.
        status, _ = self._call(
            "/runs/live/plan", {**_SELECTION, "agent": _AGENT, "budget": {"max_usd": 0}},
        )
        self.assertEqual(status, 400)

    def test_a_model_is_required(self) -> None:
        status, _ = self._call(
            "/runs/live/plan",
            {**_SELECTION, "agent": {"provider": "anthropic"}, "budget": _BUDGET},
        )
        self.assertEqual(status, 400)

    def test_an_unsupported_provider_is_refused(self) -> None:
        status, _ = self._call("/runs/live/plan", {
            **_SELECTION, "agent": {"provider": "openai", "model": "gpt-5"},
            "budget": _BUDGET,
        })
        self.assertEqual(status, 400)


class EstimateTest(_Base):
    def test_the_plan_prices_the_run_and_says_what_binds(self) -> None:
        planned = self._plan()
        self.assertGreater(planned["trials"], 0)
        self.assertIn("usd", planned["estimate"])
        # token ceilings are hard; the dollar figure is not, and says so rather
        # than being discovered from an invoice
        self.assertTrue(planned["ceilings"]["usd_is_best_effort"])
        self.assertIn("illustrative price table", planned["ceilings"]["note"])

    def test_it_warns_that_a_live_run_cannot_be_paired(self) -> None:
        # A live model samples each condition independently, so McNemar does not
        # apply. Saying so at plan time stops someone paying for a paired study
        # they were never going to get.
        planned = self._plan()
        self.assertEqual(planned["comparison_design"], "independent_samples")
        self.assertIn("never a paired McNemar", planned["design_note"])

    def test_an_oversized_live_run_is_refused(self) -> None:
        # 3 scenarios × 2 conditions × 50 repeats = 300 trials, over the live
        # ceiling but within what the composer itself allows — so this reaches the
        # live guard rather than tripping the composer's repeats bound first.
        status, body = self._call("/runs/live/plan", {
            "compose": {"suite": "banking", "repeats": 50},
            "agent": _AGENT, "budget": _BUDGET,
        })
        self.assertEqual(status, 413, body)
        self.assertIn(str(MAX_LIVE_TRIALS), body["error"])


class ConfirmTokenTest(_Base):
    def test_spending_without_confirming_is_refused(self) -> None:
        status, body = self._call(
            "/runs/live", {**_SELECTION, "agent": _AGENT, "budget": _BUDGET, "api_key": "sk-x"},
        )
        self.assertEqual(status, 428)
        self.assertIn("costs money", body["error"])

    def test_a_forged_token_is_refused(self) -> None:
        status, _ = self._call("/runs/live", {
            **_SELECTION, "agent": _AGENT, "budget": _BUDGET,
            "confirm_token": "sha256:" + "0" * 64, "api_key": "sk-x",
        })
        self.assertEqual(status, 409)

    def test_a_token_does_not_authorise_a_bigger_run(self) -> None:
        """The approval commits to what it approved.

        Without binding, an operator shown a 6-trial estimate could confirm it and
        have a 120-trial run execute on their card.
        """
        planned = self._plan()
        status, body = self._call("/runs/live", {
            "compose": {"suite": "banking", "repeats": 20},
            "agent": _AGENT, "budget": _BUDGET,
            "confirm_token": planned["confirm_token"], "api_key": "sk-x",
        })
        self.assertEqual(status, 409)
        self.assertIn("different experiment", body["error"])

    def test_a_token_does_not_authorise_a_raised_ceiling(self) -> None:
        planned = self._plan()
        status, _ = self._call("/runs/live", {
            **_SELECTION, "agent": _AGENT, "budget": {"max_usd": 500.0},
            "confirm_token": planned["confirm_token"], "api_key": "sk-x",
        })
        self.assertEqual(status, 409)

    def test_a_token_does_not_authorise_a_different_model(self) -> None:
        planned = self._plan()
        status, _ = self._call("/runs/live", {
            **_SELECTION, "agent": {"provider": "anthropic", "model": "claude-opus-4-8"},
            "budget": _BUDGET,
            "confirm_token": planned["confirm_token"], "api_key": "sk-x",
        })
        self.assertEqual(status, 409)

    def test_a_confirmed_run_still_needs_a_key(self) -> None:
        planned = self._plan()
        status, body = self._call("/runs/live", {
            **_SELECTION, "agent": _AGENT, "budget": _BUDGET,
            "confirm_token": planned["confirm_token"],
        })
        self.assertEqual(status, 400)
        self.assertIn("api_key", body["error"])


class FailedRunHonestyTest(_Base):
    def test_a_run_where_everything_failed_is_not_a_completed_run(self) -> None:
        """A rejected key must not land as a successful empty run.

        The runner marks each trial failed and carries on, so without this guard
        the surface answered 201 with zero traces — reporting success for a run
        that measured nothing.
        """
        planned = self._plan()
        status, body = self._call("/runs/live", {
            **_SELECTION, "agent": _AGENT, "budget": _BUDGET,
            "confirm_token": planned["confirm_token"], "api_key": "sk-definitely-invalid",
        })
        self.assertEqual(status, 502, body)
        self.assertIn("nothing was measured", body["error"])
        # and it names the runner's own reason rather than a generic failure
        self.assertTrue(len(body["error"]) > 60)


class KeyHandlingTest(unittest.TestCase):
    def test_the_key_is_never_part_of_what_binds_the_token(self) -> None:
        # The token commits to the experiment, model and budget — never to the
        # key, so rotating a key does not invalidate an approved estimate, and a
        # token can never be a channel for leaking one.
        from lab_server.live_run import _budget, _confirm_token, _resolve_document

        document = _resolve_document(dict(_SELECTION))
        budget = _budget(dict(_BUDGET))
        token = _confirm_token(document, "claude-sonnet-5", budget)
        self.assertTrue(token.startswith("sha256:"))
        self.assertNotIn("sk-", json.dumps({"token": token}))

    def test_plan_never_asks_for_a_key(self) -> None:
        # Pricing must not require a credential: you should be able to see what a
        # run costs before deciding to hand anything over.
        planned = plan({**_SELECTION, "agent": _AGENT, "budget": _BUDGET})
        self.assertIn("confirm_token", planned)

    def test_refusals_carry_a_status_the_surface_can_use(self) -> None:
        with self.assertRaises(LiveRunRefused) as caught:
            plan({**_SELECTION, "agent": _AGENT})
        self.assertEqual(caught.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
