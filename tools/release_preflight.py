"""Refuse a release PyPI would reject — before the tag is spent.

`pypa/gh-action-pypi-publish` is the last step of a release, so everything
wrong with the distribution surfaces there: after the build, after the tests,
after the tag is already pushed. Two of the failures this package can actually
hit are silent until that moment, and neither error message says what to do:

  * **A direct-URL dependency.** PyPI refuses any upload whose metadata carries
    a PEP 508 direct reference (`name @ git+https://...`) with
    `400 Can't have direct dependency: ...`. `pyproject.toml` pins two of them
    (axor-wrap, axor-eval) to integration branches, so a publish today fails on
    upload with nothing published and the tag consumed.

  * **A dependency that is not on PyPI.** `axor-core>=0.11,<0.12` is not a
    direct reference, so the upload succeeds — and then every
    `pip install axor-lab` on a clean machine fails to resolve, because the
    only axor-core on PyPI is older than the floor. A release that installs
    nowhere is worse than no release.

This script runs before the publish leg and fails the workflow on either, with
the remedy in the message. It reads the built wheel's METADATA rather than
`pyproject.toml` — that file is what PyPI validates, and the two can differ
(a dynamic backend, a stale build directory).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Sequence

TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")


def wheel_metadata(wheel: pathlib.Path) -> str:
    """The METADATA of a built wheel — the text PyPI itself validates."""
    with zipfile.ZipFile(wheel) as zf:
        names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise SystemExit(f"{wheel.name}: expected one .dist-info/METADATA, found {names}")
        return zf.read(names[0]).decode("utf-8")


def requires_dist(metadata: str) -> list[str]:
    return [
        line.split(":", 1)[1].strip()
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:")
    ]


def _requirement(spec: str) -> str:
    """The requirement with its environment marker removed."""
    return spec.split(";", 1)[0].strip()


def _marker(spec: str) -> str:
    return spec.split(";", 1)[1].strip() if ";" in spec else ""


def direct_references(specs: Iterable[str]) -> list[str]:
    """Requirements PyPI rejects: a PEP 508 `name @ url` direct reference.

    The marker half can legitimately contain no `@`, and no version specifier
    can, so `@` in the requirement half is exactly the direct-reference form.
    """
    return [spec for spec in specs if "@" in _requirement(spec)]


def runtime_requirements(specs: Iterable[str]) -> list[str]:
    """Requirements a plain `pip install axor-lab` must resolve.

    Extras are excluded: nobody gets them without asking, and a missing extra
    dependency does not break the base install.
    """
    return [
        _requirement(spec)
        for spec in specs
        if "extra ==" not in _marker(spec) and "@" not in _requirement(spec)
    ]


def version_of(pyproject: pathlib.Path) -> str:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def check_tag(tag: str, pyproject: pathlib.Path) -> list[str]:
    m = TAG_RE.fullmatch(tag)
    if not m:
        return [f"tag {tag!r} must match vX.Y.Z"]
    declared = version_of(pyproject)
    if m.group(1) != declared:
        return [f"version mismatch: tag={m.group(1)}, pyproject={declared}"]
    return []


def check_direct_references(specs: Sequence[str]) -> list[str]:
    problems = []
    for spec in direct_references(specs):
        name = re.split(r"[\s@\[]", spec, maxsplit=1)[0]
        problems.append(
            f"{spec}: PyPI rejects a direct-URL dependency "
            f'("400 Can\'t have direct dependency"). Publish {name} to PyPI and '
            f"replace the git pin in pyproject.toml with a version range."
        )
    return problems


def check_resolvable(specs: Sequence[str], *, offline: bool) -> list[str]:
    """Every runtime requirement must be installable from PyPI today.

    `pip download --no-deps` is the cheapest honest probe: it resolves against
    the real index and the running interpreter, so a floor no published version
    satisfies fails here rather than on a user's machine.
    """
    if offline:
        return []
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        for req in specs:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "download", "--no-deps", "--dest", tmp, req],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()
                detail = tail[-1] if tail else f"pip exited {proc.returncode}"
                problems.append(
                    f"{req}: does not resolve from PyPI, so `pip install axor-lab` "
                    f"would fail on a clean machine — {detail}"
                )
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist", help="directory holding the built wheel")
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--tag", default="", help="release tag; skipped when empty")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the PyPI resolve probe (for tests, not for a release)",
    )
    args = parser.parse_args(argv)

    dist = pathlib.Path(args.dist)
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        print(f"::error::expected exactly one wheel in {dist}/, found {[w.name for w in wheels]}")
        return 1
    if not sorted(dist.glob("*.tar.gz")):
        print(f"::error::no sdist in {dist}/ — publish both, or `pip install --no-binary` breaks")
        return 1

    specs = requires_dist(wheel_metadata(wheels[0]))
    problems: list[str] = []
    if args.tag:
        problems += check_tag(args.tag, pathlib.Path(args.pyproject))
    problems += check_direct_references(specs)
    problems += check_resolvable(runtime_requirements(specs), offline=args.offline)

    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} blocker(s): this release would not install. Nothing published.")
        return 1
    print(f"preflight clean: {wheels[0].name} declares {len(specs)} dependency line(s), "
          "all resolvable from PyPI, none a direct URL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
