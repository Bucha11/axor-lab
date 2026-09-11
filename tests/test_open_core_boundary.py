"""The open-core / commercial boundary — Suite Platform RFC §16, plan §4.8.

The spec draws a line: the OPEN half is the suite format, the runner, replay,
the artifact and regression formats, and the SDK; the COMMERCIAL half is the
Suite Builder, the hosted workspace, hosted execution, and the registry. That
line is only worth drawing if it is ENFORCED — otherwise one convenient
`from lab_server import ...` inside `lab_suite` makes the open core silently
un-shippable (importing it drags in the whole hosted server), and nothing
catches it until someone tries to `pip install` the core alone.

This is the same discipline `test_capability_boundary.py` applies to governance,
turned on the §16 line: the open core must not import the commercial half. The
DIRECTION is the whole point — commercial depends on open, never the reverse.

Phase 5's entitlement/licensing gate is deliberately NOT here: the plan defers
it until hosted features actually ship, and today the server is a single-process
demo with nothing to gate. What Phase 5 needs NOW is this boundary, so the open
core stays genuinely separable.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The OPEN core (§16): format + runner + replay + regression + SDK + the
# governance capability (a capability of the runner, not the Builder).
OPEN_PACKAGES = (
    "lab_contracts",
    "lab_analysis",
    "lab_suite",
    "lab_runner",
    "lab_capabilities",
    "lab_adapters",
)

# The COMMERCIAL half (§16): the hosted server (workspace + hosted execution +
# screen API + registry storage) and the Suite Builder (web/, not Python). A
# module in the open core that imports either cannot be shipped as open core.
COMMERCIAL_PACKAGES = frozenset({"lab_server"})

# The single declared SEAM: `axor-lab serve` composes the commercial server from
# the open CLI. It is a composition root — like the governance wiring points —
# not spine, and its import of lab_server is lazy (inside the serve command).
# Asserted to be EXACTLY this set below, so a second leak cannot appear quietly.
COMMERCIAL_SEAMS = ("lab_runner/cli.py",)


def _imported_modules(path: Path) -> set[str]:
    """Every module named by an import in this file, at any nesting depth — so an
    import buried in a function body (the usual way a boundary is breached) is
    caught too."""
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


def _touches_commercial(modules: set[str]) -> set[str]:
    hits: set[str] = set()
    for module in modules:
        for commercial in COMMERCIAL_PACKAGES:
            if module == commercial or module.startswith(commercial + "."):
                hits.add(module)
    return hits


def _open_core_files() -> list[Path]:
    files: list[Path] = []
    for package in OPEN_PACKAGES:
        files.extend((REPO_ROOT / package).rglob("*.py"))
    return files


class TestOpenCoreDoesNotImportCommercial(unittest.TestCase):
    def test_no_open_module_imports_the_commercial_half(self) -> None:
        seams = {REPO_ROOT / s for s in COMMERCIAL_SEAMS}
        for path in _open_core_files():
            if path in seams:
                continue
            with self.subTest(module=str(path.relative_to(REPO_ROOT))):
                hits = _touches_commercial(_imported_modules(path))
                self.assertEqual(
                    hits, set(),
                    f"{path.relative_to(REPO_ROOT)} imports the commercial half "
                    f"{sorted(hits)}. The open core must be installable without the "
                    "hosted server; move the code to lab_server, or declare a new "
                    "seam (and justify it) in COMMERCIAL_SEAMS.",
                )

    def test_the_seam_list_is_exactly_the_declared_one(self) -> None:
        """A new open→commercial import must not hide by being added to the seam
        set silently. Every seam is a composition root with a stated reason; the
        set is pinned so a fourth one is a deliberate, reviewed change."""
        actual_seams = set()
        for path in _open_core_files():
            if _touches_commercial(_imported_modules(path)):
                actual_seams.add(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            actual_seams, set(COMMERCIAL_SEAMS),
            "the set of open-core files importing the commercial half changed — "
            "update COMMERCIAL_SEAMS with a justification, or remove the import",
        )

    def test_the_serve_seam_keeps_its_import_lazy(self) -> None:
        """The seam's cost must stay contained: `import lab_runner` (or the CLI)
        must not eagerly pull in the server. The lab_server import lives INSIDE
        the serve command, so merely loading the CLI module does not drag in the
        commercial half."""
        cli = REPO_ROOT / "lab_runner" / "cli.py"
        tree = ast.parse(cli.read_text())
        module_level: set[str] = set()
        for node in tree.body:  # top level only
            if isinstance(node, ast.Import):
                module_level.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                module_level.add(node.module)
        self.assertEqual(
            _touches_commercial(module_level), set(),
            "lab_runner/cli.py imports the commercial half at MODULE level — the "
            "serve command's lab_server import must stay inside the function so "
            "the open CLI loads without the server",
        )


class TestTheOpenCoreLoadsWithoutTheServer(unittest.TestCase):
    def test_importing_the_sdk_does_not_drag_in_the_commercial_half(self) -> None:
        """The shippability property itself: importing the open SDK + replay
        surface must not load `lab_server`. If it does, `pip install axor-lab-core`
        would need the hosted server at import time — the split is a fiction.

        Run in a FRESH interpreter: in this test session other tests have already
        imported lab_server, so a same-process check would be masked. A subprocess
        that imports only the open core is the honest test."""
        import subprocess
        import sys

        program = (
            "import sys; "
            "import lab_suite, lab_runner.bundle_io, lab_contracts, "
            "lab_capabilities.governance; "
            "leaked = [m for m in sys.modules if m.startswith('lab_server')]; "
            "print(','.join(leaked))"
        )
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(), "",
            f"importing the open core pulled in the commercial server: {result.stdout}",
        )


if __name__ == "__main__":
    unittest.main()
