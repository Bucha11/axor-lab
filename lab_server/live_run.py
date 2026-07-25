"""Live-model runs from the browser — BYOK, estimated, confirmed, capped.

`/runs/local` refuses anything that would cost money, and that refusal stays: a
request must never be able to make the server spend silently. But "run these
conditions against three different models and see which one gets exfiltrated" is
a real question, and it is the one question the connected-runtime path does NOT
answer — that path is about *your* agent, singular, and needs an integration.

So this is a separate surface with the CLI's guarantees carried over rather than
waived:

* **Two steps.** The first call prices the run and returns a confirm token; only
  a second call carrying that token spends anything. The token is derived from
  the exact document, model and budget, so you cannot approve one estimate and
  execute a different run.
* **A budget is required.** The CLI can run unbounded because a human is at the
  terminal watching it. Nothing in an HTTP request plays that role, so an
  unbounded live run is refused outright.
* **The key is yours.** It arrives per request, is used, and is never stored,
  logged or echoed back.

One honesty carried through from `lab_agent.cost`: the token ceilings are HARD —
Lab counts what it sends and caps each call — while `max_usd` is BEST-EFFORT,
derived from an illustrative price table rather than your provider's billing. The
payload says so, because a "dollar cap" that quietly is not one is the kind of
thing people find out from an invoice.

Statistics: a live model draws each condition independently, so the arms are NOT
matched pairs and McNemar does not apply. The runner already enforces this; the
estimate says it up front so nobody plans a paired study they cannot have.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

# A browser-triggered paid run is bounded twice over: by the caller's budget, and
# by this ceiling on how much work one request may queue at all.
MAX_LIVE_TRIALS = 200

SUPPORTED_PROVIDERS = ("anthropic",)


class LiveRunRefused(ValueError):
    """The live run cannot proceed. Carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _budget(raw: Any) -> Any:
    from lab_agent.cost import CostBudget

    if not isinstance(raw, dict) or not raw:
        raise LiveRunRefused(
            400,
            "a live run needs a `budget` — the CLI may run unbounded because a "
            "human is watching the terminal, and nothing in an HTTP request "
            "plays that role. Set max_usd and/or a token ceiling.",
        )
    try:
        budget = CostBudget(
            max_usd=raw.get("max_usd"),
            max_input_tokens=raw.get("max_input_tokens"),
            max_output_tokens=raw.get("max_output_tokens"),
        )
    except ValueError as exc:
        raise LiveRunRefused(400, str(exc)) from exc
    if not budget.is_set():
        raise LiveRunRefused(400, "the budget sets no ceiling at all")
    return budget


def _model(raw: Any) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise LiveRunRefused(400, "`agent` must be {provider, model}")
    provider = str(raw.get("provider") or "anthropic")
    model = str(raw.get("model") or "").strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise LiveRunRefused(
            400, f"unsupported provider {provider!r}; supported: {list(SUPPORTED_PROVIDERS)}"
        )
    if not model:
        raise LiveRunRefused(400, "`agent.model` is required for a live run")
    return provider, model


def _resolve_document(body: dict[str, Any]) -> dict[str, Any]:
    from . import compose, local_run

    selection = body.get("compose")
    document = body.get("experiment")
    if isinstance(selection, dict):
        return compose.compose(selection)["document"]
    if isinstance(document, dict):
        return document
    if document is None and selection is None:
        return local_run.load_example()
    raise LiveRunRefused(400, "`experiment` must be an .axl document object")


def _confirm_token(document: dict[str, Any], model: str, budget: Any) -> str:
    """Bind an estimate to the exact run it priced.

    Without this, an operator could be shown a $0.40 estimate for a 30-trial run
    and confirm a 3000-trial one — the approval has to commit to what it approved.
    """
    from lab_contracts import content_hash

    return content_hash({
        "experiment": document,
        "model": model,
        "budget": {
            "max_usd": budget.max_usd,
            "max_input_tokens": budget.max_input_tokens,
            "max_output_tokens": budget.max_output_tokens,
        },
    })


def plan(body: dict[str, Any]) -> dict[str, Any]:
    """Price a live run and return the token that authorises exactly it."""
    from lab_agent.cost import estimate_cost
    from lab_runner.errors import ExperimentFileError
    from lab_runner.experiment_file import resolve

    document = _resolve_document(body)
    _, model = _model(body.get("agent"))
    budget = _budget(body.get("budget"))

    try:
        resolved = resolve(document)
    except ExperimentFileError as exc:
        raise LiveRunRefused(422, "not a runnable experiment: " + "; ".join(exc.errors)) from exc

    trials = len(resolved.scenarios) * len(resolved.conditions) * max(resolved.repeats, 1)
    if trials > MAX_LIVE_TRIALS:
        raise LiveRunRefused(
            413, f"{trials} planned trials exceeds the live ceiling of {MAX_LIVE_TRIALS}"
        )

    estimate = estimate_cost(trials, model)
    return {
        "confirm_token": _confirm_token(document, model, budget),
        "model": model,
        "trials": trials,
        "estimate": {
            "trials": estimate.trials,
            "input_tokens": estimate.est_input_tokens,
            "output_tokens": estimate.est_output_tokens,
            "usd": estimate.est_usd,
            "line": estimate.line(),
        },
        "ceilings": {
            "max_usd": budget.max_usd,
            "max_input_tokens": budget.max_input_tokens,
            "max_output_tokens": budget.max_output_tokens,
            # said plainly, because a dollar cap that is not one is discovered
            # from an invoice
            "usd_is_best_effort": True,
            "note": "token ceilings are hard — Lab counts what it sends and caps "
                    "each call. max_usd is derived from an illustrative price "
                    "table, not your provider's billing: it stops the run NEAR "
                    "the figure. Set a token ceiling when an exact bound matters.",
        },
        # a live model samples each condition independently, so the arms are not
        # matched pairs — say it before the money is spent, not after
        "comparison_design": "independent_samples",
        "design_note": "a live model draws each condition independently, so the "
                       "arms are not matched pairs: this reports a two-proportion "
                       "comparison, never a paired McNemar p-value.",
    }


def execute(body: dict[str, Any]) -> dict[str, Any]:
    """Run it, for real, against the provider — after the token checks out."""
    from lab_agent import AnthropicBackend, WrappedModelAgent
    from lab_agent.cost import actual_usd
    from lab_agent.errors import AgentError
    from lab_analysis import missingness
    from lab_analysis.errors import AnalysisError
    from lab_contracts import build_bundle
    from lab_runner.bundle_io import PACKAGING
    from lab_runner.cli import _aggregates, _derive_run_id, _environment
    from lab_runner.errors import RunnerError
    from lab_runner.experiment_file import resolve
    from lab_runner.runner import run_experiment_suite

    document = _resolve_document(body)
    _, model = _model(body.get("agent"))
    budget = _budget(body.get("budget"))

    supplied = str(body.get("confirm_token") or "")
    expected = _confirm_token(document, model, budget)
    if not supplied:
        raise LiveRunRefused(
            428, "this run costs money — call /runs/live/plan first and confirm the estimate"
        )
    if supplied != expected:
        raise LiveRunRefused(
            409,
            "the confirm token does not match this request — the estimate you "
            "approved was for a different experiment, model or budget",
        )

    api_key = str(body.get("api_key") or "").strip()
    if not api_key:
        raise LiveRunRefused(400, "a live run needs your provider `api_key` (BYOK)")

    resolved = resolve(document)
    backend = AnthropicBackend(model=model)
    agent = WrappedModelAgent(backend=backend)
    agent.budget = budget
    agent.model = model

    def budget_check() -> str | None:
        return budget.exceeded(agent.usage(), model)

    run_id = _derive_run_id(None, resolved.experiment, f"anthropic:{model}", deterministic=False)

    # The key lives in the environment for the duration of this call only. It is
    # never written to the job store, never logged, and never echoed back.
    previous = os.environ.get(backend.api_key_env)
    os.environ[backend.api_key_env] = api_key
    try:
        result = run_experiment_suite(
            list(resolved.scenarios), resolved.manifests, list(resolved.conditions),
            resolved.kernel_registry, repeats=resolved.repeats,
            run_id=run_id, agent=agent, budget_check=budget_check,
        )
    except (AgentError, RunnerError) as exc:
        raise LiveRunRefused(502, f"the provider run failed: {exc}") from exc
    finally:
        if previous is None:
            os.environ.pop(backend.api_key_env, None)
        else:
            os.environ[backend.api_key_env] = previous

    by_status: dict[str, int] = {}
    for trial in result.trials:
        key = str(trial["status"])
        by_status[key] = by_status.get(key, 0) + 1

    if not by_status.get("completed"):
        # Every trial failed — a rejected key, an unreachable provider, a model
        # name that does not exist. Landing that as a "completed" run with zero
        # traces would report success for a run that produced no evidence at all,
        # so surface the runner's own reason instead.
        reasons = sorted({
            str(t.get("failure_reason") or "unknown")
            for t in result.trials if t["status"] == "failed"
        })
        raise LiveRunRefused(
            502,
            f"every one of the {len(result.trials)} trials failed against "
            f"{model} — nothing was measured. Reason(s): {'; '.join(reasons[:3])}",
        )

    used = agent.usage()
    usage = {
        "input_tokens": int(used.get("input_tokens", 0)),
        "output_tokens": int(used.get("output_tokens", 0)),
        "usd": actual_usd(
            int(used.get("input_tokens", 0)), int(used.get("output_tokens", 0)), model,
        ),
    }
    if result.stopped_reason:
        usage["stopped_reason"] = result.stopped_reason

    summary = missingness(result.trials)
    try:
        aggregates = _aggregates(resolved, result, agent)
        environment = _environment(resolved, model, usage, agent=agent)
    except (RunnerError, AnalysisError) as exc:
        raise LiveRunRefused(422, f"cannot summarise the run: {exc}") from exc

    bundle = build_bundle(
        bundle_id=f"b_{run_id}",
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        scenarios=list(resolved.scenarios),
        conditions=list(resolved.conditions),
        tool_manifests=list(resolved.manifests.values()),
        environment=environment,
        trials=result.trials,
        aggregates=aggregates,
        traces=result.traces,
        packaging=dict(PACKAGING),
    )
    return {
        "run_id": run_id,
        "bundle": bundle,
        "traces": dict(result.traces),
        "aggregates": aggregates,
        "missingness": summary.display(),
        "by_status": by_status,
        "stopped_reason": result.stopped_reason,
        "usage": usage,
        "model": model,
    }
