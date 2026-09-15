# Releasing `axor-lab`

`.github/workflows/release.yml` builds and publishes `axor-lab` to PyPI when a
`vX.Y.Z` tag is pushed. Publishing is credential-free — **PyPI Trusted
Publishing (OIDC)**, the same arrangement the Control Plane uses — so no API
token ever lives in this repo. A test asserts that
(`tests/test_release_preflight.py::TestTheWorkflowWiring`).

This is the one-time setup plus the per-release procedure.

## The blocker that used to be here — cleared

`pyproject.toml` pinned `axor-wrap` and `axor-eval` to git branches, because
neither had cut a release carrying the API Lab imports. PyPI **rejects** any
upload whose metadata holds a PEP 508 direct reference:

```
400 Bad Request — Invalid value for requires_dist.
Error: Can't have direct dependency: 'axor-wrap@ git+https://…'
```

`axor-wrap 0.2.0` and `axor-eval 0.2.0` are on PyPI now, and both carry every
symbol Lab imports (`axor_wrap.WrappedToolset` / `ToolDenied` /
`toolset_for_arm` / `trial_of` / `LabRuntimeConnector` / `compile_manifests`,
`axor_wrap.trace.verdict_events`, `axor_wrap.compile.governor_kwargs`;
`axor_eval.claims.reconstruct_claim`, `axor_eval.audit.tool_audit`,
`axor_eval.contracts`, `axor_eval.deprivation.engine`). Both dependencies are
version ranges now (`>=0.2,<0.3`), and the full suite — 1240 tests — runs green
against the published wheels with no wrap/eval skip.

So `axor-lab` is publishable. What is left is the one-time PyPI setup below;
nothing in the package blocks a release.

The gate stays either way. `tools/release_preflight.py` fails the workflow if a
direct reference comes back, and `tests/test_docs_match_the_code.py` fails on
every push if one reappears in `pyproject` — a release-time check is too late
to be the only one.

## One-time setup

### 1. Create the `pypi` environment

The publish job runs in a GitHub environment named `pypi`. In the repo:
**Settings → Environments → New environment → `pypi`**. Add a required reviewer
if you want a tag to need a human before it can publish.

Only one package ships from this repo, so unlike the Control Plane there is no
per-package environment split — that one exists because PyPI keys a *pending*
trusted publisher on (owner, repo, workflow file, environment) and two packages
sharing an environment collide.

### 2. Register the Trusted Publisher on PyPI

On pypi.org: **Publishing → Add a pending publisher** (this creates the project
on first publish).

| Field | Value |
|---|---|
| PyPI Project Name | `axor-lab` |
| Owner | `Bucha11` |
| Repository name | `axor-lab` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

`Workflow name` is the **filename** (`release.yml`), not the workflow's `name:`
field (`Release`). The environment name must match the job's `environment:`
exactly, or PyPI rejects the OIDC token.

## Rehearse without spending a tag

`workflow_dispatch` on a branch is a **dry run**: it builds, runs `twine check`,
does the clean-room install and runs the preflight gate, and then skips the
publish leg because the ref is not a tag (`if: github.ref_type == 'tag'`). Use
it from the Actions tab whenever you want to know whether a release *would*
work. It comes back clean today.

You can do the same locally:

```bash
python -m build
python tools/release_preflight.py --dist dist --tag v0.1.0
```

## Cut a release

```bash
# 1. Bump `version` in pyproject.toml. The tag must match it exactly;
#    the preflight refuses the run otherwise.

# 2. Make sure main is green.
python -m unittest discover -s tests -t . -v
ruff check lab_contracts lab_runner lab_analysis lab_adapters \
           lab_server lab_service lab_suite lab_capabilities tools

# 3. Tag and push. The tag drives the workflow.
git tag v0.1.0
git push origin v0.1.0
```

The `Release` workflow then, in order:

1. builds the sdist + wheel;
2. `twine check`s them (the long description has to render on the project page);
3. **installs the wheel into a fresh venv and runs it from `/`** — with no
   `AXOR_LAB_CONTRACTS` and no `contracts/` nearby, so the schemas must load
   from package data, `axor-lab --help` must resolve, and `axor-lab report
   --help` must reach `lab_service`. This step runs *before* the gate on
   purpose: it answers the question a user actually has (does the install
   work?), which stopping at the gate would leave unanswered;
4. runs the preflight gate;
5. uploads the built files as a workflow artifact, and the publish job
   downloads **those exact files** rather than rebuilding — a rebuild would
   upload artefacts nothing in the workflow had checked.

If you added a required reviewer to the `pypi` environment, approve the run.
`skip-existing: true` means a re-tag to pick up a fix skips an already-published
version instead of failing the whole workflow.

## Verify on a clean machine

Once a release is out, the check is:

```bash
python -m venv cleanroom
./cleanroom/bin/pip install axor-lab   # pulls axor-core, axor-wrap, axor-eval from PyPI
mkdir -p /tmp/cleanroom-check && cd /tmp/cleanroom-check
~/cleanroom/bin/axor-lab suites                             # the catalog, from package data
~/cleanroom/bin/axor-lab run-suite ingest --out ./run --yes # a real run, outside any checkout
~/cleanroom/bin/axor-lab replay ./run                       # and it replays bit-identically
```

This was run against the 0.1.0 wheel built from this tree: the catalog resolves
from package data (13 schemas, no `AXOR_LAB_CONTRACTS`), `run-suite ingest`
completes 108 trials, and the analysis comes back with
ASR 0.71 → 0.00 and McNemar p=1.5e-05 across the governed arms.

It is worth doing in a scratch directory, and not only for tidiness: this check
is what found `resolve_suite_target` treating a *directory* named after a suite
id as a manifest path. In a checkout the cwd is the repo root and the collision
never happens.

The `packaging` job in `ci.yml` asserts the first half of this on every push;
the release workflow repeats it against the exact files being uploaded.

## Dry run on TestPyPI (optional)

Register the same trusted publisher on **test.pypi.org**, add
`repository-url: https://test.pypi.org/legacy/` to the publish step on a
throwaway branch, push a `v0.0.0rc1` tag, then
`pip install --index-url https://test.pypi.org/simple/ --extra-index-url
https://pypi.org/simple/ axor-lab`. Remove the override before the real
release.

## What this workflow does not do

No container images. The Control Plane's `release.yml` also pushes
`axor-platform` / `axor-frontend` to GHCR because it ships a compose stack; Lab
has no `Dockerfile` and is consumed as a library plus a console script, so there
is nothing to build. `axor-lab serve` runs the screen API and the built web app
out of the installed package.

No downstream fan-out. `axor-core`'s publish job dispatches
`axor-core-published` to every repo that depends on it (this one included, see
`ci.yml`'s `repository_dispatch` trigger). Nothing depends on `axor-lab`, so
there is no one to notify — add a fan-out here the day something does.
