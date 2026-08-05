"""Governance is a capability, and this is what makes that true.

Suite Platform RFC §10 says governance is optional. Moving files into a
`lab_capabilities/` directory does not make it optional — only an enforced
dependency direction does. Without a test like this, one convenient import puts
governance back in the spine and nobody notices until a governance-free run
needs a kernel registry.

Two rules:

  1. The platform SPINE must not import governance. A module that runs every
     experiment cannot depend on machinery only some experiments use.
  2. The direction is one-way. Governance reaches into the platform; the
     platform never reaches back.

The check reads the source's import statements rather than inspecting loaded
modules, so an import buried in a function body is caught too — that is exactly
where an accidental dependency would hide.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules that run for EVERY experiment, governed or not.
SPINE = (
    "lab_runner/loop.py",
    "lab_runner/invariants.py",
    "lab_runner/predicates.py",
    "lab_runner/ledger.py",
    "lab_runner/simulator.py",
    "lab_runner/agents.py",
    "lab_contracts/artifact.py",
    "lab_contracts/bundle.py",
    "lab_contracts/canonical.py",
    "lab_suite/manifest.py",
    "lab_suite/sdk.py",
)

# The governance capability: gates, kernels, verdicts, replay, the CP bridge.
GOVERNANCE_MODULES = frozenset({
    "lab_capabilities",
    "lab_runner.kernel",
    "lab_runner.axor_backend",
    "lab_runner.replay",
    "lab_runner.evidence",
    "lab_runner.regression",
    "lab_runner.cp_export",
    "lab_runner.claims",
})

# The ONE place the platform is allowed to wire a gate in: suite execution has
# to construct the capability a suite asked for. Keeping it to a single file is
# what makes the dependency reviewable.
WIRING_POINTS = ("lab_suite/execute.py",)


def _imported_modules(path: Path) -> set[str]:
    """Every module named by an import in this file, at any nesting depth."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import stays inside its own package
                continue
            if node.module:
                found.add(node.module)
    return found


def _touches_governance(modules: set[str]) -> set[str]:
    hits: set[str] = set()
    for module in modules:
        for governed in GOVERNANCE_MODULES:
            if module == governed or module.startswith(governed + "."):
                hits.add(module)
    return hits


class TestSpineIsGovernanceFree(unittest.TestCase):
    def test_no_spine_module_imports_governance(self) -> None:
        for relative in SPINE:
            with self.subTest(module=relative):
                path = REPO_ROOT / relative
                self.assertTrue(path.is_file(), f"{relative} moved — update this test")
                hits = _touches_governance(_imported_modules(path))
                self.assertEqual(
                    hits, set(),
                    f"{relative} imports governance {sorted(hits)}; the platform spine "
                    "runs for every experiment and cannot depend on a capability only "
                    "some experiments use",
                )

    def test_the_loop_names_no_kernel_machinery(self) -> None:
        """The load-bearing case. The loop takes a `Gate` — something that may
        return a decision — and nothing more.

        Checked as IDENTIFIERS, not as text: the loop still reads
        `condition["kernel"]` to decide whether to stamp producer.kernel_version,
        and that is a dict key it copies, not a dependency it has. What must not
        appear is a reference to the machinery itself.
        """
        tree = ast.parse((REPO_ROOT / "lab_runner" / "loop.py").read_text())
        used = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        forbidden = {"Kernel", "AxorKernel", "KernelRegistry", "resolve_kernel",
                     "gate_with_governor", "default_registry", "decide_sink_call"}
        self.assertEqual(
            used & forbidden, set(),
            "lab_runner/loop.py references kernel machinery; the general loop must "
            "know only the Gate protocol",
        )

    def test_governance_wiring_lives_in_one_place(self) -> None:
        """lab_suite must reach the capability from exactly one file, so the
        dependency is reviewable rather than scattered."""
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / "lab_suite").rglob("*.py")):
            relative = str(path.relative_to(REPO_ROOT))
            if relative in WIRING_POINTS:
                continue
            if _touches_governance(_imported_modules(path)):
                offenders.append(relative)
        self.assertEqual(offenders, [], f"governance imported outside {WIRING_POINTS}")


class TestDependencyDirectionIsOneWay(unittest.TestCase):
    def test_the_platform_never_imports_lab_capabilities(self) -> None:
        """Governance reaches into the platform, never the other way round. A
        back-reference would make the two packages one package with extra
        directories."""
        offenders: list[str] = []
        # `lab_agent` used to be listed here and was deleted a while ago;
        # `rglob` on a missing directory yields nothing, so the entry passed
        # vacuously and the package that actually replaced it — `lab_suite` —
        # went unchecked. A guard list is only as good as the names in it.
        for package in ("lab_runner", "lab_contracts", "lab_analysis", "lab_suite"):
            self.assertTrue(
                (REPO_ROOT / package).is_dir(), f"{package} does not exist",
            )
            for path in sorted((REPO_ROOT / package).rglob("*.py")):
                relative = str(path.relative_to(REPO_ROOT))
                if relative in WIRING_POINTS:
                    continue  # the one declared place a gate is constructed
                modules = _imported_modules(path)
                if any(m == "lab_capabilities" or m.startswith("lab_capabilities.")
                       for m in modules):
                    offenders.append(relative)
        self.assertEqual(offenders, [])

    def test_the_capability_is_importable_and_wired(self) -> None:
        from lab_capabilities.governance import KernelGate, gate_for_condition
        self.assertTrue(callable(gate_for_condition))
        self.assertTrue(hasattr(KernelGate, "decide"))


class TestAGovernanceFreeRunEnforcesNothing(unittest.TestCase):
    """What "governance-free" means is NO ENFORCEMENT, not no kernel.

    An earlier version asserted the kernel resolver was never called. That
    encoded the wrong goal: an unobserved run has no value ledger, no
    replayable verdicts, and describes an agent that was never wrapped. The
    kernel observes; only the gates are off.
    """

    def test_a_conditionless_suite_observes_but_never_denies(self) -> None:
        from lab_suite import builtin_registry, run_suite

        suite = builtin_registry().get("budget")
        run = run_suite(suite.manifest(), run_id="r_free", suite=suite)
        self.assertTrue(run.trials)
        self.assertEqual(str(run.conditions[0]["enforcement"]), "off")

        verdicts = [
            str(e["decision"]["verdict"]) for trace in run.traces.values()
            for e in trace["events"]  # type: ignore[union-attr]
            if e.get("type") == "gate_decision"
        ]
        self.assertTrue(verdicts, "the kernel still observes every call")
        self.assertNotIn("DENY", verdicts, "enforcement is off — nothing is denied")

    def test_a_governed_suite_does_resolve_one(self) -> None:
        """The converse, so the test above cannot pass by the capability simply
        being broken."""
        import lab_capabilities.governance.gate as gate_module
        from lab_suite import builtin_registry, run_suite

        calls: list[object] = []
        original = gate_module.resolve_kernel

        def spy(*args: object, **kwargs: object) -> object:
            calls.append(args)
            return original(*args, **kwargs)

        gate_module.resolve_kernel = spy  # type: ignore[assignment]
        try:
            suite = builtin_registry().get("agentdojo")
            run_suite(suite.manifest(), run_id="r_gov", suite=suite)
        finally:
            gate_module.resolve_kernel = original  # type: ignore[assignment]

        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
