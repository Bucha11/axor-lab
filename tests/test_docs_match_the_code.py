"""Every checkable claim in the docs, checked.

Prose drifts silently. Over one sweep the docs turned out to describe three
deleted subsystems as "experimental" twelve lines above the paragraph saying
their packages are gone, name a `validate.py` that does not exist, count 9
schemas where there are 10 and 78 tests where there are 112, list schema FILES
for a trace, a tool manifest and a predicate that have none, point at
`lab_runner/axor_backend.py` after the governance code moved, and route the
Integrations screen at `GET /runtimes/{id}/manifests`, which nothing serves.

None of that is catchable by reading, and all of it is catchable mechanically:
a doc that names a module, a package, a CLI command, a test file, a schema or a
route is making a claim the repo can answer.

HISTORY IS ALLOWED. A doc that records what was deleted has to name it, so a
mention may be excused — but only by an explicit entry here, with the reason.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(
    p for p in ROOT.rglob("*.md")
    if not {"node_modules", ".git"} & set(p.parts)
)

#: Packages deleted in the v0.3 re-scope. The docs that name them say so; the
#: entry is here so a NEW mention in a doc that does not is still a failure.
RETIRED_PACKAGES = {"lab_agent", "lab_endpoint", "lab_entitlement", "lab_games", "lab_sandbox"}

#: Docs whose job is to record history, so they may name retired things.
HISTORICAL = {
    "contracts/architecture-boundary.md",  # its "Removed from Lab" section must
                                           # name the routes it removed
    "docs/REVIEW_RESPONSE.md",          # one row per review round, as answered
    "docs/POST_MVP_PLAN.md",            # B1/B5-B8 carry an explicit RETIRED banner
    "docs/IMPLEMENTATION_PLAN.md",      # the plan as written, with status notes
    "docs/spec-v0.3/CONFORMANCE.md",    # the deletion record itself
    "docs/spec-suite-platform/INTEGRATION_PLAN.md",
    "README.md",                        # says outright that the packages are gone
}

#: Prefixes served by something OTHER than the screen API. Not exemptions from
#: being real — each is a real surface with its own route table — but this test
#: can only read `lab_server`, so it says which server it cannot see and why.
ELSEWHERE = {
    "/api/publications": "the publication server (lab_server/app.py)",
    "/e/": "the publication server (lab_server/app.py)",
    "/identity/": "axor-identity, a separate service Lab verifies tokens from",
}


def _cli_commands() -> set[str]:
    out = subprocess.run([sys.executable, "-m", "lab_runner.cli", "--help"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    found = re.search(r"\{([a-z0-9,\-]+)\}", out)
    return set(found.group(1).split(",")) if found else set()


def _cli_flags(command: str) -> set[str]:
    """The option strings `axor-lab <command> --help` actually offers."""
    out = subprocess.run([sys.executable, "-m", "lab_runner.cli", command, "--help"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    return set(re.findall(r"(--[a-z][a-z-]+)", out))


def _routes() -> tuple[set[str], set[str]]:
    """(normalised literal routes, normalised regex routes) across both servers."""
    literal: set[str] = set()
    pattern: set[str] = set()
    for name in ("runtime_jobs.py", "app.py"):
        source = (ROOT / "lab_server" / name).read_text()
        literal |= {re.sub(r"\{[a-z_]+\}", "{}", r)
                    for r in re.findall(r'path == "(/[^"]*)"', source)}
        literal |= {re.sub(r"\{[a-z_]+\}", "{}", r)
                    for r in re.findall(r'self\.path == "(/[^"]*)"', source)}
        for raw in re.findall(r're\.compile\(rf?"\^([^"]+)\$"\)', source):
            # a negative lookahead is a guard, not a segment: `/suites/(?!...)([..]+)`
            # is still `/suites/{}`, and leaving it in made a real route unfindable
            without_guards = re.sub(r"\(\?[!=][^)]*\)[^)]*\)", "", raw)
            pattern.add(re.sub(r"\([^)]*\)", "{}", without_guards))
    return literal, pattern


class TestTheDocsNameThingsThatExist(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.modules = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
                       if "__pycache__" not in p.parts and "node_modules" not in p.parts}
        cls.packages = {p.name for p in ROOT.iterdir()
                        if p.is_dir() and p.name.startswith("lab_")}
        cls.schemas = {p.name for p in (ROOT / "contracts" / "schemas").glob("*.json")}
        cls.tests = {p.name for p in (ROOT / "tests").glob("*.py")}
        cls.web = {str(p.relative_to(ROOT)) for p in (ROOT / "web" / "src").rglob("*")
                   if p.is_file()}
        cls.commands = _cli_commands()
        cls._flag_cache = {}
        cls.literal, cls.pattern = _routes()

    def _flags(self, command: str) -> set[str]:
        # one subprocess per command, not per mention
        if command not in self.__class__._flag_cache:
            self.__class__._flag_cache[command] = _cli_flags(command)
        return self.__class__._flag_cache[command]

    def _docs(self):
        for doc in DOCS:
            yield doc, str(doc.relative_to(ROOT)), doc.read_text()

    def test_every_named_python_module_exists(self) -> None:
        for doc, name, text in self._docs():
            for ref in sorted(set(re.findall(r"`(lab_[a-z_]+(?:/[a-z_0-9]+)*\.py)`", text))):
                if name in HISTORICAL:
                    continue
                with self.subTest(doc=name, module=ref):
                    self.assertIn(ref, self.modules)

    def test_a_retired_package_is_only_named_where_history_is_recorded(self) -> None:
        for doc, name, text in self._docs():
            for package in sorted(RETIRED_PACKAGES):
                if not re.search(rf"`{package}[/`]", text):
                    continue
                with self.subTest(doc=name, package=package):
                    self.assertIn(
                        name, HISTORICAL,
                        f"{name} names the deleted `{package}`; either stop naming it "
                        f"or add {name!r} to HISTORICAL with the reason",
                    )

    def test_every_cli_command_exists(self) -> None:
        self.assertTrue(self.commands, "could not read the CLI's subcommands")
        for doc, name, text in self._docs():
            for command in sorted(set(re.findall(r"`axor-lab ([a-z][a-z-]+)", text))):
                if name in HISTORICAL:
                    continue
                with self.subTest(doc=name, command=command):
                    self.assertIn(command, self.commands)

    def test_every_flag_the_docs_give_a_command_actually_exists(self) -> None:
        """A command name that exists is a weaker claim than the docs make.

        The real claim is the whole invocation: `axor-lab run-suite ingest-agent
        --trials 20` names a command that exists, a suite that does not, and a
        flag that does not. Someone following the runbook gets `unrecognized
        arguments` and then `no suite`. Checking the command alone passed it.
        """
        for doc, name, text in self._docs():
            if name in HISTORICAL:
                continue
            for line in re.findall(r"axor-lab ([a-z][a-z-]+[^\n`]*)", text):
                command, _, rest = line.partition(" ")
                if command not in self.commands:
                    continue  # the command check above owns that failure
                flags = set(re.findall(r"(--[a-z][a-z-]+)", rest))
                if not flags:
                    continue
                known = self._flags(command)
                for flag in sorted(flags):
                    with self.subTest(doc=name, command=command, flag=flag):
                        self.assertIn(flag, known,
                                      f"`axor-lab {command} {flag}` — no such flag")

    def test_every_suite_the_docs_run_is_in_the_catalog(self) -> None:
        """`run-suite <id>` names a registered suite, or the reader gets
        `no suite 'ingest-agent'` as their first experience of the product."""
        from lab_suite import builtin_registry

        catalog = set(builtin_registry().ids())
        self.assertTrue(catalog, "the built-in registry is empty")
        for doc, name, text in self._docs():
            if name in HISTORICAL:
                continue
            for suite in sorted(set(re.findall(r"axor-lab run-suite ([a-z][a-z0-9-]*)", text))):
                if "/" in suite or suite.endswith(".json"):
                    continue  # a manifest path, not an id
                with self.subTest(doc=name, suite=suite):
                    self.assertIn(suite, catalog)

    def test_every_named_test_file_exists(self) -> None:
        for doc, name, text in self._docs():
            if name in HISTORICAL:
                continue
            for ref in sorted(set(re.findall(r"`(test_[a-z_0-9]+\.py)`", text))):
                with self.subTest(doc=name, test=ref):
                    self.assertIn(ref, self.tests)

    def test_every_named_schema_file_exists(self) -> None:
        for doc, name, text in self._docs():
            for ref in sorted(set(re.findall(r"`([a-z0-9\-]+\.schema\.json)`", text))):
                with self.subTest(doc=name, schema=ref):
                    self.assertIn(
                        ref, self.schemas,
                        "a trace, a tool manifest and a predicate have no schema file "
                        "of their own — the first belongs to axor-core, the other two "
                        "are defined inside the schemas that carry them",
                    )

    def test_every_named_web_file_exists(self) -> None:
        for doc, name, text in self._docs():
            for ref in sorted(set(re.findall(r"`(web/src/[A-Za-z0-9_/.\-]+)`", text))):
                with self.subTest(doc=name, file=ref):
                    self.assertIn(ref, self.web)

    def test_every_quoted_http_route_is_served(self) -> None:
        for doc, name, text in self._docs():
            if name in HISTORICAL:
                continue
            for verb, route in sorted(set(
                re.findall(r"`(GET|POST|PUT|DELETE) (/[A-Za-z0-9_{}/\-]*)`", text)
            )):
                if route.startswith(tuple(ELSEWHERE)):
                    continue
                norm = re.sub(r"\{[a-z_]+\}", "{}", route)
                served = norm in self.literal or any(
                    p.rstrip("$") == norm for p in self.pattern
                )
                with self.subTest(doc=name, route=f"{verb} {route}"):
                    self.assertTrue(served, f"{verb} {route} is served by neither server")


class TestTheWorkflowAgreesWithThePackage(unittest.TestCase):
    """CI is a document about the code, and it drifts the same way prose does.

    The real-kernel job pins axor-core to an EXACT version on purpose — a
    floating range means a green run no longer proves the same kernel the
    evidence was measured against still behaves. That makes the pin a claim,
    and it went stale: `pyproject` moved to `>=0.11,<0.12` while the job stayed
    on 0.10.2, so the one job whose whole purpose is to measure against the real
    kernel was measuring against a version the package declares incompatible.
    """

    def test_the_pinned_kernel_satisfies_what_pyproject_requires(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        pins = set(re.findall(r'pip install "axor-core==([0-9.]+)"', workflow))
        self.assertEqual(len(pins), 1, f"expected exactly one exact pin, got {pins}")
        pinned = tuple(int(p) for p in pins.pop().split("."))

        spec = re.search(r'"axor-core>=([0-9.]+),<([0-9.]+)"',
                         (ROOT / "pyproject.toml").read_text())
        self.assertIsNotNone(spec, "pyproject no longer states an axor-core range")
        low = tuple(int(p) for p in spec.group(1).split("."))
        high = tuple(int(p) for p in spec.group(2).split("."))
        self.assertGreaterEqual(pinned[: len(low)], low, "CI pins below the floor")
        self.assertLess(pinned[: len(high)], high, "CI pins at or above the ceiling")

    def test_every_ecosystem_pin_in_ci_satisfies_what_pyproject_requires(self) -> None:
        """axor-wrap and axor-eval are exact pins for the same reason the kernel
        is, so they are claims that go stale the same way.

        Both were git pins to an integration branch until they cut releases; the
        day they moved to PyPI ranges in `pyproject`, CI's pins became a second
        copy of a number, which is the shape the kernel drift already took once.
        """
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        pyproject = (ROOT / "pyproject.toml").read_text()
        for package in ("axor-wrap", "axor-eval"):
            with self.subTest(package=package):
                pins = set(re.findall(rf'"{package}==([0-9.]+)"', workflow))
                self.assertTrue(pins, f"CI no longer pins {package} exactly")
                spec = re.search(rf'"{package}>=([0-9.]+),<([0-9.]+)"', pyproject)
                self.assertIsNotNone(spec, f"pyproject no longer states a {package} range")
                low = tuple(int(n) for n in spec.group(1).split("."))
                high = tuple(int(n) for n in spec.group(2).split("."))
                for pin in pins:
                    pinned = tuple(int(n) for n in pin.split("."))
                    self.assertGreaterEqual(pinned[: len(low)], low,
                                            f"CI pins {package}=={pin}, below the floor")
                    self.assertLess(pinned[: len(high)], high,
                                    f"CI pins {package}=={pin}, at or above the ceiling")

    def test_no_dependency_is_a_git_pin_anymore(self) -> None:
        """A PEP 508 direct reference cannot be published: PyPI answers
        "400 Can't have direct dependency". One creeping back into `pyproject`
        makes `axor-lab` unpublishable again, and `tools/release_preflight.py`
        would only say so at release time — this says so on every push."""
        deps = (ROOT / "pyproject.toml").read_text().split("dependencies = [", 1)[1]
        deps = deps.split("]", 1)[0]
        self.assertNotIn("git+", deps, "a git pin is back in pyproject dependencies")

    def test_the_drift_assertion_names_the_same_version_as_the_pin(self) -> None:
        """The job also asserts the version at runtime; two copies of a number
        is one copy that goes stale."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        pin = re.search(r'pip install "axor-core==([0-9.]+)"', workflow).group(1)
        asserted = set(re.findall(r"__version__ == '([0-9.]+)'", workflow))
        self.assertEqual(asserted, {pin})


class TestTheCountsAreRight(unittest.TestCase):
    """A number in prose is a claim like any other, and the cheapest to check."""

    def test_the_schema_count_matches(self) -> None:
        count = len(list((ROOT / "contracts" / "schemas").glob("*.json")))
        for doc in DOCS:
            text = doc.read_text()
            for stated in re.findall(r"(\d+) JSON Schemas", text):
                if str(doc.relative_to(ROOT)) in HISTORICAL:
                    continue
                with self.subTest(doc=str(doc.relative_to(ROOT))):
                    self.assertEqual(int(stated), count)

    def test_the_validator_command_the_docs_give_actually_runs(self) -> None:
        """The README used to say `python3 validate.py && python3 validate_slice.py`;
        there is no `validate.py`, and `validate_slice.py` needs the repo root on
        the path and its own directory as the cwd."""
        self.assertFalse((ROOT / "contracts" / "validate.py").exists())
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        done = subprocess.run(
            [sys.executable, "validate_slice.py"],
            cwd=ROOT / "contracts", capture_output=True, text=True, env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        self.assertIn("0 example(s) failing", done.stdout)


if __name__ == "__main__":
    unittest.main()
