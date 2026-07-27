"""`POST /incidents/reconstruct` — a pre-Axor incident becomes a scenario draft.

The sibling of `POST /api/incidents`, and deliberately not the same thing:

  * **`/api/incidents`** takes an Axor `trace/v1` package and REPLAYS it. The
    value ledger and `arg_bindings` are on record, so the verdict is recomputed
    exactly. Fidelity `explicit_flow_tracked`.
  * **this** takes whatever your existing observability had — LangSmith runs,
    OTel spans, application logs — and RECONSTRUCTS a scenario draft from it.
    Nothing is replayed, nothing is decided, nothing is stored. Fidelity
    `heuristic_attribution`, stated in the response body.

Conflating the two under one "trace import" was the original error, because it
promised exact replay of a run Axor never saw. The honest funnel is: your first
incident is reconstructed, every incident after it replays exactly, because by
then Axor was there when it happened.

This endpoint is a pure function of its input. It writes nothing, executes
nothing, and spends nothing — the run only happens later, once a human has
confirmed the draft, through the ordinary `/runs/local` path.
"""

from __future__ import annotations

from typing import Any

from lab_runner.reconstruct import ReconstructionRefused, build_experiment, reconstruct

from .errors import PublishRejected

#: an uploaded transcript is a document, not a stream; anything larger is a data
#: export and belongs in the CLI
MAX_CALLS = 500


def handle_reconstruct(body: dict[str, Any]) -> dict[str, Any]:
    """Draft a scenario from a recorded incident.

    Body: `{trace: <the recording>, name?: str}` — or the recording itself, since
    people paste what their tool exported rather than wrap it for us.
    """
    payload = body.get("trace") if isinstance(body, dict) and "trace" in body else body
    name = "reconstructed-incident"
    if isinstance(body, dict) and isinstance(body.get("name"), str) and body["name"].strip():
        name = body["name"].strip()
    try:
        result = reconstruct(payload, name=name)
    except ReconstructionRefused as exc:
        # 422, not 400: the request was well-formed and we understood it — it
        # just cannot become a scenario, and the message says why
        raise PublishRejected(str(exc), 422) from exc
    if len(result.calls) > MAX_CALLS:
        raise PublishRejected(
            f"{len(result.calls)} tool calls is beyond what this endpoint drafts "
            f"({MAX_CALLS}); narrow the export to the incident itself",
            413,
        )
    return result.to_dict()


def build_confirmed(selection: dict[str, Any]) -> dict[str, Any]:
    """A CONFIRMED draft becomes a runnable `.axl`.

    Everything reconstructed ends here. What is assembled is an ordinary Lab
    experiment that happens to have been authored from an incident: the run it
    produces is genuine, its trace is conformant, and its EvidenceCase is real.
    What it is NOT is a replay of the incident — the scenario carries that
    statement in its `notes`, and it travels with the bundle.

    `{scenario, manifests, repeats?, governed?, agent_ref?}`.
    """
    from .compose import MAX_REPEATS, _conditions

    scenario = selection.get("scenario")
    manifests = selection.get("manifests")
    if not isinstance(scenario, dict) or not scenario.get("name"):
        raise PublishRejected("`scenario` must be the confirmed scenario object", 400)
    if not isinstance(manifests, list) or not manifests:
        raise PublishRejected("`manifests` must be the confirmed tool manifests", 400)
    if not scenario.get("task_success"):
        # utility is half of every number this product reports, and the extractor
        # deliberately refuses to invent it. Running without it would report a
        # gate's cost as zero for the wrong reason.
        raise PublishRejected(
            "`task_success` is empty — say what the agent had to do for the run to "
            "count as useful, or the utility side of the result means nothing",
            400,
        )
    try:
        repeats = int(selection.get("repeats", 10))
    except (TypeError, ValueError):
        raise PublishRejected("`repeats` must be an integer", 400) from None
    if repeats < 1 or repeats > MAX_REPEATS:
        raise PublishRejected(f"`repeats` must be in 1..{MAX_REPEATS}", 400)
    governed = bool(selection.get("governed", True))
    # The agent is the axis that decides what the result MEANS, and it is the one
    # thing the incident trace could not give us. A stand-in proves the mechanism
    # fires on this shape of incident; only THEIR wrapped agent measures their
    # agent. The default is the stand-in because that is what a first-contact
    # demo has, and the limitation is stamped on the publication rather than left
    # to whoever renders the number.
    agent_ref = str(selection.get("agent_ref") or "scripted@0.6")
    return build_experiment(
        scenario, list(manifests), _conditions(["ungoverned", "governed"]),
        repeats=repeats, run_mode="compare" if governed else "ungoverned",
        agent_ref=agent_ref,
    )


def handle_compose(body: dict[str, Any]) -> dict[str, Any]:
    """A confirmed draft as a document + plan, for the CONNECTED-RUNTIME path.

    `/runs/local` executes here with the stand-in; a customer's own agent runs on
    their own infrastructure, so that path needs the assembled `.axl` to hand to
    the runtime instead. Same builder, same validation — only the executor
    differs, and it is the difference that makes the result theirs.
    """
    from .runtime_jobs import plan_experiment

    document = build_confirmed(body)
    experiment: dict[str, Any] = document["experiment"]
    plan = plan_experiment({
        **experiment,
        "condition_ids": [str(c["id"]) for c in experiment["conditions"]]
        if str(experiment.get("run_mode")) == "compare"
        else [str(experiment["conditions"][0]["id"])],
    })
    return {
        "document": document,
        "planned_trials": plan["trials"],
        "estimate": plan["estimate"],
    }
