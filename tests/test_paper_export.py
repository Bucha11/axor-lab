"""The run, as something you can paste into a paper.

Every export door this product had handed over JSON: `artifact/v1`, a
reproduction package, a CP handoff directory. All correct, and none of them
what someone writing a paper needs — nobody pastes a bundle into a results
section, so the numbers were retyped by hand out of a JSON viewer. Retyping is
where a figure quietly stops matching its evidence.

These pin the parts a manuscript actually depends on: that the table compiles,
that it does not launder a self-reported latency as a derived rate, that a
Wilson bound of 1.39e-17 never reaches a page, that a p-value is never printed
as zero, and that a run with only one arm is not described as a paired
comparison.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support
from lab_service import REPORT_FORMATS, ReportError, build_paper_report
from lab_suite import builtin_registry
from lab_suite.execute import run_suite
from lab_suite.sdk import comparison_design

CREATED = "2026-09-13T00:00:00+00:00"


def _artifact(suite_id: str) -> dict[str, object]:
    suite = builtin_registry().get(suite_id)
    run = run_suite(suite.manifest(), run_id=f"r_{suite_id}")
    environment = dict(support.environment())
    environment["experiment_design"] = comparison_design(suite)
    return run.artifact(f"a_{suite_id}", CREATED, environment)


class TestTheResultsTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_paper_report(_artifact("ingest"))

    def test_every_format_renders(self) -> None:
        for fmt in REPORT_FORMATS:
            with self.subTest(format=fmt):
                self.assertTrue(self.report.render(fmt).strip())

    def test_the_table_carries_every_arm(self) -> None:
        for arm in ("ungoverned", "governed", "governed_allowlist"):
            self.assertIn(arm, self.report.markdown)

    def test_the_comparison_is_written_as_a_sentence(self) -> None:
        """A results section says "McNemar's exact test over 24 matched pairs,
        p < 0.001", not `{"name": "mcnemar", "discordant": {"b": 17}}`."""
        self.assertIn("McNemar", self.report.markdown)
        self.assertIn("b=17", self.report.markdown)
        self.assertIn("p < 0.001", self.report.markdown)

    def test_a_p_value_is_never_printed_as_zero(self) -> None:
        """`p = 0.000` is the classic table lie — it reads as impossible rather
        than small."""
        self.assertNotIn("p = 0.000", self.report.markdown)
        self.assertNotIn("p = 0.000", self.report.latex)

    def test_the_methods_paragraph_carries_what_was_pinned(self) -> None:
        """The values an author would otherwise transcribe: the kernel pin, the
        per-arm config hash, the design, the denominator."""
        methods = self.report.markdown.split("## Methods")[1]
        self.assertIn("axor-core@", methods)
        self.assertIn("sha256:", methods)
        self.assertIn("matched pairs", methods)
        self.assertIn("n=108/108", methods)


class TestTheLatexCompiles(unittest.TestCase):
    """Not "looks right" — the specific characters that break a build."""

    def setUp(self) -> None:
        self.latex = build_paper_report(_artifact("ingest")).latex

    def test_underscores_in_identifiers_are_escaped(self) -> None:
        """`governed_allowlist` and `task_success` are full of underscores, and
        an unescaped one is a manuscript that does not compile — which makes a
        generated table worse than retyping it."""
        self.assertIn(r"governed\_allowlist", self.latex)
        self.assertIn(r"task\_success", self.latex)
        # and no BARE underscore survived anywhere in the file
        for index, char in enumerate(self.latex):
            if char == "_":
                self.assertEqual(self.latex[index - 1], "\\", f"bare _ at {index}")

    def test_the_table_is_a_complete_float(self) -> None:
        for token in (r"\begin{table}", r"\toprule", r"\midrule", r"\bottomrule",
                      r"\end{tabular}", r"\caption{", r"\label{", r"\end{table}"):
            self.assertIn(token, self.latex)

    def test_it_names_the_package_it_needs(self) -> None:
        """booktabs is not in a default preamble. Saying so in the file beats a
        reader discovering it from an error."""
        self.assertIn("booktabs", self.latex)


class TestTheHonestColumn(unittest.TestCase):
    """The reason this is worth generating rather than writing by hand: a
    latency mean and an attack-success rate look identical in a results table
    and are not the same kind of claim. The artifact is the only place that
    distinction survives."""

    def setUp(self) -> None:
        self.report = build_paper_report(_artifact("budget"))

    def test_a_derived_rate_and_a_reported_mean_are_labelled_differently(self) -> None:
        rows = [
            line for line in self.report.markdown.splitlines()
            if line.startswith("| `")
        ]
        by_metric = {line.split("`")[1]: line for line in rows}
        self.assertIn("derived", by_metric["task_success"])
        self.assertIn("self-reported", by_metric["duration_ms"])

    def test_an_observed_range_is_not_offered_as_a_confidence_interval(self) -> None:
        self.assertIn("not a confidence interval", self.report.markdown)
        self.assertIn("†", self.report.markdown)
        # the derived rate's Wilson interval carries NO dagger
        rate_row = next(l for l in self.report.markdown.splitlines()
                        if l.startswith("| `task_success`"))
        self.assertNotIn("†", rate_row)

    def test_a_scripted_stand_in_is_declared_as_one(self) -> None:
        """The caveat that matters most and is easiest to omit: these numbers
        describe the harness, not a production model."""
        self.assertTrue(any("scripted stand-in" in c for c in self.report.caveats))

    def test_a_single_arm_run_is_not_described_as_a_paired_comparison(self) -> None:
        """The design block says matched_pairs because it describes the AGENT.
        Repeating that for a one-arm run would describe a pairwise comparison of
        one arm against nothing."""
        methods = self.report.markdown.split("## Methods")[1]
        self.assertIn("Only one arm ran", methods)
        self.assertNotIn("compared pairwise", methods)


class TestFloatNoiseNeverReachesAPage(unittest.TestCase):
    def test_a_zero_rate_prints_as_zero(self) -> None:
        """A Wilson bound for a zero rate comes back as 1.39e-17. That is
        exactly the arithmetic and it does not belong in a table: printed raw it
        reads as a bug, and an author retypes the row by hand."""
        report = build_paper_report(_artifact("ingest"))
        self.assertNotIn("e-17", report.markdown)
        self.assertNotIn("e-17", report.latex)
        self.assertIn("[0.000, 0.138]", report.markdown)


class TestTheCitation(unittest.TestCase):
    def test_an_unpublished_artifact_says_it_has_no_location(self) -> None:
        """It still gets an entry — cite the hash — but a reader is told there
        is nowhere to resolve it, rather than handed a URL that does not exist."""
        bib = build_paper_report(_artifact("budget")).bibtex
        self.assertIn("@misc{axorlab:", bib)
        self.assertIn("NOT PUBLISHED", bib)
        self.assertNotIn("url", bib)

    def test_a_published_artifact_cites_the_immutable_record(self) -> None:
        artifact = _artifact("ingest")
        publication = {
            "publication_id": "e_abc123def",
            "question": "Does taint enforcement contain attachment-borne exfiltration?",
            "statistics_integrity": "recomputed_from_traces",
        }
        report = build_paper_report(
            artifact, publication=publication, url="https://lab.example/e/e_abc123def")
        self.assertIn("@misc{axorlab:e_abc123", report.bibtex)
        self.assertIn("https://lab.example/e/e_abc123def", report.bibtex)
        self.assertIn("recomputed_from_traces", report.bibtex)
        self.assertNotIn("NOT PUBLISHED", report.bibtex)
        # the question becomes the title — it is what the run ANSWERS
        self.assertIn("attachment-borne exfiltration", report.markdown.splitlines()[0])


class TestARunThatMeasuredNothing(unittest.TestCase):
    def test_it_is_refused_with_a_reason(self) -> None:
        """An answer, not a crash: the user asked a reasonable question of an
        artifact that cannot answer it."""
        artifact = _artifact("budget")
        artifact["bundle"]["aggregates"] = []  # type: ignore[index]
        with self.assertRaises(ReportError) as caught:
            build_paper_report(artifact)
        self.assertIn("no aggregates", str(caught.exception))


class TestTheCLI(unittest.TestCase):
    def test_it_writes_all_three_files_from_an_artifact(self) -> None:
        from lab_runner.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "artifact.json"
            source.write_text(json.dumps(_artifact("ingest")))
            out = Path(tmp) / "paper"
            self.assertEqual(
                main(["report", str(source), "--format", "all", "--out", str(out)]), 0)
            self.assertEqual(
                sorted(p.name for p in out.iterdir()),
                ["report.bib", "report.md", "report.tex"],
            )

    def test_it_reads_a_bundle_directory_too(self) -> None:
        """"Which of the three shapes do I have" is not a question worth making
        someone answer to get a table."""
        from lab_runner.bundle_io import write_bundle_dir
        from lab_runner.cli import main

        suite = builtin_registry().get("ingest")
        run = run_suite(suite.manifest(), run_id="r_dir")
        environment = dict(support.environment())
        environment["experiment_design"] = comparison_design(suite)
        bundle = run.bundle("b_dir", CREATED, environment)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "bundle"
            write_bundle_dir(directory, bundle, run.traces)
            out = Path(tmp) / "paper"
            self.assertEqual(
                main(["report", str(directory), "--format", "tex", "--out", str(out)]), 0)
            self.assertIn(r"\toprule", (out / "report.tex").read_text())


if __name__ == "__main__":
    unittest.main()
