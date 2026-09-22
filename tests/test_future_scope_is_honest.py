"""The unbuilt paid features stay unbuilt, or the list stops being true.

`docs/POST_MVP_PLAN.md` §B10 inventories what the tiers SELL and the code does
not have. A list like that rots the moment one of its rows ships and nobody
edits the doc — and a roadmap that claims a feature is missing after it landed
is the same kind of lie as a plan that grants a capability nothing checks.

So each row carries a search that must come back empty, and this runs them.
When one starts matching, the feature exists: move the row into B9's status
rather than loosening the pattern.

The patterns are deliberately NARROW. The first draft searched for the plain
words and matched prose — "scheduled transactions" in an AgentDojo fixture,
`coverage` under `overage`, the word "compliance" in a comment about the audit
log — which is a check that proves nothing and reads like one that does.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "docs" / "POST_MVP_PLAN.md"

#: the Python packages every check scans — `$PKGS` in the doc
PACKAGES = (
    "lab_analysis", "lab_capabilities", "lab_contracts", "lab_runner",
    "lab_server", "lab_service", "lab_suite", "lab_adapters",
)

#: row id -> (what it would be, regex that must not match)
ABSENT = {
    "B10.1 scheduled CI": r"crontab|APScheduler|schedule\(|_schedule|SCHEDULE",
    "B10.2 approvals": r"(?i)require_approval|approvals?_|n_confirmations|quorum",
    "B10.3 compliance reports": r"(?i)compliance_report|def .*compliance|audit_export|export_audit",
    "B10.4 fleet view": r"(?i)fleet",
    "B10.5 trial metering": r"\boverage\b|trials_used|trial_allowance|\bquota\b",
    "B10.6 SAML / SCIM": r"(?i)\bsaml\b|\bscim\b",
    "B10.7 retention policy / legal hold": r"(?i)legal_hold|retention_policy|retention_policies",
    "B10.8 inter-federation A2A": r"(?i)inter_federation|interfederation|peer_keyset",
}


def _sources() -> list[Path]:
    files: list[Path] = []
    for package in PACKAGES:
        files.extend(sorted((ROOT / package).rglob("*.py")))
    return [f for f in files if "__pycache__" not in f.parts]


class TestTheInventoryIsStillTrue(unittest.TestCase):
    def test_nothing_on_the_list_has_quietly_shipped(self) -> None:
        sources = _sources()
        self.assertGreater(len(sources), 40, "the scan found almost no source — it broke")
        for row, pattern in ABSENT.items():
            with self.subTest(row=row):
                hits = [
                    f"{path.relative_to(ROOT)}:{n}"
                    for path in sources
                    for n, line in enumerate(path.read_text().splitlines(), 1)
                    if re.search(pattern, line)
                ]
                self.assertEqual(
                    hits, [],
                    f"{row} matches now — if the feature shipped, move the row out of "
                    f"§B10 into B9's status; do not widen the pattern",
                )

    def test_saml_and_scim_are_absent_from_the_client_too(self) -> None:
        """SSO is half a client concern, and B10.6's check spans both."""
        web = sorted((ROOT / "web" / "src").rglob("*.ts*"))
        self.assertTrue(web)
        offenders = [
            str(path.relative_to(ROOT)) for path in web
            if re.search(r"(?i)\bsaml\b|\bscim\b", path.read_text())
        ]
        self.assertEqual(offenders, [])


class TestTheDocAndTheTestAgree(unittest.TestCase):
    """Two copies of a list is one copy that drifts."""

    def test_every_row_in_the_doc_is_checked_here(self) -> None:
        body = PLAN.read_text()
        section = body[body.index("## B10 —"):body.index("### The one open decision")]
        in_doc = set(re.findall(r"\| (B10\.\d) \|", section))
        checked = {row.split()[0] for row in ABSENT}
        self.assertEqual(in_doc, checked)

    def test_the_section_still_exists(self) -> None:
        """A rename that loses the section would make every check above vacuous
        while still passing."""
        self.assertIn("## B10 — Paid features declared but not built", PLAN.read_text())


class TestWhatIsBuiltIsNotOnTheList(unittest.TestCase):
    """The guard on the guard: B10 is what is MISSING, so anything B9 actually
    ships must not appear there — a list that hides working features is as
    misleading as one that hides missing ones."""

    def test_the_gates_that_exist_are_not_claimed_absent(self) -> None:
        from lab_server.workspaces import ALL_CAPABILITIES

        body = PLAN.read_text()
        section = body[body.index("## B10 —"):body.index("### The one open decision")]
        for capability in ("hosted_execution", "private_registry", "governance"):
            self.assertIn(capability, ALL_CAPABILITIES)
            self.assertNotIn(f"`{capability}`", section)


if __name__ == "__main__":
    unittest.main()
