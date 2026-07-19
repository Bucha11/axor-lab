"""HTML rendering for the catalog, publication page, and EvidenceCase.

Threat-model §4: every string in a bundle/publication is untrusted (uploaded
JSON). All interpolated content goes through `esc` — there is no path that
injects raw content into markup. claims.md: the page separates a *Exactly
replayable* block from a *Statistically reproducible* block and never merges
them; it prints two reproduce commands with distinct meaning. Terminology:
only ungoverned/governed/compare; "deterministic" never attaches to a live
aggregate (enforced by tests/test_terminology.py).
"""

from __future__ import annotations

from html import escape

from lab_runner import build_evidence_case, default_registry

from .store import StoredPublication

_STYLE = """
body{font:15px/1.5 system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{font-size:1.5rem} h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:.4rem;font-size:.8rem;margin-right:.3rem;background:#eef}
.axis{background:#f6f6f6;padding:.5rem .8rem;border-radius:.5rem;margin:.3rem 0;display:inline-block}
.claim{border-left:3px solid #58a;padding:.4rem .8rem;margin:.5rem 0;background:#f8fbff}
.claim.stat{border-left-color:#a85}
code,pre{background:#f4f4f4;padding:.1rem .3rem;border-radius:.3rem;font-size:.85em}
pre{padding:.6rem;overflow-x:auto} table{border-collapse:collapse;width:100%} td,th{border:1px solid #ddd;padding:.3rem .5rem;text-align:left}
.deny{color:#b00;font-weight:600} .allow{color:#494} a{color:#36c}
.note{color:#666;font-size:.9em}
"""


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{_STYLE}</style></head><body>{body}</body></html>"
    )


def render_catalog(publications: list[StoredPublication]) -> str:
    rows = []
    for stored in sorted(
        publications, key=lambda s: str(s.publication["publication_id"])
    ):
        pub = stored.publication
        axes = stored.axes()
        reproductions: dict[str, object] = axes["reproductions"]  # type: ignore[assignment]
        pid = esc(pub["publication_id"])
        rows.append(
            f"<tr><td><a href='/e/{pid}'>{esc(pub['question'])}</a></td>"
            f"<td>{_provenance_badges(axes)}"
            f"<span class='note'>{esc(reproductions['count'])} reproduction(s)</span></td></tr>"
        )
    table = (
        "<table><tr><th>Question</th><th>Provenance</th></tr>"
        + ("".join(rows) or "<tr><td colspan='2'>No published experiments yet.</td></tr>")
        + "</table>"
    )
    intro = (
        "<p class='note'>Published experiments are re-runnable, forkable, citable "
        "artifacts. Governance runs are <b>compare</b> experiments: the same agent, "
        "same model, <b>ungoverned</b> vs <b>governed</b>.</p>"
    )
    return _page("Axor Lab — Catalog", f"<h1>Axor Lab — Catalog</h1>{intro}{table}")


def render_publication(stored: StoredPublication) -> str:
    pub = stored.publication
    axes = stored.axes()
    claims: list[dict[str, object]] = pub["claims"]  # type: ignore[assignment]
    exact = [c for c in claims if c["kind"] == "exactly_replayable"]
    statistical = [c for c in claims if c["kind"] == "statistically_reproducible"]

    body = [f"<h1>{esc(pub['question'])}</h1>"]
    body.append("<p>" + _provenance_badges(axes) + "</p>")

    body.append("<h2>Exactly replayable</h2>")
    body.append(
        "<p class='note'>Governance verdicts over frozen traces. Deterministic, "
        "no confidence interval, reproducible bit-for-bit given the pinned kernel.</p>"
    )
    body.extend(
        f"<div class='claim'>{esc(c['text'])}</div>" for c in exact
    ) or body.append("<p class='note'>No exact claims.</p>")

    body.append("<h2>Statistically reproducible</h2>")
    body.append(
        "<p class='note'>Aggregates over live runs. Stochastic, carry a CI, "
        "reproduced by re-running — matched within the interval, never bit-for-bit.</p>"
    )
    body.extend(
        f"<div class='claim stat'>{esc(c['text'])}</div>" for c in statistical
    )

    body.append("<h2>Methodology</h2>")
    environment: dict[str, object] = stored.bundle["environment"]  # type: ignore[assignment]
    body.append(
        f"<p>Kernel <code>{esc(environment['kernel_version'])}</code>; "
        f"model <code>{esc(environment['model']['id'])}</code>. "  # type: ignore[index]
        "Conditions differ only in enforcement (ungoverned vs governed).</p>"
    )
    body.append("<table><tr><th>Trial</th><th>Condition</th><th>Verdict (replayed)</th></tr>")
    for trace in sorted(stored.traces.values(), key=lambda t: str(t["trace_id"]))[:12]:
        verdict = _final_verdict(trace)
        cls = "deny" if verdict == "DENY" else "allow"
        tid = esc(trace["trace_id"])
        body.append(
            f"<tr><td><a href='/e/{esc(pub['publication_id'])}/evidence/{tid}'>{tid}</a></td>"
            f"<td>{esc(trace['trial']['condition_id'])}</td>"  # type: ignore[index]
            f"<td class='{cls}'>{esc(verdict)}</td></tr>"
        )
    body.append("</table>")

    body.append("<h2>Reproduce</h2>")
    body.append(
        "<pre># replay the governance verdicts (exact)\n"
        f"axor-lab replay ./{esc(pub['publication_id'])}\n\n"
        "# fresh live run (new sample, reproduces within CI)\n"
        "axor-lab run experiment.axl --out ./bundle</pre>"
    )

    body.append("<h2>Limitations</h2><ul>")
    for limitation in pub.get("limitations", []):  # type: ignore[union-attr]
        body.append(f"<li>{esc(limitation)}</li>")
    body.append("</ul>")

    body.append(
        f"<p class='note'>License {esc(pub['license'])}. "
        "This publication is immutable; reproductions accrue separately.</p>"
    )
    return _page(str(pub["question"]), "".join(body))


def render_evidence(stored: StoredPublication, trace_id: str) -> str:
    trace = stored.traces[trace_id]
    bundle = stored.bundle
    scenario = _scenario_for(bundle, trace)
    condition = _enforcing_condition(bundle)
    kernel = default_registry((str(condition["kernel"]),)).get(str(condition["kernel"]))
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    case = build_evidence_case(trace, scenario, condition, kernel, manifests)
    chain: dict[str, object] = case["chain"]  # type: ignore[assignment]
    modes: dict[str, object] = case["modes"]  # type: ignore[assignment]

    body = [f"<h1>EvidenceCase <code>{esc(trace_id)}</code></h1>"]
    body.append(f"<p><b>Injection:</b> {esc(chain['injection']['text'])}</p>")  # type: ignore[index]

    body.append("<h2>Provenance chain</h2><table><tr><th>value</th><th>labels</th><th>sources</th></tr>")
    for value in chain["provenance"]:  # type: ignore[union-attr]
        sources = ", ".join(esc(s.get("origin_ref", s.get("kind"))) for s in value["sources"])
        body.append(
            f"<tr><td>{esc(value.get('preview', value['value_id']))}</td>"
            f"<td>{esc(', '.join(value['labels']))}</td><td>{sources}</td></tr>"
        )
    body.append("</table>")

    verdict: dict[str, object] = chain["verdict"]  # type: ignore[assignment]
    vcls = "deny" if verdict["verdict"] == "DENY" else "allow"
    body.append(
        f"<h2>Gated call &amp; verdict</h2><p>Tool <code>{esc(chain['gated_call']['tool'])}</code> "  # type: ignore[index]
        f"&rarr; <span class='{vcls}'>{esc(verdict['verdict'])}</span> "
        f"(gate <code>{esc(verdict['gate'])}</code>).</p>"
    )
    body.append(f"<p class='note'>{esc(case['note'])}</p>")

    body.append("<h2>Modes</h2>")
    observed: dict[str, object] = modes["observed"]  # type: ignore[assignment]
    body.append(
        f"<p><b>Observed ({esc(observed['condition_id'])}):</b> "
        f"{esc(', '.join(observed['verdicts']))}</p>"
    )
    counterfactual: dict[str, object] = modes["counterfactual_policy_replay"]  # type: ignore[assignment]
    body.append(
        f"<p><b>Counterfactual: policy replay:</b> {esc(', '.join(counterfactual['verdicts']))} "
        f"<span class='note'>{esc(counterfactual['caveat'])}</span></p>"
    )
    if "observed_governed_twin" in modes:
        twin: dict[str, object] = modes["observed_governed_twin"]  # type: ignore[assignment]
        body.append(f"<p><b>Observed governed twin:</b> {esc(', '.join(twin['verdicts']))}</p>")
    if "fidelity_warning" in case:
        body.append(f"<p class='note'>&#9888; {esc(case['fidelity_warning'])}</p>")

    body.append(f"<p><a href='/e/{esc(stored.publication['publication_id'])}'>&larr; back</a></p>")
    return _page(f"EvidenceCase {trace_id}", "".join(body))


def _provenance_badges(axes: dict[str, object]) -> str:
    reproductions: dict[str, object] = axes["reproductions"]  # type: ignore[assignment]
    return (
        f"<span class='badge'>origin: {esc(axes['origin'])}</span>"
        f"<span class='badge'>integrity: {esc(axes['integrity'])}</span>"
        f"<span class='badge'>reproduced &times;{esc(reproductions['count'])}</span>"
    )


def _final_verdict(trace: dict[str, object]) -> str:
    verdicts = [
        str(e["decision"]["verdict"])  # type: ignore[index]
        for e in trace["events"]  # type: ignore[union-attr]
        if e.get("type") == "gate_decision"
    ]
    return verdicts[-1] if verdicts else "—"


def _scenario_for(bundle: dict[str, object], trace: dict[str, object]) -> dict[str, object]:
    scenario_id = str(trace["trial"]["scenario_id"])  # type: ignore[index]
    for scenario in bundle["scenarios"]:  # type: ignore[union-attr]
        if scenario["name"] == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def _enforcing_condition(bundle: dict[str, object]) -> dict[str, object]:
    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        if condition["enforcement"] == "on":
            return condition
    raise KeyError("no enforcement-on condition")
