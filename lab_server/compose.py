"""The experiment catalogue, and composing a runnable `.axl` from a selection.

The builder used to invent its own menu client-side: four suites with fabricated
scenario ids (`banking-01`…`banking-07`, `slack-*`, `travel-*`). None of them
exist, so nothing the builder composed could actually run — it was a picture of a
builder. This module is the real menu, served from the code that owns the
scenarios, plus the composition step that turns a selection into a validated
document the runner accepts.

Composition is server-side on purpose. A runnable `.axl` needs scenario objects,
tool manifests and per-condition config hashes that must agree with what the
kernel will compute; a browser assembling that by hand would be a second,
drifting implementation of the contract.
"""
from __future__ import annotations

from typing import Any

# Enforcement presets a selection can ask for, in narrowing order. `ungoverned`
# is the undefended baseline every comparison needs — the ASR delta is meaningless
# without it, so it cannot be composed away.
BASELINE_CONDITION = "ungoverned"
_PRESETS: dict[str, dict[str, Any] | None] = {
    BASELINE_CONDITION: None,
    "governed": {"profile": "strict", "trust_model": "content-ledger"},
}

# The stdlib reference kernel — the same one the bundled example pins, and the
# only kernel guaranteed to be present. A composed run must always be runnable, so
# it never pins a real axor-core build: the runner refuses to run a build other
# than the pinned one, and `import-agentdojo` pins `axor-core@0.4.2`, which fails
# against any other installed core.
#
# Driving the production governor stays a CLI concern (`axor-lab run
# --real-kernel`), which repins every condition to the INSTALLED version so the
# comparison isolates enforcement rather than a mixed kernel.
REFERENCE_KERNEL = "reference_taint_floor_kernel"

MAX_REPEATS = 100
# The default, and the floor below which the comparison stops being able to say
# anything. McNemar's power comes only from the DISCORDANT pairs, so a small run
# yields an aggregate with no published test at all — honest, but a dead end for
# someone who just wanted a result. At 10 repeats over the curated suite (30
# matched pairs) the test is conclusive; at 5 it is not.
DEFAULT_REPEATS = 10
MIN_POWERED_REPEATS = 10


class ComposeRefused(ValueError):
    """The selection cannot be composed. Carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _axor_available() -> bool:
    from lab_runner import axor_available

    return bool(axor_available())


def _real_kernel_version() -> str | None:
    from lab_runner import real_kernel_version

    return real_kernel_version()


def catalogue() -> dict[str, Any]:
    """Everything a builder can legitimately offer, with real scenario counts.

    The suites come from the adapter that materializes them, so the menu cannot
    drift from what is runnable. The bundled example is listed beside them as its
    own entry — it is a hand-written experiment, not an imported suite.
    """
    from lab_adapters import available_suites, import_suite

    suites = []
    for suite in available_suites():
        scenarios = import_suite(suite)
        suites.append({
            "id": suite,
            "label": suite,
            "source": "agentdojo-curated",
            "scenarios": [
                {"name": str(s["name"]), "task": str(s.get("task", ""))}
                for s in scenarios
            ],
        })
    return {
        "suites": suites,
        "conditions": [
            {"id": name, "policy": policy, "baseline": name == BASELINE_CONDITION}
            for name, policy in _PRESETS.items()
        ],
        "kernel": REFERENCE_KERNEL,
        # Whether this server can drive the PRODUCTION governor. `--real-kernel`
        # was CLI-only only because that is where axor-core happened to be
        # installed; when the server has it, the server can repin every condition
        # — baseline included, so the compare isolates enforcement rather than
        # mixing an enforcement change with a kernel change.
        "real_kernel": {
            "available": _axor_available(),
            "version": _real_kernel_version(),
        },
        "agent": {
            "ref": "scripted@0.6",
            "deterministic": True,
            "note": "a deterministic stand-in for the model layer — no provider, "
                    "no key, no cost. A live model needs the CLI.",
        },
        "repeats": {
            "default": DEFAULT_REPEATS,
            "max": MAX_REPEATS,
            # Below this the paired test has too few discordant pairs to conclude,
            # so the aggregate publishes no p-value. The UI says so before you run
            # rather than leaving you with a result that quietly answers nothing.
            "min_powered": MIN_POWERED_REPEATS,
        },
    }


def _conditions(condition_ids: list[str]) -> list[dict[str, Any]]:
    from lab_contracts import condition_config_hash

    unknown = [c for c in condition_ids if c not in _PRESETS]
    if unknown:
        raise ComposeRefused(
            400, f"unknown condition(s) {unknown}; available: {sorted(_PRESETS)}"
        )
    if BASELINE_CONDITION not in condition_ids:
        # Refusing beats silently adding it: a comparison whose baseline the
        # caller did not ask for is not the comparison they think they ran.
        raise ComposeRefused(
            400,
            f"a comparison needs the {BASELINE_CONDITION!r} baseline — the attack "
            "success delta has nothing to be a delta against without it",
        )
    out = []
    for name in condition_ids:
        policy = _PRESETS[name]
        condition: dict[str, Any] = {
            "schema_version": "condition/v1",
            "id": name,
            "label": name,
            "enforcement": "off" if policy is None else "on",
            "kernel": REFERENCE_KERNEL,
            "config_hash": condition_config_hash(REFERENCE_KERNEL, policy),
        }
        if policy is not None:
            condition["policy"] = policy
        out.append(condition)
    return out


def compose(spec: dict[str, Any]) -> dict[str, Any]:
    """Turn `{suite, conditions, repeats}` into a runnable `.axl` document.

    Returns `{document, planned_trials, estimate}`. The document is resolved
    before it is handed back, so a selection that cannot run fails here with a
    reason rather than at execution.
    """
    from lab_adapters import UnknownSuiteError, build_experiment_document
    from lab_runner.errors import ExperimentFileError
    from lab_runner.experiment_file import resolve

    from .runtime_jobs import plan_experiment

    suite = str(spec.get("suite") or "").strip()
    if not suite:
        raise ComposeRefused(400, "a selection needs a `suite`")
    condition_ids = spec.get("conditions") or [BASELINE_CONDITION, "governed"]
    if not isinstance(condition_ids, list) or not all(isinstance(c, str) for c in condition_ids):
        raise ComposeRefused(400, "`conditions` must be a list of condition ids")

    try:
        repeats = int(spec.get("repeats", DEFAULT_REPEATS))
    except (TypeError, ValueError):
        raise ComposeRefused(400, "`repeats` must be an integer") from None
    if repeats < 1 or repeats > MAX_REPEATS:
        raise ComposeRefused(400, f"`repeats` must be in 1..{MAX_REPEATS}")

    # Which of the declared conditions actually run. `ungoverned` is the run with
    # no gate in it at all — the plain "what does my agent do on these tasks"
    # experiment. Governance is something you ADD to a run here, so it has to be
    # possible to leave it out; the .axl format already had run_mode for exactly
    # this, and nothing was passing it.
    run_mode = str(spec.get("run_mode") or "compare")
    if run_mode not in ("compare", "ungoverned", "governed"):
        raise ComposeRefused(
            400, f"unknown run_mode {run_mode!r}; expected compare, ungoverned or governed"
        )

    try:
        document = build_experiment_document(
            suite, _conditions(list(condition_ids)), repeats=repeats, run_mode=run_mode,
        )
    except UnknownSuiteError as exc:
        raise ComposeRefused(404, str(exc)) from exc

    if spec.get("real_kernel"):
        # Drive the production axor-core governor instead of the stdlib reference
        # kernel. This was CLI-only only because the CLI is where axor-core
        # happened to be installed; when the server has it, the server can do it.
        from lab_runner.cli import _repin_to_real_kernel
        from lab_runner.errors import RunnerError

        try:
            _repin_to_real_kernel(document)
        except RunnerError as exc:
            raise ComposeRefused(409, str(exc)) from exc

    try:
        resolved = resolve(document)
    except ExperimentFileError as exc:
        # A composed suite that will not resolve is a bug in this module, not user
        # error — surface it loudly instead of handing back a document that fails
        # at execution.
        raise ComposeRefused(
            500, "composed a document that does not resolve: " + "; ".join(exc.errors)
        ) from exc

    # plan over the conditions that will actually RUN, not the ones declared:
    # under run_mode=ungoverned the governed arm is not executed, and planning it
    # anyway would report every such run as half-missing.
    plan = plan_experiment({
        **document["experiment"],
        "condition_ids": [str(c["id"]) for c in resolved.conditions],
    })
    return {
        "document": document,
        "planned_trials": plan["trials"],
        "estimate": plan["estimate"],
    }
