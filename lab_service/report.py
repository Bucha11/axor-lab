"""The run, as something you can paste into a paper.

Every export door this product had handed over JSON: `artifact/v1`, a
reproduction package, a CP handoff directory. All correct, and none of them
what someone writing a paper needs — nobody pastes a bundle into a results
section. They paste a TABLE, a test statistic, a methods paragraph and a
citation, and until now every one of those was retyped by hand out of a JSON
viewer. Retyping is where a number quietly stops matching its evidence.

So this renders the same artifact three ways — Markdown to read, LaTeX to
compile, BibTeX to cite — from the artifact itself, never from prose written
alongside it.

WHAT MAKES IT WORTH GENERATING rather than writing by hand is the column no
author would think to add: every row says whether its number was DERIVED from
the traces or merely REPORTED by the runner. A latency mean and an attack-
success rate look identical in a results table and are not the same kind of
claim, and the artifact is the only place that distinction survives. The same
goes for the interval — a Wilson CI and an observed range print alike, so the
range is marked as what it is rather than borrowed as a confidence interval.

Nothing here prints, exits, or speaks HTTP: `lab_runner/cli.py` writes the
files, `lab_server` serves them, and neither can drift from the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_analysis import metric_is_derived, missingness
from lab_contracts import content_hash

from .outcomes import Outcome

REPORT_FORMATS = ("md", "tex", "bib")

_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


class ReportError(Exception):
    """The artifact carries nothing a results table could be built from."""


@dataclass(frozen=True)
class PaperReport:
    """One run, rendered for a manuscript."""

    outcome: Outcome
    markdown: str
    latex: str
    bibtex: str
    #: what the table CANNOT say, surfaced rather than left for a reader to
    #: infer from a footnote they will not read
    caveats: tuple[str, ...] = ()

    def render(self, fmt: str) -> str:
        if fmt not in REPORT_FORMATS:
            raise ReportError(f"unknown report format {fmt!r}; known: {list(REPORT_FORMATS)}")
        return {"md": self.markdown, "tex": self.latex, "bib": self.bibtex}[fmt]


def _tex(value: object) -> str:
    """Escape for LaTeX text mode.

    Not optional politeness: condition ids like `governed_allowlist` and metrics
    like `task_success` are full of underscores, and an unescaped one is a
    manuscript that does not compile — the exact failure that makes a generated
    table less useful than retyping it.
    """
    return "".join(_LATEX_ESCAPES.get(ch, ch) for ch in str(value))


def _bundle_of(document: dict[str, object]) -> dict[str, object]:
    """Accept an artifact/v1 or a bare bundle/v1 — a caller holding either has
    the same question."""
    inner = document.get("bundle")
    return inner if isinstance(inner, dict) else document


def _number(value: float) -> str:
    """Enough digits to be re-checkable, few enough to read in a table."""
    if value != value or value in (float("inf"), float("-inf")):  # noqa: PLR0124
        return "—"
    magnitude = abs(value)
    if magnitude != 0 and (magnitude < 1e-3 or magnitude >= 1e6):
        return f"{value:.3g}"
    if magnitude >= 100:
        return f"{value:.1f}"
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _p_value(value: float, *, latex: bool = False) -> str:
    """A p-value in the form a reviewer expects, and never rounded to 0.

    `p = 0.000` is the classic table lie: it reads as impossible rather than
    small. Below the printing threshold this says `p < 0.001`, which is what the
    number actually supports. The RELATION travels with the number so a caller
    cannot write "p = < 0.001".
    """
    if value < 0.001:
        return r"$p < 0.001$" if latex else "p < 0.001"
    return f"$p = {value:.3f}$" if latex else f"p = {value:.3f}"


def _proportion(value: float) -> str:
    """A rate, printed as a proportion: fixed decimals, no float noise.

    A Wilson bound comes back as 1.39e-17 for a zero rate and 0.9999999 for a
    complete one. Both are exactly the arithmetic and neither belongs in a
    table — printed raw they read as a bug, and an author retypes them by hand,
    which is the thing this module exists to stop.
    """
    return f"{min(max(value, 0.0), 1.0):.3f}"


def _interval(aggregate: dict[str, object]) -> tuple[str, bool]:
    """The interval cell, and whether it is a confidence interval at all.

    A Wilson interval and the observed range of a latency print identically and
    mean nothing alike. Returning the flag lets the table mark the range instead
    of letting it pass as a CI.
    """
    interval: dict[str, object] = aggregate.get("interval") or {}  # type: ignore[assignment]
    method = str(interval.get("method", "none"))
    if "low" not in interval or "high" not in interval:
        return "—", False
    cell = _proportion if str(aggregate.get("estimator", "")) == "rate" else _number
    low, high = cell(float(interval["low"])), cell(float(interval["high"]))  # type: ignore[arg-type]
    return f"[{low}, {high}]", method not in ("none", "")


def _rows(bundle: dict[str, object]) -> list[dict[str, object]]:
    aggregates: list[dict[str, object]] = list(bundle.get("aggregates") or [])  # type: ignore[arg-type]
    rows: list[dict[str, object]] = []
    for aggregate in aggregates:
        metric = str(aggregate["metric"])
        cell, is_ci = _interval(aggregate)
        rows.append({
            "metric": metric,
            "condition_id": str(aggregate["condition_id"]),
            "estimator": str(aggregate.get("estimator", "")),
            "estimate": (
                _proportion(float(aggregate["estimate"]))  # type: ignore[arg-type]
                if str(aggregate.get("estimator", "")) == "rate"
                else _number(float(aggregate["estimate"]))  # type: ignore[arg-type]
            ),
            "interval": cell,
            "is_ci": is_ci,
            "n": int(aggregate["n"]),  # type: ignore[arg-type]
            "derived": metric_is_derived(metric),
            "test": aggregate.get("test"),
        })
    return rows


def _test_sentence(row: dict[str, object], *, latex: bool) -> str:
    """One comparison, written the way a results section writes one."""
    test: dict[str, object] = row["test"]  # type: ignore[assignment]
    name = str(test.get("name", ""))
    baseline = str(test.get("vs", ""))
    metric = str(row["metric"])
    treated = str(row["condition_id"])
    esc = _tex if latex else (lambda v: str(v))
    code = (lambda v: f"\\texttt{{{_tex(v)}}}") if latex else (lambda v: f"`{v}`")
    p_raw = test.get("p", test.get("p_value"))
    p_text = _p_value(float(p_raw), latex=latex) if isinstance(p_raw, (int, float)) else "p = —"
    if name == "mcnemar":
        discordant: dict[str, object] = test.get("discordant") or {}  # type: ignore[assignment]
        return (
            f"{esc(metric)} under {code(treated)} versus {code(baseline)}: "
            f"McNemar's exact test over {test.get('paired_n', '?')} matched pairs "
            f"(discordant b={discordant.get('b', '?')}, c={discordant.get('c', '?')}; "
            f"effective n={test.get('effective_n', '?')}), {p_text}."
        )
    if name == "two_proportion":
        difference = test.get("difference")
        diff_text = _number(float(difference)) if isinstance(difference, (int, float)) else "—"
        return (
            f"{esc(metric)} under {code(treated)} versus {code(baseline)}: "
            f"two-proportion test on independent samples, difference {diff_text}, "
            f"{p_text}. Exploratory — the arms were sampled independently, so "
            "this is not a paired significance test."
        )
    return (
        f"{esc(metric)} under {code(treated)} versus {code(baseline)}: "
        f"{esc(name) or 'test'}, {p_text}."
    )


def _repeats(bundle: dict[str, object]) -> int:
    trials: list[dict[str, object]] = list(bundle.get("trials") or [])  # type: ignore[arg-type]
    indices = {int(t.get("repeat_index", 0)) for t in trials}  # type: ignore[arg-type]
    return max(indices) + 1 if indices else 0


def _kernels(bundle: dict[str, object]) -> str:
    environment: dict[str, object] = bundle.get("environment") or {}  # type: ignore[assignment]
    many = environment.get("kernel_versions")
    if isinstance(many, list) and many:
        return ", ".join(str(k) for k in many)
    return str(environment.get("kernel_version", "unpinned"))


def _methods(bundle: dict[str, object], suite: dict[str, object] | None) -> list[str]:
    """The paragraph a Methods section needs, assembled from what RAN.

    Written from the artifact rather than from memory, because every value here
    is one a author would otherwise transcribe: the kernel pin, the per-arm
    config hash, the design, the denominator.
    """
    environment: dict[str, object] = bundle.get("environment") or {}  # type: ignore[assignment]
    model: dict[str, object] = environment.get("model") or {}  # type: ignore[assignment]
    design: dict[str, object] = environment.get("experiment_design") or {}  # type: ignore[assignment]
    conditions: list[dict[str, object]] = list(bundle.get("conditions") or [])  # type: ignore[arg-type]
    scenarios: list[dict[str, object]] = list(bundle.get("scenarios") or [])  # type: ignore[arg-type]
    trials: list[dict[str, object]] = list(bundle.get("trials") or [])  # type: ignore[arg-type]
    repeats = _repeats(bundle)

    what = "an experiment"
    if suite:
        what = f"suite {suite.get('name') or suite.get('id')} v{suite.get('version', '?')}"
    arms = ", ".join(
        f"{c.get('id')} (enforcement {c.get('enforcement')})" for c in conditions
    ) or "no declared conditions"
    lines = [
        f"We ran {what} over {len(scenarios)} scenario(s) and {len(conditions)} arm(s) "
        f"— {arms} — with {repeats} repeat(s) per scenario-arm, for "
        f"{len(trials)} planned trials.",
        f"The agent was {model.get('provider', 'unknown')}/{model.get('id', 'unknown')}; "
        f"the governance kernel was pinned at {_kernels(bundle)}.",
    ]
    kind = str(design.get("kind", "")) or "undeclared"
    if len(conditions) < 2:
        # the design block still says matched_pairs (it describes the AGENT, not
        # the plan), and repeating it here would describe a pairwise comparison
        # of one arm against nothing
        lines.append(
            "Only one arm ran, so no between-arm comparison is made and no "
            "comparison design applies."
        )
    elif kind == "matched_pairs":
        lines.append(
            "The comparison design is matched pairs: the agent is recorded as "
            "deterministic, so each arm sees the same unit and the arms are compared "
            "pairwise. The design is recorded at run time, not asserted at analysis time."
        )
    elif kind == "independent_samples":
        lines.append(
            "The comparison design is independent samples: the agent is not recorded "
            "as deterministic, so each arm was drawn separately and comparisons are "
            "exploratory rather than paired."
        )
    else:
        lines.append("The run recorded no comparison design, so no design is asserted here.")

    hashes = [
        f"{c.get('id')}={c.get('config_hash')}" for c in conditions if c.get("config_hash")
    ]
    if hashes:
        lines.append(
            "Each arm's governor configuration is content-addressed: " + "; ".join(hashes) + "."
        )
    if _no_enforcing_arm(bundle):
        lines.append(
            "No arm enforced: the kernel observed every trial and gated nothing, "
            "so this run measures the agent's unprotected behaviour and makes no "
            "claim about governance."
        )
    if trials:
        lines.append("Denominator: " + missingness(trials).display() + ".")
    return lines


def _no_enforcing_arm(bundle: dict[str, object]) -> bool:
    conditions: list[dict[str, object]] = list(bundle.get("conditions") or [])  # type: ignore[arg-type]
    return not any(str(c.get("enforcement", "")) == "on" for c in conditions)


def _unattacked_scenarios(bundle: dict[str, object]) -> tuple[int, int]:
    """(scenarios with no breach predicate, total). A scenario with no attack
    model cannot contribute to a breach rate, so ASR's denominator is smaller
    than task success's BY DESIGN — which a table shows as two different n and a
    reviewer reads as missing data."""
    scenarios: list[dict[str, object]] = list(bundle.get("scenarios") or [])  # type: ignore[arg-type]
    return sum(1 for s in scenarios if s.get("violation") is None), len(scenarios)


def _caveats(rows: list[dict[str, object]], bundle: dict[str, object]) -> list[str]:
    caveats: list[str] = []
    # FIRST, because it changes what every row means. An observe-only run is the
    # likeliest first run anyone does — wrap the agent, watch, enforce nothing —
    # and an ASR printed with no treated arm reads as a finding about a governed
    # system when it is a measurement of an unprotected one.
    if _no_enforcing_arm(bundle):
        caveats.append(
            "NO ARM ENFORCED: every trial ran with enforcement off, the kernel "
            "observing only. These are BASELINE measurements of an ungoverned "
            "agent. Nothing here shows what governance would change — that needs "
            "a second arm with enforcement on, compared against this one."
        )
    unattacked, total = _unattacked_scenarios(bundle)
    if unattacked and total > unattacked and len({r["n"] for r in rows}) > 1:
        caveats.append(
            f"Denominators differ between metrics by DESIGN, not by missing data: "
            f"{unattacked} of {total} scenarios declare no attack model, so a breach "
            "rate is measured over the attacked trials only while task success is "
            "measured over all of them."
        )
    if any(not r["derived"] for r in rows):
        caveats.append(
            "Rows marked self-reported were measured by the runner and appear in no "
            "trace, so a verifier can re-apply the estimator but cannot check the "
            "observations. Only derived rows can back a reproducibility claim."
        )
    if any(not r["is_ci"] for r in rows):
        caveats.append(
            "An interval on a self-reported row is the OBSERVED RANGE of the "
            "per-trial values: not a confidence interval, and — for a sum — not "
            "an interval on the estimate either."
        )
    environment: dict[str, object] = bundle.get("environment") or {}  # type: ignore[assignment]
    provider = str((environment.get("model") or {}).get("provider", ""))  # type: ignore[union-attr]
    if provider in ("scripted", "cassette"):
        caveats.append(
            f"The agent is a {provider} stand-in, not a live model: these numbers "
            "describe the harness, not a production model's behaviour."
        )
    return caveats


def _citation_key(bundle_ref: str, publication: dict[str, object] | None) -> str:
    ident = str(publication["publication_id"]) if publication else bundle_ref
    short = ident.split(":")[-1][:8]
    return f"axorlab:{short}"


def _bibtex(
    bundle: dict[str, object],
    bundle_ref: str,
    publication: dict[str, object] | None,
    url: str | None,
    title: str,
) -> str:
    """A citation whose key IS the evidence.

    A published run is content-addressed and immutable, so the entry can cite
    the exact bytes rather than "the version on the website" — which is more
    than most artifacts referenced in a paper can say. An UNPUBLISHED artifact
    gets an entry too, and it says so: the reader is told there is no resolvable
    location, rather than being handed a URL that does not exist.
    """
    key = _citation_key(bundle_ref, publication)
    created = str(bundle.get("created", ""))[:4] or "n.d."
    fields = [
        ("title", title),
        ("year", created),
        ("howpublished", "Axor Lab artifact"),
    ]
    note = [f"bundle content hash {bundle_ref}"]
    if publication:
        note.append(f"publication {publication['publication_id']} (immutable, content-addressed)")
        integrity = publication.get("statistics_integrity")
        if integrity:
            note.append(f"statistics: {integrity}")
    else:
        note.append(
            "NOT PUBLISHED — no resolvable location; cite the hash or publish the artifact first"
        )
    if url:
        fields.append(("url", url))
    fields.append(("note", "; ".join(note)))
    body = ",\n".join(f"  {name:<12} = {{{value}}}" for name, value in fields)
    return f"@misc{{{key},\n{body}\n}}\n"


def build_paper_report(
    document: dict[str, object],
    *,
    publication: dict[str, object] | None = None,
    url: str | None = None,
    title: str | None = None,
) -> PaperReport:
    """Render one artifact (or bare bundle) as Markdown, LaTeX and BibTeX."""
    bundle = _bundle_of(document)
    rows = _rows(bundle)
    if not rows:
        raise ReportError(
            "this artifact declares no aggregates, so there is no results table to "
            "write — run a suite that measures something first"
        )
    suite = document.get("suite") if isinstance(document.get("suite"), dict) else None
    bundle_ref = content_hash(bundle)
    heading = title or (
        str(publication.get("question")) if publication and publication.get("question")
        else str((suite or {}).get("name") or (suite or {}).get("id") or "Axor Lab run")
    )
    methods = _methods(bundle, suite)  # type: ignore[arg-type]
    caveats = _caveats(rows, bundle)
    tests = [r for r in rows if r.get("test")]
    return PaperReport(
        outcome=Outcome.OK,
        markdown=_markdown(heading, rows, tests, methods, caveats, bundle_ref, document),
        latex=_latex(heading, rows, tests, methods, caveats, bundle_ref),
        bibtex=_bibtex(bundle, bundle_ref, publication, url, heading),
        caveats=tuple(caveats),
    )


def _evidence_word(derived: bool) -> str:
    return "derived" if derived else "self-reported"


def _markdown(
    heading: str,
    rows: list[dict[str, object]],
    tests: list[dict[str, object]],
    methods: list[str],
    caveats: list[str],
    bundle_ref: str,
    document: dict[str, object],
) -> str:
    out = [f"# {heading}", ""]
    out += ["## Results", ""]
    out += ["| Metric | Arm | Estimator | Estimate | Interval | n | Evidence |",
            "|---|---|---|---:|---|---:|---|"]
    for row in rows:
        marker = "" if row["is_ci"] else " †"
        out.append(
            f"| `{row['metric']}` | `{row['condition_id']}` | {row['estimator']} | "
            f"{row['estimate']} | {row['interval']}{marker} | {row['n']} | "
            f"{_evidence_word(bool(row['derived']))} |"
        )
    if any(not r["is_ci"] for r in rows):
        out += ["", "† observed range of the per-trial values — not a confidence interval, and "
         "not an interval on the estimate itself."]
    if tests:
        out += ["", "## Comparisons", ""]
        out += [f"- {_test_sentence(row, latex=False)}" for row in tests]
    out += ["", "## Methods", ""] + methods
    if caveats:
        out += ["", "## What this does not say", ""] + [f"- {c}" for c in caveats]
    artifact_id = document.get("artifact_id")
    out += ["", "## Provenance", ""]
    if artifact_id:
        out.append(f"- Artifact: `{artifact_id}`")
    out.append(f"- Bundle content hash: `{bundle_ref}`")
    reproduce: dict[str, object] = document.get("reproduce") or {}  # type: ignore[assignment]
    if reproduce.get("command"):
        out.append(f"- Reproduce: `{reproduce['command']}`")
    return "\n".join(out) + "\n"


def _latex(
    heading: str,
    rows: list[dict[str, object]],
    tests: list[dict[str, object]],
    methods: list[str],
    caveats: list[str],
    bundle_ref: str,
) -> str:
    label = "tab:" + bundle_ref.split(":")[-1][:8]
    out = [
        "% Generated by Axor Lab from the artifact — do not retype these numbers.",
        "% Requires: \\usepackage{booktabs}",
        "\\begin{table}[t]",
        "  \\centering",
        "  \\begin{tabular}{llrrlrl}",
        "    \\toprule",
        "    Metric & Arm & Estimator & Estimate & Interval & $n$ & Evidence \\\\",
        "    \\midrule",
    ]
    for row in rows:
        marker = "" if row["is_ci"] else "$^{\\dagger}$"
        out.append(
            f"    \\texttt{{{_tex(row['metric'])}}} & \\texttt{{{_tex(row['condition_id'])}}} & "
            f"{_tex(row['estimator'])} & {row['estimate']} & {_tex(row['interval'])}{marker} & "
            f"{row['n']} & {_evidence_word(bool(row['derived']))} \\\\"
        )
    out += ["    \\bottomrule", "  \\end{tabular}"]
    caption = [f"{_tex(heading)}."]
    if any(not r["is_ci"] for r in rows):
        caption.append(
            "$^{\\dagger}$ observed range of the per-trial values --- not a confidence "
            "interval, and not an interval on the estimate itself."
        )
    caption.append(
        "Rows marked \\emph{derived} were recomputed from the frozen traces; "
        "\\emph{self-reported} rows are the runner's own measurements."
    )
    out += [f"  \\caption{{{' '.join(caption)}}}", f"  \\label{{{label}}}", "\\end{table}", ""]
    if tests:
        out += ["% Comparisons"]
        out += [f"% {_test_sentence(row, latex=True)}" for row in tests]
        out.append("")
    out += ["% Methods"] + [f"% {_tex(line)}" for line in methods]
    if caveats:
        out += ["% Limitations"] + [f"% {_tex(line)}" for line in caveats]
    return "\n".join(out) + "\n"
