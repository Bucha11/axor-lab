# Releasing `axor-lab`

`.github/workflows/release.yml` builds and publishes `axor-lab` to PyPI when a
`vX.Y.Z` tag is pushed. Publishing is credential-free — **PyPI Trusted
Publishing (OIDC)**, the same arrangement the Control Plane uses — so no API
token ever lives in this repo. A test asserts that
(`tests/test_release_preflight.py::TestTheWorkflowWiring`).

This is the one-time setup plus the per-release procedure, and it opens with
the part that matters most right now: **Lab cannot be published yet, and the
workflow will tell you so instead of burning a tag.**

## Blocker: two dependencies are git pins

`pyproject.toml` requires:

```
axor-wrap @ git+https://github.com/Bucha11/axor-wrap@claude/axor-lab-spec-integration-wygy8m
axor-eval @ git+https://github.com/Bucha11/axor-eval@claude/axor-lab-spec-integration-wygy8m
```

PyPI **rejects** any upload whose metadata carries a PEP 508 direct reference:

```
400 Bad Request — Invalid value for requires_dist.
Error: Can't have direct dependency: 'axor-wrap@ git+https://…'
```

That failure lands on the *last* step of a release, after the tag is pushed and
the version number is spent. So `tools/release_preflight.py` runs before the
publish leg, reads the built wheel's `METADATA` (the text PyPI validates, not
`pyproject.toml`), and fails the run with the remedy in the message.

**To unblock a release:** cut an `axor-wrap` release and an `axor-eval` release
carrying the APIs Lab imports (`axor_wrap.WrappedToolset` / `build_trace` /
`toolset_for_arm` / `trial_of`, and `axor_eval.claims.reconstruct_claim`), then
replace both git pins in `pyproject.toml` with version ranges. `axor-core` is
already on PyPI at 0.11.0, which satisfies Lab's `>=0.11,<0.12` — the preflight
probes that too, with `pip download --no-deps`, because a floor no published
version satisfies uploads *fine* and then breaks every install.

Until then, `pip install axor-lab` from a checkout or a git URL works (pip
resolves direct references; PyPI just will not host them).

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
work — today it reports the two git pins above and nothing else.

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

Once the git pins are gone and a release is out, the check is:

```bash
python -m venv cleanroom
./cleanroom/bin/pip install axor-lab      # pulls axor-core, axor-wrap, axor-eval from PyPI
cd / && ~/cleanroom/bin/axor-lab suites   # the catalog, from package data
cd / && ~/cleanroom/bin/axor-lab run-suite ingest-agent --trials 20
```

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
