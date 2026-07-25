"""HTML rendering for the catalog, publication page, and EvidenceCase.

These are the SHAREABLE surface. A publication is meant to be pasted into a
thread, a paper, or a due-diligence packet, so it is server-rendered, works with
JavaScript off, is crawlable, and carries link-preview metadata — the SPA cannot
do any of those. The React app is the workbench; this is the artifact.

Threat-model §4: every string in a bundle/publication is untrusted (uploaded
JSON). All interpolated content goes through `esc` — there is no path that
injects raw content into markup. claims.md: the page separates a *Exactly
replayable* block from a *Statistically reproducible* block and never merges
them; it prints two reproduce commands with distinct meaning. Terminology:
only ungoverned/governed/compare; "deterministic" never attaches to a live
aggregate (enforced by tests/test_terminology.py).

The headline panel is held to one rule: it may only show what the run measured.
A deterministic stand-in agent has an injection-following rate that is a
PARAMETER, so its ungoverned attack-success rate is set by construction — the
panel says so, in place, rather than letting a dial be read as a finding.
"""

from __future__ import annotations

from html import escape

from lab_runner import (
    build_evidence_case,
    default_registry,
    evidence_condition,
    resolve_kernel,
)

from .errors import PublishRejected
from .store import StoredPublication

SITE_NAME = "Axor Lab"

# Palette: categorical slot 1 for data marks (one measure, so one hue — the
# condition labels carry identity, not colour), the fixed status steps for
# deltas only, and the chrome/ink roles. Dark is SELECTED from the same ramps
# against the dark surface, not an automatic inversion.
_STYLE = """
:root{
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --rule:#e1e0d9; --baseline:#c3c2b7; --ring:rgba(11,11,11,.10);
  --series-1:#2a78d6; --track:#e9e8e2;
  --good:#0ca30c; --good-ink:#006300; --critical:#d03b3b; --warning:#fab219;
  --accent-wash:#f1f6fd;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
    --rule:#2c2c2a; --baseline:#383835; --ring:rgba(255,255,255,.10);
    --series-1:#3987e5; --track:#262624;
    --good:#0ca30c; --good-ink:#0ca30c; --critical:#d03b3b; --warning:#fab219;
    --accent-wash:#15202c;
    color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781;
  --rule:#2c2c2a; --baseline:#383835; --ring:rgba(255,255,255,.10);
  --series-1:#3987e5; --track:#262624;
  --good:#0ca30c; --good-ink:#0ca30c; --critical:#d03b3b; --warning:#fab219;
  --accent-wash:#15202c;
  color-scheme:dark;
}
*{box-sizing:border-box}
body{font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;
  background:var(--plane);color:var(--ink);
  -webkit-font-smoothing:antialiased}
.wrap{max-width:880px;margin:0 auto;padding:2.5rem 1.25rem 4rem}
a{color:var(--series-1);text-underline-offset:2px}
a:hover{text-decoration-thickness:2px}
h1{font-size:1.75rem;line-height:1.25;letter-spacing:-.015em;margin:.2rem 0 .75rem;font-weight:650}
h2{font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  font-weight:600;margin:2.5rem 0 .75rem}
h3{font-size:.95rem;margin:0 0 .35rem;font-weight:600}
p{margin:.6rem 0}
.eyebrow{font-size:.75rem;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
  font-weight:600}
.note{color:var(--ink-2);font-size:.875rem;line-height:1.6}
.muted{color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
  padding:1.15rem 1.25rem}
.card + .card{margin-top:.6rem}
.rowline{display:flex;flex-wrap:wrap;gap:.4rem .75rem;align-items:center}
.meta{font-size:.8rem;color:var(--muted);
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.badge{display:inline-flex;align-items:center;gap:.3rem;padding:.2rem .55rem;
  border:1px solid var(--ring);border-radius:999px;font-size:.75rem;color:var(--ink-2);
  background:var(--surface);white-space:nowrap}
.badge b{font-weight:600;color:var(--ink)}
.chip{display:inline-flex;align-items:center;gap:.35rem;padding:.2rem .55rem;
  border-radius:999px;font-size:.78rem;font-weight:600;border:1px solid transparent}
.chip.good{color:var(--good-ink);background:color-mix(in srgb,var(--good) 12%,transparent);
  border-color:color-mix(in srgb,var(--good) 35%,transparent)}
.chip.bad{color:var(--critical);background:color-mix(in srgb,var(--critical) 12%,transparent);
  border-color:color-mix(in srgb,var(--critical) 35%,transparent)}
.chip.flat{color:var(--ink-2);background:var(--track);border-color:var(--ring)}
/* --- the measure panel: one hue, direct-labelled, recessive chrome --- */
.panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:.6rem}
.panel{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:1.1rem 1.25rem}
.panel .measure{font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.bars{margin:.9rem 0 0}
.bar{display:grid;grid-template-columns:1fr auto;gap:.15rem .75rem;align-items:baseline;
  margin-bottom:.7rem}
.bar:last-child{margin-bottom:0}
.bar .name{font-size:.85rem;color:var(--ink-2)}
.bar .val{font-size:.9rem;font-weight:650;font-variant-numeric:tabular-nums;color:var(--ink)}
.bar .track{grid-column:1 / -1;height:10px;background:var(--track);border-radius:5px;
  overflow:hidden}
.bar .fill{height:100%;background:var(--series-1);border-radius:0 4px 4px 0;min-width:0}
.bar .ci{grid-column:1 / -1;font-size:.72rem;color:var(--muted);
  font-variant-numeric:tabular-nums;margin-top:.15rem}
.delta{margin-top:.9rem;padding-top:.8rem;border-top:1px solid var(--rule)}
.hero{font-size:2rem;font-weight:680;letter-spacing:-.02em;line-height:1.1;margin:0}
.callout{border:1px solid var(--ring);border-left:3px solid var(--warning);
  background:var(--surface);border-radius:0 10px 10px 0;padding:.9rem 1.1rem;margin:.6rem 0}
.callout h3{font-size:.85rem}
.claim{border-left:3px solid var(--series-1);background:var(--accent-wash);
  border-radius:0 8px 8px 0;padding:.6rem .9rem;margin:.4rem 0;font-size:.92rem}
.claim.stat{border-left-color:var(--warning)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em;
  background:var(--track);padding:.1rem .35rem;border-radius:4px}
pre{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
  padding:.9rem 1rem;overflow-x:auto;font-size:.82rem;line-height:1.65;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre code{background:none;padding:0}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th{text-align:left;font-weight:600;color:var(--muted);font-size:.72rem;
  letter-spacing:.06em;text-transform:uppercase;padding:.4rem .6rem;
  border-bottom:1px solid var(--rule)}
td{padding:.45rem .6rem;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
td code{font-size:.8em}
td.nowrap{white-space:nowrap}
.deny{color:var(--critical);font-weight:650}
.allow{color:var(--good-ink);font-weight:600}
ul{margin:.5rem 0;padding-left:1.15rem}
li{margin:.25rem 0;font-size:.9rem;color:var(--ink-2)}
.entry{display:block;background:var(--surface);border:1px solid var(--ring);
  border-radius:12px;padding:1.1rem 1.25rem;text-decoration:none;color:inherit;
  margin-bottom:.6rem}
.entry:hover{border-color:var(--series-1)}
.entry .q{font-size:1.05rem;font-weight:600;line-height:1.35;color:var(--ink);
  margin-bottom:.55rem}
.empty{background:var(--surface);border:1px dashed var(--baseline);border-radius:12px;
  padding:2.5rem 1.25rem;text-align:center;color:var(--muted)}
.foot{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--rule);
  font-size:.8rem;color:var(--muted)}
"""


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def _page(title: str, body: str, description: str | None = None) -> str:
    """A document with link-preview metadata.

    Without og/twitter tags a pasted publication URL renders as a bare link —
    for an artifact whose whole distribution model is "share the evidence", the
    preview card is not decoration.
    """
    head = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{esc(title)}</title>",
        f"<meta property='og:site_name' content='{esc(SITE_NAME)}'>",
        "<meta property='og:type' content='article'>",
        f"<meta property='og:title' content='{esc(title)}'>",
        "<meta name='twitter:card' content='summary'>",
        f"<meta name='twitter:title' content='{esc(title)}'>",
    ]
    if description:
        head.append(f"<meta name='description' content='{esc(description)}'>")
        head.append(f"<meta property='og:description' content='{esc(description)}'>")
        head.append(f"<meta name='twitter:description' content='{esc(description)}'>")
    head.append(f"<style>{_STYLE}</style></head>")
    return "".join(head) + f"<body><div class='wrap'>{body}</div></body></html>"


# --------------------------------------------------------------------------
# measures: what the run actually reports, per metric, per condition
# --------------------------------------------------------------------------

#: metrics whose INCREASE is the bad direction. Everything else (utility, task
#: success) improves upward. Getting this backwards would paint a regression
#: green, so the direction is declared, never inferred from the numbers.
_LOWER_IS_BETTER = {"ASR", "asr", "attack_success_rate", "violation_rate"}

_METRIC_LABELS = {
    "ASR": "Attack success rate",
    "task_success_rate": "Task success rate",
}


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _baseline_and_treated(bundle: dict[str, object]) -> tuple[str | None, str | None]:
    """The (enforcement off, enforcement on) condition ids to compare."""
    baseline = treated = None
    for condition in bundle.get("conditions", []):  # type: ignore[union-attr]
        if condition.get("enforcement") == "on" and treated is None:
            treated = str(condition["id"])
        elif condition.get("enforcement") != "on" and baseline is None:
            baseline = str(condition["id"])
    return baseline, treated


def _by_metric(bundle: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for aggregate in bundle.get("aggregates", []):  # type: ignore[union-attr]
        grouped.setdefault(str(aggregate["metric"]), []).append(aggregate)
    return grouped


def _delta_chip(metric: str, base: float, treat: float) -> str:
    """A signed change, with an icon and a word — never colour alone."""
    points = (treat - base) * 100
    lower_better = metric in _LOWER_IS_BETTER
    if abs(points) < 0.05:
        return "<span class='chip flat'>= unchanged</span>"
    improved = (points < 0) if lower_better else (points > 0)
    arrow = "&darr;" if points < 0 else "&uarr;"
    word = "better" if improved else "worse"
    return (
        f"<span class='chip {'good' if improved else 'bad'}'>"
        f"{arrow} {abs(points):.1f} pp {word}</span>"
    )


def _measure_panels(bundle: dict[str, object]) -> str:
    """One panel per metric: a bar per condition, direct-labelled, plus the delta.

    Bars share ONE hue because this is one measure across two conditions, not two
    series — identity comes from the labels beside each bar, so nothing here
    depends on telling two colours apart.
    """
    grouped = _by_metric(bundle)
    if not grouped:
        return ""
    baseline_id, treated_id = _baseline_and_treated(bundle)
    panels = []
    for metric, aggregates in grouped.items():
        label = _METRIC_LABELS.get(metric, metric)
        ordered = sorted(
            aggregates,
            key=lambda a: (str(a["condition_id"]) != baseline_id, str(a["condition_id"])),
        )
        bars = []
        values: dict[str, float] = {}
        for aggregate in ordered:
            condition = str(aggregate["condition_id"])
            estimate = float(aggregate["estimate"])  # type: ignore[arg-type]
            values[condition] = estimate
            interval: dict[str, object] = aggregate.get("interval") or {}  # type: ignore[assignment]
            ci = ""
            if interval:
                ci = (
                    f"<div class='ci'>95% CI {_pct(float(interval['low']))}"  # type: ignore[arg-type]
                    f" – {_pct(float(interval['high']))}"  # type: ignore[arg-type]
                    f" · n={esc(aggregate['n'])}</div>"
                )
            width = max(0.0, min(100.0, estimate * 100))
            # a zero estimate draws NO bar: a minimum-width stub would render
            # "never happened" as a visible quantity
            fill = (
                f"<div class='fill' style='width:{width:.4f}%'></div>" if width > 0 else ""
            )
            bars.append(
                f"<div class='bar'><span class='name'>{esc(condition)}</span>"
                f"<span class='val'>{_pct(estimate)}</span>"
                f"<div class='track'>{fill}</div>{ci}</div>"
            )
        delta = ""
        if baseline_id in values and treated_id in values:
            delta = (
                f"<div class='delta'>{_delta_chip(metric, values[baseline_id], values[treated_id])}"
                f" <span class='note'>{esc(baseline_id)} &rarr; {esc(treated_id)}</span></div>"
            )
        panels.append(
            f"<div class='panel'><div class='measure'>{esc(label)}</div>"
            f"<div class='bars'>{''.join(bars)}</div>{delta}</div>"
        )
    return f"<div class='panels'>{''.join(panels)}</div>"


def _significance_line(bundle: dict[str, object]) -> str:
    """The test, stated with the sample that actually powers it."""
    for aggregate in bundle.get("aggregates", []):  # type: ignore[union-attr]
        test: dict[str, object] = aggregate.get("test") or {}  # type: ignore[assignment]
        if not test:
            continue
        name = str(test.get("name", ""))
        p = test.get("p")
        p_txt = f"p = {float(p):.3g}" if isinstance(p, (int, float)) else "p unavailable"
        if name == "mcnemar":
            discordant: dict[str, object] = test.get("discordant") or {}  # type: ignore[assignment]
            return (
                f"McNemar (paired), {p_txt} over {esc(test.get('effective_n', '?'))} "
                f"discordant pairs of {esc(test.get('paired_n', '?'))} "
                f"(b={esc(discordant.get('b', '?'))}, c={esc(discordant.get('c', '?'))}) — "
                "the power comes from the discordant pairs alone, not the total n."
            )
        return (
            f"{esc(name)}, {p_txt}, effective n = {esc(test.get('effective_n', '?'))}. "
            "Independent samples: exploratory, not a paired significance test."
        )
    return ""


def _is_deterministic_agent(bundle: dict[str, object]) -> tuple[bool, str]:
    environment: dict[str, object] = bundle.get("environment", {})  # type: ignore[assignment]
    model: dict[str, object] = environment.get("model", {})  # type: ignore[assignment]
    provider = str(model.get("provider", ""))
    return provider in ("", "scripted", "cassette"), str(model.get("id", provider or "unknown"))


def _scripted_caveat(bundle: dict[str, object]) -> str:
    """Say, in place, which number is a dial and which is a measurement.

    A scripted agent follows the injection at a rate fixed by its own reference
    (`scripted@0.6` follows it about 60% of the time), so the ungoverned attack
    rate is a PARAMETER of the stand-in — it measures nothing about any model. The
    governed arm is a different kind of number: the kernel's verdicts are recorded
    and replay bit-for-bit. Leaving that unsaid is how a dial gets read as a finding.
    """
    deterministic, model_id = _is_deterministic_agent(bundle)
    if not deterministic:
        return ""
    return (
        "<div class='callout'><h3>What this run does and does not measure</h3>"
        f"<p class='note'>The agent is <code>{esc(model_id)}</code>, a deterministic "
        "stand-in for the model layer. Its rate of following an injection is a "
        "<b>parameter of the stand-in</b>, so the ungoverned attack-success rate here "
        "is set by construction and says nothing about any model.</p>"
        "<p class='note'>What the run does establish, and what replays bit-for-bit: "
        "the governance kernel's verdict on every tainted call, and whether enforcement "
        "cost the agent any task success. To measure a <i>model</i>, run the same "
        "experiment against a live one — the ungoverned arm becomes an observation "
        "and the analysis moves to an independent-samples comparison.</p></div>"
    )


def headline(bundle: dict[str, object]) -> str:
    """A one-line summary of the result — the link-preview text and card subtitle."""
    baseline_id, treated_id = _baseline_and_treated(bundle)
    if baseline_id is None or treated_id is None:
        return ""
    parts = []
    for metric, aggregates in _by_metric(bundle).items():
        values = {str(a["condition_id"]): float(a["estimate"]) for a in aggregates}  # type: ignore[arg-type]
        if baseline_id not in values or treated_id not in values:
            continue
        label = _METRIC_LABELS.get(metric, metric)
        base, treat = values[baseline_id], values[treated_id]
        if abs(treat - base) < 0.0005:
            parts.append(f"{label} unchanged at {_pct(base)}")
        else:
            parts.append(f"{label} {_pct(base)} → {_pct(treat)}")
    if not parts:
        return ""
    trials = len(bundle.get("trials", []))  # type: ignore[arg-type]
    return f"{'; '.join(parts)}. {trials} trials, {baseline_id} vs {treated_id}."


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------


def _short_hash(value: object) -> str:
    """A hash you can read at a glance, with the whole thing on hover.

    A full sha256 in a table column pushes every other column off the page, so
    the digest is elided — never silently, and never truncated in a way that
    could be mistaken for the real value: the full string stays in `title`.
    """
    text = str(value)
    prefix, _, digest = text.partition(":")
    if not digest or len(digest) <= 16:
        return f"<code>{esc(text)}</code>"
    return f"<code title='{esc(text)}'>{esc(prefix)}:{esc(digest[:10])}&hellip;</code>"


def _short_trace_id(trace_id: str) -> str:
    """Elide the run hash, keep the trial coordinate.

    A trace id is `t_r_<run hash>_<scenario>_<condition>_s<seed>_r<repeat>`.
    Printed whole it wraps to two lines and the table becomes unreadable, while
    the part a reader scans for is the trial coordinate at the end. Every row in
    a table shares one run, so the run hash is the part to drop.

    The cut is at the run-id boundary, not at a character count: a fixed-width
    cut lands mid-word and shifts row to row (`_governed_` and `_ungoverned_`
    differ in length), which renders scenario names as plausible-looking
    garbage — worse than a long id, because it looks like data.
    """
    parts = trace_id.split("_")
    if len(parts) > 4 and parts[0] == "t" and parts[1] == "r":
        return f"&hellip;{esc('_'.join(parts[3:]))}"
    return esc(trace_id)


def _trace_sample(traces: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    """A sample that shows EVERY condition.

    Sorting by trace_id and slicing filled the table with one arm — a governance
    comparison whose evidence table only ever showed `governed` rows, which is
    the one thing a reader must not have to take on trust.
    """
    by_condition: dict[str, list[dict[str, object]]] = {}
    for trace in sorted(traces, key=lambda t: str(t["trace_id"])):
        by_condition.setdefault(str(trace["trial"]["condition_id"]), []).append(trace)  # type: ignore[index]
    picked: list[dict[str, object]] = []
    while len(picked) < limit and any(by_condition.values()):
        for queue in by_condition.values():
            if queue and len(picked) < limit:
                picked.append(queue.pop(0))
    return sorted(picked, key=lambda t: (str(t["trial"]["condition_id"]), str(t["trace_id"])))  # type: ignore[index]


def _hoist_shared_caveat(texts: list[str]) -> tuple[list[str], str]:
    """Pull a caveat every claim repeats out into one line beneath them.

    The contract's claim text carries its own methodological caveat, so four
    aggregates print the same paragraph four times and the numbers drown in it.
    This moves the shared tail; it never drops it, and it only fires on a whole
    trailing sentence common to every claim.
    """
    if len(texts) < 2:
        return texts, ""
    suffix = texts[0]
    for text in texts[1:]:
        while suffix and not text.endswith(suffix):
            suffix = suffix[1:]
        if not suffix:
            return texts, ""
    # start the shared part at a sentence boundary — hoisting half a sentence
    # would leave both halves unreadable
    position = suffix.find(". ")
    if position == -1:
        return texts, ""
    shared = suffix[position + 2:].strip()
    if len(shared) < 60:
        return texts, ""
    trimmed = [text[: len(text) - len(shared)].rstrip().rstrip(".") for text in texts]
    if not all(trimmed):
        return texts, ""
    return trimmed, shared


def _created(pub: dict[str, object]) -> str:
    created = str(pub.get("created", ""))
    return created[:10] if len(created) >= 10 else ""


def render_catalog(publications: list[StoredPublication]) -> str:
    """The public index. Newest first — it used to sort by `publication_id`,
    which is content-addressed, so the order was stable but arbitrary."""
    entries = []
    for stored in sorted(
        publications,
        key=lambda s: (str(s.publication.get("created", "")), str(s.publication["publication_id"])),
        reverse=True,
    ):
        pub = stored.publication
        pid = esc(pub["publication_id"])
        summary = headline(stored.bundle)
        date = _created(pub)
        entries.append(
            f"<a class='entry' href='/e/{pid}'>"
            f"<div class='q'>{esc(pub['question'])}</div>"
            + (f"<div class='note'>{esc(summary)}</div>" if summary else "")
            + f"<div class='rowline' style='margin-top:.7rem'>{_provenance_badges(stored.axes())}"
            + (f"<span class='meta'>{esc(date)}</span>" if date else "")
            + "</div></a>"
        )
    listing = "".join(entries) or (
        "<div class='empty'>No published experiments yet.<br>"
        "<span class='note'>Run one and publish it — every trace travels with it.</span></div>"
    )
    intro = (
        "<p class='note'>Published experiments are re-runnable, forkable, citable "
        "artifacts. Governance runs are <b>compare</b> experiments: the same agent, "
        "same model, <b>ungoverned</b> vs <b>governed</b>. Every verdict on this site "
        "can be replayed from the frozen traces, and verified offline without trusting "
        "this server.</p>"
    )
    body = (
        "<div class='eyebrow'>Axor Lab</div>"
        "<h1>Axor Lab &mdash; Catalog</h1>"
        f"{intro}<div style='margin-top:1.5rem'>{listing}</div>"
        f"{_footer()}"
    )
    return _page(
        "Axor Lab — Catalog", body,
        "Reproducible agent-governance experiments: ungoverned vs governed, "
        "every verdict replayable bit-for-bit and verifiable offline.",
    )


def render_publication(stored: StoredPublication) -> str:
    pub = stored.publication
    axes = stored.axes()
    claims: list[dict[str, object]] = pub["claims"]  # type: ignore[assignment]
    exact = [c for c in claims if c["kind"] == "exactly_replayable"]
    statistical = [c for c in claims if c["kind"] == "statistically_reproducible"]
    pid_txt = esc(pub["publication_id"])

    body = [
        "<p class='meta'><a href='/catalog'>&larr; Catalog</a></p>",
        "<div class='eyebrow'>Reproducible experiment</div>",
        f"<h1>{esc(pub['question'])}</h1>",
        f"<div class='rowline'>{_provenance_badges(axes)}</div>",
    ]
    date = _created(pub)
    body.append(
        "<p class='meta'>"
        + (f"{esc(date)} &middot; " if date else "")
        + f"{esc(pub['license'])} &middot; <code>{pid_txt}</code></p>"
    )

    body.append("<h2>Result</h2>")
    body.append(_measure_panels(stored.bundle))
    significance = _significance_line(stored.bundle)
    if significance:
        body.append(f"<p class='note'>{significance}</p>")
    body.append(_scripted_caveat(stored.bundle))

    body.append("<h2>Exactly replayable</h2>")
    body.append(
        "<p class='note'>Governance verdicts over frozen traces. Deterministic, "
        "no confidence interval, reproducible bit-for-bit given the pinned kernel.</p>"
    )
    # NOTE: list.extend() returns None, so `extend(...) or append(...)` used to
    # append "No exact claims" even when there WERE exact claims. Branch explicitly.
    if exact:
        body.extend(f"<div class='claim'>{esc(c['text'])}</div>" for c in exact)
    else:
        body.append("<p class='note'>No exact claims.</p>")

    body.append("<h2>Statistically reproducible</h2>")
    body.append(f"<p class='note'>{esc(_statistical_note(stored.bundle))}</p>")
    claim_texts, shared_caveat = _hoist_shared_caveat([str(c["text"]) for c in statistical])
    body.extend(f"<div class='claim stat'>{esc(text)}</div>" for text in claim_texts)
    if shared_caveat:
        body.append(f"<p class='note'>Applies to every claim above: {esc(shared_caveat)}</p>")

    body.append("<h2>Methodology</h2>")
    environment: dict[str, object] = stored.bundle["environment"]  # type: ignore[assignment]
    # a mixed-kernel bundle omits the singular `kernel_version` and carries a
    # `kernel_versions` LIST instead (contract allows both). Reading the singular
    # key unconditionally KeyError'd → 400 on a valid publication (review r17).
    if "kernel_version" in environment:
        kernel_label = esc(str(environment["kernel_version"]))
    else:
        versions = environment.get("kernel_versions", [])  # type: ignore[union-attr]
        kernel_label = esc(", ".join(str(v) for v in versions)) or "(mixed / unspecified)"
    body.append(
        f"<p class='note'>Kernel <code>{kernel_label}</code> &middot; "
        f"model <code>{esc(environment['model']['id'])}</code>.</p>"  # type: ignore[index]
    )
    # show what ACTUALLY differs between conditions — the contract supports
    # allowlists, profiles, kernels and several enforcing conditions, so
    # "differ only in enforcement" was only true for the simplest slice
    body.append(f"<div class='card scroll'>{_conditions_diff(stored.bundle)}</div>")

    body.append("<h2>Evidence</h2>")
    body.append(
        "<p class='note'>Each trace is a single trial, replayed on load. Open one for "
        "its provenance chain and the counterfactual policy replay.</p>"
    )
    traces = list(stored.traces.values())
    rows = ["<table><tr><th>Trial</th><th>Condition</th><th>Verdict (replayed)</th></tr>"]
    for trace in _trace_sample(traces, 12):
        verdict = _final_verdict(trace)
        cls = "deny" if verdict == "DENY" else "allow"
        tid = esc(trace["trace_id"])
        rows.append(
            f"<tr><td class='nowrap'><a href='/e/{pid_txt}/evidence/{tid}' "
            f"title='{tid}'><code>{_short_trace_id(str(trace['trace_id']))}</code></a></td>"
            f"<td>{esc(trace['trial']['condition_id'])}</td>"  # type: ignore[index]
            f"<td class='{cls}'>{esc(verdict)}</td></tr>"
        )
    rows.append("</table>")
    body.append(f"<div class='card scroll'>{''.join(rows)}</div>")
    if len(traces) > 12:
        body.append(
            f"<p class='note'>Showing 12 of {len(traces)} traces — the reproduction "
            "package below carries every one.</p>"
        )

    body.append("<h2>Reproduce</h2>")
    body.append(
        "<pre><code># set your Lab server's origin (a root-relative path is not a runnable URL)\n"
        'AXOR_LAB_URL="https://lab.example.com"\n\n'
        "# download the reproduction package (bundle + frozen traces + receipt)\n"
        f'curl -fsS "$AXOR_LAB_URL/api/publications/{pid_txt}/bundle" -o {pid_txt}.json\n\n'
        "# verify it offline — content hashes, replay, and the portable receipt\n"
        f"axor-lab verify {pid_txt}.json\n\n"
        "# or replay just the governance verdicts over the frozen evidence (EXACT)\n"
        f"axor-lab replay {pid_txt}.json</code></pre>"
    )
    body.append(
        f"<p class='note'>The package at "
        f"<code>/api/publications/{pid_txt}/bundle</code> is the exact evidence "
        "this page is built from — the bundle plus every frozen trace — so the "
        "replay above runs against the real bytes, not a name you were never given. "
        "A fresh live <code>run</code> (a new statistical sample) needs the original "
        "experiment document, which the bundle does not yet embed.</p>"
    )

    body.append("<h2>Limitations</h2><ul>")
    for limitation in pub.get("limitations", []):  # type: ignore[union-attr]
        body.append(f"<li>{esc(limitation)}</li>")
    body.append("</ul>")

    body.append(
        f"<div class='foot'>License {esc(pub['license'])}. "
        "This publication is immutable; reproductions accrue separately.</div>"
    )
    return _page(str(pub["question"]), "".join(body), headline(stored.bundle))


def render_evidence(stored: StoredPublication, trace_id: str, policy_id: str | None = None) -> str:
    trace = stored.traces[trace_id]
    bundle = stored.bundle
    scenario = _scenario_for(bundle, trace)
    # replay under the condition the counterfactual is ABOUT, using the ONE
    # resolver the CLI also uses (lab_runner.evidence_condition): the ?policy=
    # choice if enforcing; else the trace's OWN enforcing condition (so a
    # governed_allowlist trace is not silently replayed under strict); else the
    # first enforcing candidate. A bad ?policy= is a clean 400, not a fallthrough.
    try:
        condition = evidence_condition(bundle, trace, policy_id)
    except ValueError as exc:
        raise PublishRejected(str(exc), status=400) from exc
    manifests = {str(m["id"]): m for m in bundle["tool_manifests"]}  # type: ignore[union-attr]
    # use the REAL axor-core governor when the condition pins the installed
    # version, exactly as replay/regress do — an EvidenceCase for a real-kernel
    # trace must not silently reason with the reference kernel (review r12) — and
    # pass THIS trace's scenario inputs so a real-kernel `$inputs` allowlist
    # expands to concrete values, not the symbolic ref (review r17).
    version = str(condition["kernel"])
    kernel = resolve_kernel(
        version, manifests, condition.get("policy"),
        default_registry((version,)), scenario.get("inputs", {}),  # type: ignore[union-attr]
    )
    case = build_evidence_case(trace, scenario, condition, kernel, manifests)
    chain: dict[str, object] = case["chain"]  # type: ignore[assignment]
    modes: dict[str, object] = case["modes"]  # type: ignore[assignment]
    pid = esc(stored.publication["publication_id"])

    body = [
        f"<p class='meta'><a href='/e/{pid}'>&larr; {esc(stored.publication['question'])}</a></p>",
        "<div class='eyebrow'>EvidenceCase</div>",
        f"<h1><code>{esc(trace_id)}</code></h1>",
        f"<div class='card'><h3>Injection</h3>"
        f"<p class='note'>{esc(chain['injection']['text'])}</p></div>",  # type: ignore[index]
    ]
    # be explicit about WHICH policy the counterfactual replay used, and offer
    # the other enforcing conditions as alternative replays
    body.append(
        f"<p class='note'>Policy replay under <code>{esc(condition['id'])}</code> "
        f"(kernel <code>{esc(condition['kernel'])}</code>, "
        f"config_hash <code>{esc(condition.get('config_hash', 'n/a'))}</code>).</p>"
    )
    enforcing = [c for c in bundle["conditions"] if c["enforcement"] == "on"]  # type: ignore[union-attr]
    if len(enforcing) > 1:
        links = " &middot; ".join(
            f"<a href='/e/{pid}/evidence/{esc(trace_id)}?policy={esc(c['id'])}'>{esc(c['id'])}</a>"
            for c in enforcing
        )
        body.append(f"<p class='note'>Replay under another policy: {links}</p>")

    body.append("<h2>Provenance chain</h2>")
    rows = ["<table><tr><th>value</th><th>labels</th><th>sources</th></tr>"]
    for value in chain["provenance"]:  # type: ignore[union-attr]
        sources = ", ".join(esc(s.get("origin_ref", s.get("kind"))) for s in value["sources"])
        rows.append(
            f"<tr><td>{esc(value.get('preview', value['value_id']))}</td>"
            f"<td>{esc(', '.join(value['labels']))}</td><td>{sources}</td></tr>"
        )
    rows.append("</table>")
    body.append(f"<div class='card scroll'>{''.join(rows)}</div>")

    verdict: dict[str, object] = chain["verdict"]  # type: ignore[assignment]
    vcls = "deny" if verdict["verdict"] == "DENY" else "allow"
    body.append("<h2>Gated call &amp; verdict</h2>")
    body.append(
        f"<div class='card'><p>Tool <code>{esc(chain['gated_call']['tool'])}</code> "  # type: ignore[index]
        f"&rarr; <span class='{vcls}'>{esc(verdict['verdict'])}</span> "
        f"(gate <code>{esc(verdict['gate'])}</code>).</p>"
        f"<p class='note'>{esc(case['note'])}</p></div>"
    )

    body.append("<h2>Modes</h2>")
    observed: dict[str, object] = modes["observed"]  # type: ignore[assignment]
    counterfactual: dict[str, object] = modes["counterfactual_policy_replay"]  # type: ignore[assignment]
    modes_html = [
        f"<div class='card'><h3>Observed ({esc(observed['condition_id'])})</h3>"
        f"<p class='note'>{esc(', '.join(observed['verdicts']))}</p></div>",
        "<div class='card'><h3>Counterfactual: policy replay</h3>"
        f"<p class='note'>{esc(', '.join(counterfactual['verdicts']))} &mdash; "
        f"{esc(counterfactual['caveat'])}</p></div>",
    ]
    if "observed_governed_twin" in modes:
        twin: dict[str, object] = modes["observed_governed_twin"]  # type: ignore[assignment]
        modes_html.append(
            "<div class='card'><h3>Observed governed twin</h3>"
            f"<p class='note'>{esc(', '.join(twin['verdicts']))}</p></div>"
        )
    body.extend(modes_html)
    if "fidelity_warning" in case:
        body.append(
            f"<div class='callout'><p class='note'>&#9888; {esc(case['fidelity_warning'])}</p></div>"
        )

    body.append(_footer())
    return _page(f"EvidenceCase {trace_id}", "".join(body))


def _footer() -> str:
    return (
        "<div class='foot'>Axor Lab &mdash; reproducible governance experiments. "
        "Every published verdict replays from its frozen traces, and "
        "<code>axor-lab verify</code> checks a downloaded package offline, "
        "trusting no server.</div>"
    )


def _provenance_badges(axes: dict[str, object]) -> str:
    reproductions: dict[str, object] = axes["reproductions"]  # type: ignore[assignment]
    # the public badge counts ONLY cryptographically verified reproductions
    # (signed by a known key); unsigned self-reports are shown separately and
    # never inflate the headline number (review r8)
    verified = int(reproductions.get("verified", 0))  # type: ignore[arg-type]
    unverified = int(reproductions.get("unverified", 0))  # type: ignore[arg-type]
    badges = (
        f"<span class='badge'>origin <b>{esc(axes['origin'])}</b></span>"
        f"<span class='badge'>integrity <b>{esc(axes['integrity'])}</b></span>"
        f"<span class='badge'>verified reproductions <b>&times;{esc(verified)}</b></span>"
    )
    if unverified:
        badges += (
            f"<span class='badge muted'>+{esc(unverified)} unverified self-report(s)</span>"
        )
    return badges


def _statistical_note(bundle: dict[str, object]) -> str:
    """An HONEST header for the statistical claims, split by how the run was
    produced and by comparison design (review r14). The old text asserted
    'Aggregates over live runs' unconditionally, but the default runner drives a
    DETERMINISTIC scripted agent (reproduces exactly on re-run, not merely within
    a CI), and an independent-samples design is exploratory, not a paired test."""
    aggregates: list[dict[str, object]] = bundle.get("aggregates", [])  # type: ignore[assignment]
    environment: dict[str, object] = bundle.get("environment", {})  # type: ignore[assignment]
    model: dict[str, object] = environment.get("model", {})  # type: ignore[assignment]
    provider = str(model.get("provider", ""))
    deterministic, _ = _is_deterministic_agent(bundle)
    designs = {str(a.get("comparison_design", "matched_pairs")) for a in aggregates}
    if deterministic:
        origin = (
            "Aggregates recomputed from the frozen traces of a DETERMINISTIC "
            f"({provider or 'scripted'}) agent — re-running reproduces them exactly, "
            "not merely within the interval."
        )
    else:
        origin = (
            "Aggregates over LIVE runs. Stochastic, carry a CI, reproduced by "
            "re-running — matched within the interval, never bit-for-bit."
        )
    design_notes = []
    if "matched_pairs" in designs:
        design_notes.append(
            "matched-pairs designs report a paired test (uploader-declared pairing)"
        )
    if "independent_samples" in designs:
        design_notes.append(
            "independent-samples designs are EXPLORATORY, not a paired significance test"
        )
    if design_notes:
        origin += " " + "; ".join(design_notes) + "."
    return origin


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


def _conditions_diff(bundle: dict[str, object]) -> str:
    """A table of every condition with what actually differs — enforcement,
    policy, kernel, config hash — instead of asserting 'differ only in
    enforcement' (which only held for the simplest slice)."""
    rows = [
        "<table><tr><th>Condition</th><th>Enforcement</th><th>Policy</th>"
        "<th>Kernel</th><th>config_hash</th></tr>"
    ]
    import json as _json

    for condition in bundle["conditions"]:  # type: ignore[union-attr]
        policy = condition.get("policy") or {}
        policy_txt = esc(_json.dumps(policy, sort_keys=True)) if policy else "—"
        rows.append(
            f"<tr><td>{esc(condition['id'])}</td>"
            f"<td>{esc(condition['enforcement'])}</td>"
            f"<td><code>{policy_txt}</code></td>"
            f"<td><code>{esc(condition['kernel'])}</code></td>"
            f"<td>{_short_hash(condition.get('config_hash', 'n/a'))}</td></tr>"
        )
    rows.append("</table>")
    return "".join(rows)
