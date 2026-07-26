// The measurement IS the home page.
//
// What this replaces, and why. The Lab's UI had grown into a control panel over
// its own endpoints: seven entry cards, a numbered four-step constructor, and a
// "Run this experiment" button standing between a visitor and the first number.
// That shape assumes the measurement is expensive enough to be worth a funnel.
// It is not — four AgentDojo suites replay through the real governor in about a
// second, with no model and no key. So the funnel was pure cost: it asked people
// to make four decisions before showing them anything that would tell them which
// decisions mattered.
//
// Inverted here. The default measurement runs on load and the result is the
// page. Every control is an edit of the table already on screen — turn a suite
// off by clicking its row, arm the allowlist and watch the same row move,
// declare a secret next to the number it will cost you. There is no Run button
// because there is nothing to wait for: the query re-fires on any change and the
// previous numbers stay put, dimmed, while the next ones land.
//
// The honest caveat stays. These are ground-truth call sequences, not a model's
// attempts, so the denial counts are a lower bound.
import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Play, RefreshCw } from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { api, type BenchRates, type BenchRow, type SweepReport } from "../api";

const pct = (r: BenchRates) => (r.base ? (100 * r.governed) / r.base : 0);
const fmt = (r: BenchRates) => (r.base ? `${pct(r).toFixed(0)}%` : "n/a");

/** Sum rates across the selected suites — the one number worth quoting. */
function total(rows: BenchRow[], pick: (r: BenchRow) => BenchRates): BenchRates {
  return rows.reduce(
    (acc, row) => {
      const r = pick(row);
      return { base: acc.base + r.base, governed: acc.governed + r.governed, unmapped: acc.unmapped + r.unmapped };
    },
    { base: 0, governed: 0, unmapped: 0 },
  );
}

/** A label whose explanation is one line under it, never a tooltip you must find. */
function Head({ label, note, width }: { label: string; note: string; width?: number }) {
  return (
    <th style={{ padding: "0 12px 10px", textAlign: "left", width, verticalAlign: "bottom" }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>{label}</div>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, fontWeight: 400, lineHeight: 1.4, marginTop: 2 }}>
        {note}
      </div>
    </th>
  );
}

/** One live control, with the thing you would otherwise learn by getting it wrong. */
function Toggle({ on, onChange, label, hint }: {
  on: boolean; onChange: (v: boolean) => void; label: string; hint: string;
}) {
  return (
    <label style={{ display: "block", cursor: "pointer" }}>
      <span className="wrapline" style={{ gap: 8 }}>
        <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} />
        <span style={{ fontFamily: MONO, fontSize: 11.5, color: on ? C.text : C.mut }}>{label}</span>
      </span>
      <span style={{ display: "block", fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6, marginLeft: 24, marginTop: 3 }}>
        {hint}
      </span>
    </label>
  );
}

export default function Home() {
  const index = useQuery({ queryKey: ["bench-index"], queryFn: api.benchIndex });
  const entry =
    index.data?.benchmarks.find((b) => b.benchmark === index.data?.default) ??
    index.data?.benchmarks[0];

  const [off, setOff] = useState<string[]>([]);
  const [allowlist, setAllowlist] = useState(false);
  const [secrets, setSecrets] = useState<Record<string, string[]>>({});
  const [open, setOpen] = useState<string | null>(null);

  const all = (entry?.suites ?? []).map((s) => s.suite);
  const chosen = all.filter((s) => !off.includes(s));
  const spec = { benchmark: entry?.benchmark, suites: chosen, allowlist, secrets };

  // No Run button: the result is a function of the controls, so it re-derives
  // itself. keepPreviousData is what makes that readable — the old numbers stay
  // in place, dimmed, instead of the table collapsing to a spinner every click.
  const run = useQuery({
    queryKey: ["bench-run", JSON.stringify(spec)],
    queryFn: () => api.benchRun(spec),
    enabled: !!entry && chosen.length > 0,
    placeholderData: keepPreviousData,
  });

  // Per-suite secret costs, fetched only when a row is opened — the sweep runs
  // one governed pass per candidate source, so it is the one thing here that is
  // worth asking for rather than doing on load.
  const sweep = useQuery({
    queryKey: ["bench-sweep", open, allowlist, entry?.benchmark],
    queryFn: () => api.benchSweep(open!, allowlist, entry?.benchmark),
    enabled: !!open,
  });

  const qc = useQueryClient();
  const example = useMutation({
    mutationFn: () => api.runLocal(),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["run-results", r.run_id] });
      navigate(`results/${r.run_id}`);
    },
  });

  const rows = run.data?.rows ?? [];
  const util = total(rows, (r) => r.utility);
  const asr = total(rows, (r) => r.asr);
  const denials = rows.reduce((n, r) => n + r.denials, 0);
  const stale = run.isFetching;
  const declared = Object.values(secrets).reduce((n, l) => n + l.length, 0);

  const toggleSuite = (suite: string) =>
    setOff((prev) => {
      const next = prev.includes(suite) ? prev.filter((s) => s !== suite) : [...prev, suite];
      return next.length === all.length ? prev : next; // never all off
    });

  const toggleSecret = (suite: string, tool: string) =>
    setSecrets((prev) => {
      const cur = new Set(prev[suite] ?? []);
      cur.has(tool) ? cur.delete(tool) : cur.add(tool);
      const next = { ...prev };
      if (cur.size) next[suite] = [...cur].sort();
      else delete next[suite];
      return next;
    });

  return (
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px", maxWidth: 560 }}>
        What does governance cost, and what does it buy?
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7, marginBottom: 22, maxWidth: 600 }}>
        {entry ? `${entry.suites.length} ${entry.title} suites` : "The benchmark suites"} replayed
        through the real axor-core governor. No model, no API key, about a second — the answer is
        below, already measured. Change anything and it re-measures in place.
      </div>

      {index.isError && (
        <div className="p-3 mb-4" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red, lineHeight: 1.6 }}>
          the runtime API is not answering. Start the server without <code>--no-runtime-api</code>:
          <div style={{ color: C.mut, marginTop: 6 }}>axor-lab serve</div>
        </div>
      )}

      {/* the headline: two numbers, always together, because a defense that
          reports one describes half a trade */}
      <div
        className="p-4 mb-4"
        style={{
          background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10,
          opacity: stale ? 0.55 : 1, transition: "opacity .15s",
        }}
      >
        <div className="wrapline" style={{ gap: 34 }}>
          <div>
            <div style={{ fontSize: 34, fontWeight: 700, color: C.text, lineHeight: 1 }}>{fmt(util)}</div>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 6 }}>
              legitimate work still gets done
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 2 }}>
              {util.governed} of {util.base} benign tasks
            </div>
          </div>
          <div>
            <div style={{ fontSize: 34, fontWeight: 700, lineHeight: 1, color: asr.governed ? C.amber : C.green }}>
              {fmt(asr)}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 6 }}>
              attacks still succeed
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 2 }}>
              {asr.governed} of {asr.base} injection tasks
            </div>
          </div>
          <div>
            <div style={{ fontSize: 34, fontWeight: 700, color: C.violet, lineHeight: 1 }}>{denials}</div>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 6 }}>calls refused</div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 2 }}>
              across {chosen.length} suite{chosen.length === 1 ? "" : "s"}
            </div>
          </div>
          {stale && (
            <span className="wrapline" style={{ gap: 6, fontFamily: MONO, fontSize: 10, color: C.dim }}>
              <RefreshCw size={11} className="animate-spin" /> re-measuring
            </span>
          )}
        </div>
      </div>

      {/* the controls sit between the headline and the breakdown, because they
          are how you move both — not a form you fill in before you see either */}
      <div className="p-4 mb-4" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginBottom: 10, letterSpacing: .4 }}>
          THE POLICY BEING MEASURED
        </div>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6, marginBottom: 12 }}>
          <b style={{ color: C.text }}>Taint floor</b> — always on. An effect may not reach a
          destination that came from attacker-reachable data.
        </div>
        <Toggle
          on={allowlist}
          onChange={setAllowlist}
          label="Declare the known-payee enum"
          hint="An enum on a driving argument restricts as well as supersedes: it lifts the taint on
                destinations you vetted, and denies every destination outside the set — including
                clean, prompt-given ones you forgot to list."
        />
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.6, marginTop: 12 }}>
          {declared === 0
            ? "No secret reads declared. Open a suite below to declare one and see what it costs."
            : `${declared} secret read${declared === 1 ? "" : "s"} declared — the confidentiality floor is armed for ${Object.keys(secrets).join(", ")}.`}
        </div>
      </div>

      {/* the breakdown. Clicking a suite name drops it from the measurement;
          clicking "secrets" opens the sweep for that suite alone. */}
      <div style={{ overflowX: "auto", opacity: stale ? 0.55 : 1, transition: "opacity .15s" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11.5 }}>
          <thead>
            <tr>
              <Head label="suite" note="click to include or drop it" width={190} />
              <Head label="work kept" note="benign tasks that still finish" />
              <Head label="attacks kept" note="injections that still succeed" />
              <Head label="refused" note="calls, and which gate" />
              <Head label="reference" note="published o4-mini run" />
              <Head label="secrets" note="declare and price them" width={110} />
            </tr>
          </thead>
          <tbody>
            {(entry?.suites ?? []).map((suite) => {
              const on = chosen.includes(suite.suite);
              const row = rows.find((r) => r.suite === suite.suite);
              const mine = secrets[suite.suite] ?? [];
              return (
                <tr key={suite.suite} style={{ borderTop: `1px solid ${C.line}`, opacity: on ? 1 : 0.35 }}>
                  <td
                    onClick={() => toggleSuite(suite.suite)}
                    style={{ padding: "12px", cursor: "pointer" }}
                    title={on ? "drop this suite from the measurement" : "put it back"}
                  >
                    <span className="wrapline" style={{ gap: 8 }}>
                      <input type="checkbox" checked={on} readOnly tabIndex={-1} />
                      <span style={{ color: C.text, fontWeight: 600 }}>{suite.suite}</span>
                    </span>
                    <div style={{ fontSize: 9.5, color: C.dim, marginTop: 3, marginLeft: 24, lineHeight: 1.5 }}>
                      {suite.note}
                    </div>
                  </td>
                  <td style={{ padding: "12px" }}>
                    <span style={{ fontSize: 16, fontWeight: 700, color: C.text }}>
                      {row ? fmt(row.utility) : "—"}
                    </span>
                    {row && <span style={{ fontSize: 9.5, color: C.dim }}> of {row.utility.base}</span>}
                  </td>
                  <td style={{ padding: "12px" }}>
                    <span style={{ fontSize: 16, fontWeight: 700, color: !row ? C.dim : row.asr.governed ? C.amber : C.green }}>
                      {row ? fmt(row.asr) : "—"}
                    </span>
                    {row && <span style={{ fontSize: 9.5, color: C.dim }}> of {row.asr.base}</span>}
                  </td>
                  <td style={{ padding: "12px", color: C.text }}>
                    {row ? row.denials : "—"}
                    <div style={{ fontSize: 9.5, color: C.dim, marginTop: 2 }}>
                      {row ? Object.entries(row.by_gate).map(([g, n]) => `${g}×${n}`).join(" ") || "—" : ""}
                    </div>
                  </td>
                  <td style={{ padding: "12px", color: C.mut, fontSize: 10.5 }}>{suite.reference_denials}</td>
                  <td style={{ padding: "12px" }}>
                    <button
                      onClick={() => setOpen(open === suite.suite ? null : suite.suite)}
                      style={{
                        background: "none", border: `1px solid ${C.line}`, borderRadius: 5,
                        color: mine.length ? C.violet : C.mut, fontFamily: MONO, fontSize: 10,
                        padding: "4px 8px", cursor: "pointer",
                      }}
                    >
                      {mine.length}/{suite.secret_candidates.length}
                      <ChevronRight
                        size={10}
                        style={{ verticalAlign: -1, marginLeft: 3, transform: open === suite.suite ? "rotate(90deg)" : "none" }}
                      />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {open && (
        <div className="p-4 mt-2" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 10 }}>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600 }}>
            {open} · which reads are secret is <i>your</i> declaration
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.65, margin: "6px 0 12px" }}>
            The same <code>search_files</code> is a secret read in a law firm and routine in a public
            wiki. Declaring one arms a floor that is content-blind and sticky: after that read every
            egress in the session is refused, whatever the outgoing value looks like. That is what
            makes it paraphrase-proof, and why it can be expensive. Each row below is measured with
            that source declared <i>alone</i>, so the rows compare to each other but do not add up.
          </div>
          {sweep.isLoading && (
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>pricing each source…</div>
          )}
          {sweep.data && <SecretTable report={sweep.data} chosen={secrets[open] ?? []} onToggle={(t) => toggleSecret(open, t)} />}
        </div>
      )}

      {run.isError && (
        <div className="mt-3 p-3" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red }}>
          {String(run.error)}
        </div>
      )}

      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, lineHeight: 1.7, margin: "16px 0 30px", maxWidth: 640 }}>
        <b style={{ color: C.mut }}>What these numbers are not.</b> Utility is measured over the
        benchmark's own ground-truth call sequences, not over a model's attempts — so this is what
        the gate would cost a <i>perfect</i> agent. A real one makes extra calls that widen the
        tainted set, so treat the refusals as a lower bound. The reference column is a published
        o4-mini run shown for comparison, not something this harness reproduces.
      </div>

      {/* the other doors, once — no seven-card grid competing with the result */}
      <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 18 }}>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: .4, marginBottom: 12 }}>
          MEASURE SOMETHING ELSE
        </div>
        <div className="entrygrid">
          {[
            { to: "builder", title: "Your own scenarios", desc: "compose or author the tasks, run them under the same gate" },
            { to: "agent-ingest", title: "Your agent", desc: "code · endpoint · traces" },
            { to: "models", title: "Live models", desc: "BYOK — the one run where the ungoverned arm is a real model" },
            { to: "import", title: "A production incident", desc: "import the trace, reproduce it, pin a regression" },
          ].map((e) => (
            <div
              key={e.to}
              onClick={() => navigate(e.to)}
              style={{ cursor: "pointer", padding: "12px 14px", borderRadius: 8, background: C.panel, border: `1px solid ${C.line}` }}
            >
              <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, fontWeight: 600 }}>{e.title}</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 3, lineHeight: 1.5 }}>{e.desc}</div>
            </div>
          ))}
        </div>
        <div className="wrapline" style={{ gap: 10, marginTop: 14 }}>
          <button
            onClick={() => example.mutate()}
            disabled={example.isPending}
            style={{
              background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut,
              fontFamily: MONO, fontSize: 10.5, padding: "6px 11px", cursor: "pointer",
              display: "flex", alignItems: "center", gap: 6,
            }}
            title="a scripted banking agent, 60 trials governed and ungoverned — produces a publishable run"
          >
            {example.isPending
              ? <><RefreshCw size={11} className="animate-spin" /> running…</>
              : <><Play size={11} /> run the worked example</>}
          </button>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
            an end-to-end run you can publish to the catalog
          </span>
        </div>
        {example.isError && (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.red, marginTop: 8 }}>
            {(example.error as Error).message}
          </div>
        )}
      </div>
    </div>
  );
}

function SecretTable({ report, chosen, onToggle }: {
  report: SweepReport; chosen: string[]; onToggle: (tool: string) => void;
}) {
  // cheapest first, so the free sources surface instead of hiding in an
  // alphabetical wall — knowing which reads are free to declare is the whole
  // reason to run this
  const rows = [...report.rows].sort((a, b) => a.cost_pp - b.cost_pp);
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11 }}>
        <thead>
          <tr style={{ color: C.mut, textAlign: "left" }}>
            <th style={{ padding: "4px 8px", width: 28 }} />
            <th style={{ padding: "4px 8px" }}>candidate read</th>
            <th style={{ padding: "4px 8px" }}>utility cost</th>
            <th style={{ padding: "4px 8px" }}>attacks kept</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const on = chosen.includes(r.source);
            const free = r.cost_pp <= 0;
            return (
              <tr key={r.source} onClick={() => onToggle(r.source)} style={{ borderTop: `1px solid ${C.line}`, cursor: "pointer" }}>
                <td style={{ padding: "6px 8px" }}>
                  <input type="checkbox" checked={on} readOnly tabIndex={-1} />
                </td>
                <td style={{ padding: "6px 8px", color: on ? C.text : C.mut }}>{r.source}</td>
                <td style={{ padding: "6px 8px", fontWeight: 600, color: free ? C.green : C.red }}>
                  {free ? "free" : `−${r.cost_pp.toFixed(1)}pp`}
                </td>
                <td style={{ padding: "6px 8px", color: C.mut }}>{fmt(r.asr)}</td>
              </tr>
            );
          })}
          <tr style={{ borderTop: `1px solid ${C.line}`, background: "rgba(255,255,255,.02)" }}>
            <td />
            <td style={{ padding: "6px 8px", color: C.mut }} title="measured, not summed — sources read by the same tasks overlap">
              all of them together
            </td>
            <td style={{ padding: "6px 8px", color: C.mut }}>{fmt(report.combined.utility)} kept</td>
            <td style={{ padding: "6px 8px", color: C.mut }}>{fmt(report.combined.asr)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
