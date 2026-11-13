"""The coding-agent plan (Control Plane developer seats) buys nothing extra in
the Lab: an org on it signs in on the free plan, like Community."""
import unittest

from lab_server.workspaces import FREE_PLAN, TIER_ALIASES


class TestCodingTier(unittest.TestCase):
    def test_coding_signs_in_on_the_free_plan(self) -> None:
        self.assertEqual(TIER_ALIASES["coding"], FREE_PLAN)
        self.assertEqual(TIER_ALIASES["community"], FREE_PLAN)


if __name__ == "__main__":
    unittest.main()
