"""Reconstruct a pre-Axor incident as a scenario draft. Never as a verdict.

The rule this module exists to respect:

    Replay re-runs the JUDGE, not the agent — but it needs the judge's inputs on
    record.

`decide(π(x), policy)` is pure, so a recording of its inputs is enough to
recompute a verdict with no agent, no model and no tools. Those inputs are the
value ledger (labels, `sources`, `derived_from`) and `arg_bindings`, and **only
an Axor adapter emits them**. A LangSmith / OTel / application-log trace records
what was CALLED, never where each value CAME FROM. There is nothing for the gate
to read, so no exact replay is possible — and the incident that motivates the
whole conversation happened before Axor was installed, by definition.

The tempting move is to guess the missing provenance: substring-match the
attacker's IBAN in a tool result against a later tool argument and call it
lineage. That guess is unsound in BOTH directions — it over-taints (the same
digits legitimately reached the sink) and under-taints (the value was
paraphrased, translated or recomputed on the way) — so it must never produce a
verdict, and nothing here does.

It is, however, very good at one thing: SEEDING A SCENARIO. From a messy trace
this module proposes the tools, the task, the untrusted content and where it
entered, the harmful call that followed, and the legitimate outcome. A human
confirms or corrects the draft, and what comes out is a real `scenario/v1`. Run
it under Axor and the trace you get is genuine — a real EvidenceCase, a real
regression case, and claims that describe THAT run rather than the incident.

So the generic trace's job is to author a scenario, not to be replayed. Nothing
in this module executes anything or decides anything; it reads, proposes, and
says what it could not find.

WHAT IS AND IS NOT RECOVERED — the distinction the whole product rests on. This
rebuilds the incident's WORLD, never its AGENT:

    task                                    ✅ → `task`
    tools that existed, their shapes        ✅ → draft manifests
    what the tools RETURNED, poison and all ✅ → `fixtures` + injection_placement
    the harmful call that followed          ✅ → `violation`
    the agent that decided to make it       ❌ — it is the thing under test

You cannot rebuild a decider from a recording of its decisions. So the honest
sentence is "we rebuild the incident's world and run YOUR agent through it", and
never "we replay your incident" — nothing of the agent is replayed.

What the customer is spared is therefore not the integration; their agent still
has to be wrapped. What they are spared is having had Axor installed at the
moment the incident happened. And because every tool result is frozen from the
trace into a fixture, the reconstruction needs none of their production tools,
backends or credentials:

    world       (tool results + the injection)   ← fixtures, from the trace
    agent       (the thing that decides)         ← live, wrapped, theirs
    governance  (labels, gates, decide)          ← the kernel
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .errors import RunnerError

#: what the draft is honest about being. Mirrors provenance-semantics.md §5:
#: heuristic_attribution is a best-effort mapping that MUST be labeled and MUST
#: NOT be presented as sound.
FIDELITY = "heuristic_attribution"

#: tools whose name suggests an effect that leaves the system. Only used to
#: ORDER candidates and to guess a manifest's default_class — a guess the user
#: sees and can change, never a gate input.
_EGRESS_HINTS = (
    "send", "post", "email", "mail", "transfer", "pay", "publish", "share",
    "upload", "webhook", "notify", "message", "sms", "tweet", "write", "create",
)
_READ_HINTS = ("get", "read", "list", "search", "fetch", "query", "load", "find", "retrieve")

#: phrasings that mark a payload as an instruction rather than data. Deliberately
#: small and boring: this ranks candidates for a human to look at, and a longer
#: list would only make a wrong guess look more authoritative.
_INJECTION_MARKERS = re.compile(
    r"ignore (all )?(prior|previous|the above)|disregard (the |all )?(prior|previous)|"
    r"instead[, ]|new instructions?|system[: ]|you must now|forget (the |your )",
    re.IGNORECASE,
)

#: a value has to be this long before a substring match between a read result and
#: a later argument means anything. Short tokens ("1200", "ok", "true") collide
#: by accident constantly, and a collision presented as lineage is exactly the
#: unsound guess this module refuses to make.
_MIN_LINKED_LEN = 8

#: `foo[3]` → `foo[]`, the only form the runner's field expander matches.
_INDEX_RE = re.compile(r"\[\d+\]")


class ReconstructionRefused(RunnerError):
    """The upload cannot be reconstructed. Carries a reason a human can act on."""


@dataclass(frozen=True)
class ObservedCall:
    """One tool call as some other system recorded it."""

    index: int
    tool: str
    args: dict[str, Any]
    result: Any
    raw_name: str = ""


@dataclass
class Finding:
    """Something the extractor noticed, and where it saw it.

    Every draft field the user is asked to confirm has one of these behind it, so
    "why does it think that" is answerable without reading this file.
    """

    kind: str          # task | untrusted_source | injection | sink | linked_value
    detail: str
    where: str = ""
    confidence: str = "heuristic"


@dataclass
class Reconstruction:
    """A scenario DRAFT plus everything needed to judge it."""

    scenario: dict[str, Any]
    manifests: list[dict[str, Any]]
    calls: list[ObservedCall]
    findings: list[Finding] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    fidelity: str = FIDELITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "fidelity": self.fidelity,
            "scenario": self.scenario,
            "manifests": self.manifests,
            "observed_calls": [
                {"index": c.index, "tool": c.tool, "args": c.args, "result": c.result}
                for c in self.calls
            ],
            "findings": [
                {"kind": f.kind, "detail": f.detail, "where": f.where,
                 "confidence": f.confidence}
                for f in self.findings
            ],
            "unresolved": list(self.unresolved),
            # said in the payload, not only in a docstring: a consumer that
            # renders this must not present it as a verdict or a replay
            "note": (
                "A scenario DRAFT reconstructed from a non-Axor trace by heuristic "
                "attribution. It is a model of the incident, not the incident. No "
                "verdict was computed and none can be: the recording carries no "
                "value provenance. Confirm or correct it, then run it under Axor "
                "to get a real trace."
            ),
        }


# ── normalization ────────────────────────────────────────────────────────────
# Each reader below turns one popular recording shape into ObservedCalls. They
# are deliberately permissive about key names and strict about the outcome: if
# no tool call can be found, that is an error with a reason, not an empty draft.

_TOOL_KEYS = ("tool", "tool_name", "name", "function", "function_name", "action")
_ARG_KEYS = ("args", "arguments", "input", "inputs", "parameters", "params")
_RESULT_KEYS = ("result", "output", "outputs", "response", "return", "observation")
_TASK_KEYS = ("task", "prompt", "question", "user_input", "query", "instruction", "goal")


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", {}, []):
            return mapping[key]
    return None


def _as_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        # tool arguments arrive JSON-encoded in most transcripts
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {"input": value}
        return dict(parsed) if isinstance(parsed, dict) else {"input": parsed}
    return {"input": value}


def _looks_like_call(item: Any) -> bool:
    return isinstance(item, dict) and _first(item, _TOOL_KEYS) is not None


def _calls_from_records(records: list[Any]) -> list[ObservedCall]:
    calls: list[ObservedCall] = []
    for item in records:
        if not _looks_like_call(item):
            continue
        raw = str(_first(item, _TOOL_KEYS))
        calls.append(ObservedCall(
            index=len(calls),
            tool=_slug(raw),
            args=_as_args(_first(item, _ARG_KEYS)),
            result=_first(item, _RESULT_KEYS),
            raw_name=raw,
        ))
    return calls


def _calls_from_langsmith(runs: list[Any]) -> list[ObservedCall]:
    """LangSmith-style runs: a flat list with `run_type` and inputs/outputs."""
    return _calls_from_records([
        r for r in runs
        if isinstance(r, dict) and str(r.get("run_type", "tool")).lower() in ("tool", "function")
    ])


def _calls_from_otel(payload: dict[str, Any]) -> list[ObservedCall]:
    """OTel spans, flattened out of resourceSpans → scopeSpans → spans.

    Tool identity and payloads live in `attributes`, whose shape is a list of
    `{key, value:{stringValue|intValue|...}}` pairs.
    """
    spans: list[dict[str, Any]] = []
    for resource in payload.get("resourceSpans") or []:
        for scope in (resource or {}).get("scopeSpans") or []:
            spans.extend(s for s in (scope or {}).get("spans") or [] if isinstance(s, dict))
    records: list[dict[str, Any]] = []
    for span in spans:
        attrs: dict[str, Any] = {}
        for attribute in span.get("attributes") or []:
            if not isinstance(attribute, dict):
                continue
            value = attribute.get("value")
            if isinstance(value, dict):
                value = next(iter(value.values()), None)
            attrs[str(attribute.get("key"))] = value
        name = (
            attrs.get("gen_ai.tool.name") or attrs.get("tool.name")
            or attrs.get("function.name") or span.get("name")
        )
        if not name:
            continue
        records.append({
            "tool": name,
            "args": attrs.get("gen_ai.tool.input") or attrs.get("tool.arguments")
                    or attrs.get("input"),
            "result": attrs.get("gen_ai.tool.output") or attrs.get("tool.result")
                      or attrs.get("output"),
        })
    return _calls_from_records(records)


def normalize(payload: Any) -> list[ObservedCall]:
    """Find the tool calls in whatever was uploaded.

    Raises rather than returning an empty list: "we found nothing" is a fact the
    user needs stated, not a draft with no tools in it.
    """
    if isinstance(payload, dict) and payload.get("resourceSpans"):
        calls = _calls_from_otel(payload)
    elif isinstance(payload, dict) and isinstance(payload.get("runs"), list):
        calls = _calls_from_langsmith(payload["runs"])
    elif isinstance(payload, list):
        calls = (
            _calls_from_langsmith(payload)
            if any(isinstance(r, dict) and "run_type" in r for r in payload)
            else _calls_from_records(payload)
        )
    elif isinstance(payload, dict):
        for key in ("calls", "steps", "events", "trace", "messages", "spans", "records"):
            inner = payload.get(key)
            if isinstance(inner, list):
                calls = _calls_from_records(inner)
                if calls:
                    break
        else:
            calls = _calls_from_records([payload])
    else:
        raise ReconstructionRefused(
            "expected a JSON object or array of recorded steps"
        )
    if not calls:
        raise ReconstructionRefused(
            "no tool calls found. A reconstruction needs the calls the agent made: "
            "records carrying a tool name plus its arguments and result, in any of "
            "a flat list, {runs:[…]} (LangSmith), or OTel resourceSpans."
        )
    return calls


def is_axor_trace(payload: Any) -> bool:
    """Is this already an Axor trace, which should be REPLAYED, not reconstructed?

    Worth detecting rather than silently degrading: handing a conformant trace to
    the heuristic path would swap an exact verdict replay for a guess, which is
    the single worst trade this product can make.
    """
    if not isinstance(payload, dict):
        return False
    if str(payload.get("schema_version", "")).startswith("trace/"):
        return True
    events = payload.get("events")
    return isinstance(events, list) and any(
        isinstance(e, dict) and e.get("type") == "gate_decision" for e in events
    )


# ── extraction ───────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name)).strip("_").lower()
    return slug or "tool"


def _strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string in a structure, with its path — the injection hunting ground."""
    out: list[tuple[str, str]] = []
    if isinstance(value, str):
        out.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            out.extend(_strings(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.extend(_strings(item, f"{path}[{i}]"))
    return out


def _find_task(payload: Any, calls: list[ObservedCall]) -> tuple[str, Finding | None]:
    """The legitimate goal, as the user prompt."""
    # a LangSmith export carries the prompt on the chain/llm run, not at the top
    # level, so look through the records before giving up
    if isinstance(payload, dict):
        for key in ("runs", "calls", "steps", "records", "spans"):
            for record in payload.get(key) or []:
                if not isinstance(record, dict):
                    continue
                for holder in (record, record.get("inputs"), record.get("input")):
                    if not isinstance(holder, dict):
                        continue
                    found = _first(holder, _TASK_KEYS)
                    if isinstance(found, str) and found.strip():
                        return found.strip(), Finding(
                            "task", found.strip(), f"{key}[].inputs",
                        )
    if isinstance(payload, dict):
        direct = _first(payload, _TASK_KEYS)
        if isinstance(direct, str) and direct.strip():
            return direct.strip(), Finding("task", direct.strip(), "top-level field")
        messages = payload.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and str(message.get("role")) == "user":
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip(), Finding("task", content.strip(), "messages[user]")
    return "", None


def _linked_values(calls: list[ObservedCall]) -> list[tuple[ObservedCall, str, str, int]]:
    """Values that appear in a later call's ARGUMENTS and in an earlier RESULT.

    This is the substring match, and it is used for exactly one thing: proposing
    which call to treat as the harmful one. It is NOT lineage — the same string
    reaching a sink proves nothing about how it got there — and the caller labels
    every use of it heuristic.

    Returns (sink_call, arg_name, value, source_index).
    """
    seen: list[tuple[int, str]] = []  # (call index, string) from results
    links: list[tuple[ObservedCall, str, str, int]] = []
    for call in calls:
        for arg_name, arg_value in call.args.items():
            if not isinstance(arg_value, str) or len(arg_value) < _MIN_LINKED_LEN:
                continue
            for source_index, text in seen:
                if arg_value in text:
                    links.append((call, arg_name, arg_value, source_index))
                    break
        for _, text in _strings(call.result):
            if len(text) >= _MIN_LINKED_LEN:
                seen.append((call.index, text))
    return links


def _injection(calls: list[ObservedCall]) -> tuple[ObservedCall, str, str] | None:
    """The most instruction-shaped string in any tool result, and where it sat."""
    best: tuple[ObservedCall, str, str] | None = None
    for call in calls:
        for path, text in _strings(call.result):
            if _INJECTION_MARKERS.search(text) and (best is None or len(text) < len(best[2])):
                best = (call, path, text)
    return best


def _effect_guess(tool: str) -> str:
    lowered = tool.lower()
    if any(hint in lowered for hint in _EGRESS_HINTS):
        return "EXPORT"
    if any(hint in lowered for hint in _READ_HINTS):
        return "READ"
    return "WRITE"


def _manifest(call: ObservedCall, untrusted_path: str | None, driving: list[str]) -> dict[str, Any]:
    """A DRAFT tool-manifest/v1 from one observed call.

    Shapes come from the single recorded example, so this is a starting point a
    human edits — which is why every field it guesses is one the review screen
    shows.
    """
    effect_class = _effect_guess(call.tool)
    manifest: dict[str, Any] = {
        "schema_version": "tool-manifest/v1",
        "id": call.tool,
        "args_schema": {
            "type": "object",
            "properties": {k: {"type": _json_type(v)} for k, v in call.args.items()},
        },
        "result_schema": {"type": _json_type(call.result)},
        "effect": {
            "default_class": effect_class,
            "driving_args": list(driving),
        },
        "side_effecting": effect_class in ("EXPORT", "WRITE", "EXEC"),
    }
    if manifest["side_effecting"]:
        # a side-effecting tool MUST declare how it is faked, or the reference
        # host refuses to invent a result — correctly: silently inventing the
        # outcome of a payment is the last thing a governance lab should do. The
        # incident already told us what the call returned, so the stub is honest
        # rather than invented, and real execution stays opt-out.
        # `noop_stub`: the attempt is RECORDED and nothing is pretended. The
        # richer adapters (ledger_stub, email_outbox) model semantics we do not
        # know from one recording, and a reconstruction that invents the outcome
        # of a payment would be inventing exactly the fact under study.
        manifest["simulation"] = {
            "supported": True,
            "adapter": "noop_stub",
            "real_execution": {"opt_in": False},
        }
        manifest["reset"] = {"strategy": "snapshot_restore"}
    else:
        manifest["reset"] = {"strategy": "fixture", "fixture_ref": call.tool}
    if untrusted_path:
        # the EXACT path the payload sat at, because that is what the incident
        # showed and what `_check_injection_vector` matches against: a scenario
        # whose injection lands outside a declared untrusted field has no vector
        # and will not resolve. Declaring the whole `result` instead reads as
        # broader but fails that check, since the match is exact, not prefix.
        manifest["untrusted_fields"] = [untrusted_path]
    return manifest


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) or isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def reconstruct(payload: Any, name: str = "reconstructed-incident") -> Reconstruction:
    """Turn a non-Axor recording into a scenario draft.

    Nothing is executed and no verdict is produced. What comes back is a
    proposal, the evidence behind each part of it, and an explicit list of what
    the extractor could not determine.
    """
    if is_axor_trace(payload):
        raise ReconstructionRefused(
            "this is an Axor trace — it carries the value provenance a verdict is "
            "computed from, so it can be REPLAYED exactly. Reconstruction would "
            "throw that away and guess instead."
        )
    calls = normalize(payload)
    findings: list[Finding] = []
    unresolved: list[str] = []

    task, task_finding = _find_task(payload, calls)
    if task_finding:
        findings.append(task_finding)
    else:
        unresolved.append("task — no user prompt found in the recording; write what the agent was asked to do")

    injection = _injection(calls)
    links = _linked_values(calls)

    # the sink: the call that carried a value out. Prefer one whose argument was
    # seen in an earlier result (a link), else the last egress-shaped call.
    sink_call: ObservedCall | None = None
    sink_arg = ""
    if links:
        sink_call, sink_arg, value, source_index = links[-1]
        findings.append(Finding(
            "linked_value",
            f"{sink_call.tool}.{sink_arg} carried a value that appeared in the result of "
            f"call #{source_index} ({calls[source_index].tool})",
            where=f"call #{sink_call.index}",
        ))
    else:
        for call in reversed(calls):
            if _effect_guess(call.tool) == "EXPORT":
                sink_call = call
                sink_arg = next(iter(call.args), "")
                break
    if sink_call is not None:
        findings.append(Finding(
            "sink", f"{sink_call.tool} looks like the call that left the system",
            where=f"call #{sink_call.index}",
        ))
    else:
        unresolved.append("violation — no outbound call identified; name the tool whose call was the harm")

    untrusted_tool = injection[0].tool if injection else (
        calls[max(0, (sink_call.index if sink_call else len(calls)) - 1)].tool
        if len(calls) > 1 else calls[0].tool
    )
    if injection:
        source_call, path, text = injection
        findings.append(Finding(
            "injection", text.strip()[:400],
            where=f"{source_call.tool} result at {path or 'result'}",
        ))
        findings.append(Finding(
            "untrusted_source", f"{source_call.tool} returned attacker-reachable content",
            where=f"call #{source_call.index}",
        ))
    else:
        unresolved.append(
            "injection — no instruction-shaped text found in any tool result; paste the "
            "content that hijacked the run and say which tool returned it"
        )

    # The reference runner drives a read → sink slice whose sink argument is
    # literally `recipient` (runner._faithful_input_key reads the task_success
    # predicate for `args.recipient`, and run_trial binds that name). A draft that
    # kept the incident's own argument name would emit a violation predicate on an
    # argument the runner never binds: the attack would land and the predicate
    # would quietly never fire — ASR 0 for a reason nobody could see. So the draft
    # renames it and SAYS so, here and in `unresolved`.
    observed_sink_arg = sink_arg
    if sink_arg and sink_arg != "recipient":
        sink_arg = "recipient"
        findings.append(Finding(
            "sink", f"argument `{observed_sink_arg}` renamed to `recipient`",
            where=f"call #{sink_call.index}" if sink_call else "",
        ))
        unresolved.append(
            f"sink argument — your incident calls it `{observed_sink_arg}`; the reference "
            f"runner drives a read → sink slice whose argument is `recipient`, so the draft "
            f"renames it. The measurement is unaffected; the argument name in the resulting "
            f"trace will not match your logs"
        )

    # ── the draft itself ─────────────────────────────────────────────────────
    fixtures: dict[str, Any] = {}
    for call in calls:
        if call.result is not None and call.tool not in fixtures:
            fixtures[call.tool] = {"result": call.result}
    if injection:
        source_call, path, _text = injection
        fixtures.setdefault(source_call.tool, {"result": source_call.result})
        fixtures[source_call.tool]["injection_placement"] = {
            "field": path, "mode": "replace",
        }
    else:
        unresolved.append("fixtures.injection_placement — where the payload sat in the tool result")

    driving = [sink_arg] if sink_arg else []
    tools_seen: dict[str, ObservedCall] = {}
    for call in calls:
        tools_seen.setdefault(call.tool, call)
    # the path the injection sat at, as the vector check spells it: `result.` +
    # the placement field. Without a placement we can only name the whole result,
    # and the draft says so in `unresolved`.
    #
    # The INDEX is stripped: `_expand_field` understands `transactions[]` and not
    # `transactions[1]`, so a literal index taints nothing, the sink argument is
    # minted clean, and the reconstruction reports ASR 0 in both arms — an attack
    # that silently cannot land is worse than a loud failure. The concrete index
    # stays on `injection_placement`, which is where the payload literally goes.
    injected_path = (
        f"result.{_INDEX_RE.sub('[]', injection[1])}" if injection and injection[1] else "result"
    )
    manifests = []
    for call in tools_seen.values():
        is_sink = bool(sink_call) and call.tool == sink_call.tool  # type: ignore[union-attr]
        manifest = _manifest(
            call,
            untrusted_path=injected_path if call.tool == untrusted_tool else None,
            driving=driving if is_sink else [],
        )
        if is_sink and observed_sink_arg and observed_sink_arg != sink_arg:
            properties: dict[str, Any] = manifest["args_schema"]["properties"]
            properties[sink_arg] = properties.pop(observed_sink_arg, {"type": "string"})
        manifests.append(manifest)

    violation: dict[str, Any] = {}
    if sink_call and sink_arg:
        violation = {
            "event": "tool_call",
            "tool": sink_call.tool,
            "where": {f"prov(args.{sink_arg})": {"provenance_is": "untrusted_derived"}},
        }
    else:
        unresolved.append("violation predicate — which call, on which argument, counts as the breach")

    # The reference runner drives a read → sink slice whose sink argument is
    # literally `recipient` (runner._faithful_input_key reads the task_success
    # predicate for `args.recipient`). An incident whose sink argument is called
    # something else still runs — but under a renamed argument, and that is a
    # difference between the model and the incident, so it is stated rather than
    # smoothed over.
    # task_success is left EMPTY on purpose when the legitimate value is not
    # visible in the recording. Inventing one would quietly define what "the
    # agent did its job" means, and utility is half of every number here.
    task_success: dict[str, Any] = {}
    unresolved.append(
        "task_success predicate — what the agent had to do for the run to count as useful"
    )

    scenario: dict[str, Any] = {
        "schema_version": "scenario/v1",
        "name": _slug(name),
        "task": task,
        "tools": [{"$ref": tool} for tool in tools_seen],
        "fixtures": fixtures,
        "injection": {
            "text": injection[2].strip() if injection else "",
            "goal": "reconstructed from the incident — describe the attacker's objective",
        },
        "violation": violation,
        "task_success": task_success,
        # machine-readable, not just prose: a publication built from a run of
        # this scenario has to STATE that it models an incident rather than
        # reproducing one, and a limitation that depends on someone remembering
        # to read `notes` is not a limitation.
        "reconstructed_from": {"fidelity": FIDELITY},
        "notes": (
            "Reconstructed from a non-Axor recording by heuristic attribution. This is a "
            "MODEL of the incident, not the incident: the recording carried no value "
            "provenance, so nothing here was replayed and no verdict was computed."
        ),
    }
    return Reconstruction(
        scenario=scenario, manifests=manifests, calls=calls,
        findings=findings, unresolved=unresolved,
    )


# ── the confirmed draft becomes a runnable experiment ────────────────────────

def build_experiment(
    scenario: dict[str, Any],
    manifests: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    repeats: int = 10,
    run_mode: str = "compare",
    agent_ref: str = "scripted@0.6",
) -> dict[str, Any]:
    """Wrap a CONFIRMED scenario into an `.axl` document.

    From here on nothing is reconstructed: this is an ordinary Lab experiment
    that happens to have been authored from an incident, and the trace it
    produces is a real one.

    `agent_ref` is the axis that decides what the result MEANS. The scenario is
    the incident's world; the agent is the thing under test, and it was never in
    the recording:

      * their agent, wrapped (a connected runtime) — governance behaviour on
        THEIR agent, which is the actual deliverable;
      * `scripted@…`, the stand-in — proves the MECHANISM fires on this shape of
        incident, and nothing about their agent. Legitimate for first contact
        before any integration exists; never presentable as their result, which
        is why `store._limitations_for` stamps it on the publication rather than
        leaving it to the screen that happened to render it.
    """
    return {
        "experiment": {
            "schema_version": "experiment/v1",
            "id": f"exp_{scenario['name']}",
            "type": "benchmark",
            "scenario_ids": [scenario["name"]],
            "conditions": conditions,
            "repeats": repeats,
            "agent_ref": agent_ref,
            "run_mode": run_mode,
        },
        "scenarios": [scenario],
        "tool_manifests": manifests,
    }
