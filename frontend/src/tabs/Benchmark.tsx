// The experiment constructor for the benchmark library.
//
// Four steps in the order the decisions actually depend on each other: pick a
// benchmark, pick what to measure, declare the policy, run it. Every field
// carries a hint, because each one here is a choice with a cost that is not
// obvious from its label — "declare a secret" sounds free and is not, and an
// allowlist sounds permissive while it also restricts.
//
// AgentDojo is the DEFAULT entry, not the only shape. A deployment measuring its
// own agent against its own suites asks the same two questions — what does the
// gate cost me, what does each secret cost — with different task names.
//
// The old headline "ASR 55% → 0%" is gone for good: it came from a scripted
// agent whose injection-following rate is a parameter, so it was a dial. This
// screen always shows both axes, because a defense reporting one describes half
// a trade.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Copy, Play, ShieldCheck } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { api, type BenchRates, type BenchRow, type SweepRow } from "../api";

const pct = (r: BenchRates) =>
  r.base ? `${((100 * r.governed) / r.base).toFixed(1)}%` : "n/a";

/** A field's label and the thing you would otherwise learn by getting it wrong. */
function Field({ step, label, hint, children }: {
  step?: number; label: string; hint: string; children?: React.ReactNode;
}) {
  return (
    <div className="p-4 mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, color: C.text }}>
        {step !== undefined && <span style={{ color: C.violet }}>{step}. </span>}
        {label}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.65, margin: "6px 0 12px" }}>
        {hint}
      </div>
      {children}
    </div>
  );
}

function Chip({ on, onClick, children, title }: {
  on: boolean; onClick: () => void; children: React.ReactNode; title?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        fontFamily: MONO, fontSize: 10, padding: "3px 9px", borderRadius: 999,
        cursor: "pointer", border: `1px solid ${on ? C.violet : C.line}`,
        background: on ? C.violet : "transparent", color: on ? "#fff" : C.mut,
      }}
    >
      {children}
    </button>
  );
}

export default function Benchmark() {
  const index = useQuery({ queryKey: ["bench-index"], queryFn: api.benchIndex });
  const [benchmark, setBenchmark] = useState<string>("");
  const entry =
    index.data?.benchmarks.find((b) => b.benchmark === (benchmark || index.data?.default)) ??
    index.data?.benchmarks[0];

  const [suites, setSuites] = useState<string[] | null>(null); // null = all
  const [allowlist, setAllowlist] = useState(false);
  const [secrets, setSecrets] = useState<Record<string, string[]>>({});
  const [sweepSuite, setSweepSuite] = useState("");
  const [copied, setCopied] = useState(false);

  const chosenSuites = suites ?? (entry?.suites ?? []).map((s) => s.suite);
  const spec = {
    benchmark: entry?.benchmark,
    suites: chosenSuites,
    allowlist,
    secrets,
  };

  const run = useMutation({ mutationFn: () => api.benchRun(spec) });
  const sweep = useMutation({
    mutationFn: () =>
      api.benchSweep(sweepSuite || chosenSuites[0], allowlist, entry?.benchmark),
  });

  const toggleSuite = (suite: string) =>
    setSuites(() => {
      const current = new Set(chosenSuites);
      current.has(suite) ? current.delete(suite) : current.add(suite);
      return current.size ? [...current] : chosenSuites; // never empty
    });

  const toggleSecret = (suite: string, tool: string) =>
    setSecrets((prev) => {
      const current = new Set(prev[suite] ?? []);
      current.has(tool) ? current.delete(tool) : current.add(tool);
      const next = { ...prev };
      if (current.size) next[suite] = [...current].sort();
      else delete next[suite];
      return next;
    });

  const declared = Object.values(secrets).reduce((n, list) => n + list.length, 0);

  return (
    <div style={{ maxWidth: 880, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, margin: "0 0 6px" }}>Experiment constructor</h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7, marginBottom: 20 }}>
        Compose a governance measurement and run it against the real axor-core governor.
        Two axes, always together: <b style={{ color: C.text }}>utility retained</b> is how
        much legitimate work survives the gate, <b style={{ color: C.text }}>ASR retained</b>{" "}
        is how much attack survives it. Reporting one without the other describes half a trade.
      </div>

      {index.isError && (
        <div className="p-3 mb-4" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red }}>
          the runtime API is not reachable — start the server without --no-runtime-api
        </div>
      )}

      <Field
        step={1}
        label="Benchmark"
        hint={
          entry
            ? `${entry.title} (${entry.source}) — ${entry.description} A benchmark is a registered provider; more can be added without this screen learning a new concept.`
            : "loading the registered benchmarks…"
        }
      >
        <div className="wrapline" style={{ gap: 6 }}>
          {index.data?.benchmarks.map((b) => (
            <Chip key={b.benchmark} on={b.benchmark === entry?.benchmark}
                  onClick={() => setBenchmark(b.benchmark)} title={b.description}>
              {b.title}
            </Chip>
          ))}
        </div>
      </Field>

      <Field
        step={2}
        label={`Suites · ${chosenSuites.length} of ${entry?.suites.length ?? 0}`}
        hint="What to measure. Each suite exercises a different data flow, so the numbers are not
              interchangeable — one that denies nothing is telling you its egress arguments come
              from the prompt, not that the gate is idle. Narrowing the selection narrows the
              claim: an experiment over three suites must not be reported as four."
      >
        <div className="wrapline" style={{ gap: 6 }}>
          {(entry?.suites ?? []).map((s) => (
            <Chip key={s.suite} on={chosenSuites.includes(s.suite)}
                  onClick={() => toggleSuite(s.suite)}
                  title={`${s.note} · ${s.user_tasks} benign / ${s.injection_tasks} attack · reference denials ${s.reference_denials}`}>
              {s.suite} · {s.user_tasks}/{s.injection_tasks}
            </Chip>
          ))}
        </div>
      </Field>

      <Field
        step={3}
        label="Integrity policy"
        hint="How the gate decides which destinations an effect may reach."
      >
        <label className="wrapline" style={{ gap: 8, cursor: "pointer" }}>
          <input type="checkbox" checked={allowlist} onChange={(e) => setAllowlist(e.target.checked)} />
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>
            declare the known-payee enum
          </span>
        </label>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.65, marginTop: 6, marginLeft: 24 }}>
          An enum on a driving argument <b>restricts as well as supersedes</b>. It lifts the taint
          on destinations you vetted — the recovery — and denies every destination outside the set,
          including clean, prompt-given ones you forgot to list. It is a closed set the sink is
          confined to, not an exception list for tainted values.
        </div>
      </Field>

      <Field
        step={4}
        label={`Secrets · ${declared} declared`}
        hint="Which tools read secrets is a property of YOUR deployment — the same search_files is
              a secret read in a law firm and routine in a public wiki. Declaring one arms a floor
              that is content-blind and sticky: after that read, every egress in the session is
              refused whatever the outgoing value looks like. That is what makes it
              paraphrase-proof, and why it can be expensive. Sweep first."
      >
        {(entry?.suites ?? [])
          .filter((s) => chosenSuites.includes(s.suite))
          .map((suite) => (
            <div key={suite.suite} style={{ marginBottom: 10 }}>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginBottom: 4 }}>
                {suite.suite}
                <span style={{ color: C.mut }}> · {suite.secret_candidates.length} candidates</span>
              </div>
              <div className="wrapline" style={{ gap: 6 }}>
                {suite.secret_candidates.map((tool) => (
                  <Chip key={tool} on={(secrets[suite.suite] ?? []).includes(tool)}
                        onClick={() => toggleSecret(suite.suite, tool)}
                        title="declaring this arms the confidentiality floor after it is read">
                    {tool}
                  </Chip>
                ))}
              </div>
            </div>
          ))}
      </Field>

      <div className="wrapline" style={{ gap: 8, marginBottom: 6 }}>
        <button style={cta(!run.isPending)} onClick={() => run.mutate()} disabled={run.isPending}>
          <Play size={14} /> {run.isPending ? "measuring…" : "Run this experiment"}
        </button>
        <button
          style={{ ...cta(true), background: "transparent", color: C.mut, border: `1px solid ${C.line}` }}
          onClick={() => {
            navigator.clipboard?.writeText(JSON.stringify(spec, null, 2));
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
          title="the exact request body — paste it into a script and the run is the same run"
        >
          <Copy size={13} /> {copied ? "copied" : "copy spec"}
        </button>
      </div>

      {run.data && (
        <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11 }}>
            <thead>
              <tr style={{ color: C.mut, textAlign: "left" }}>
                <th style={{ padding: "10px 12px" }} title="the suite this row measures">suite</th>
                <th style={{ padding: "10px 12px" }} title="benign tasks still succeeding after the gate, of those that succeed ungoverned — the gap is the false-positive cost">utility retained</th>
                <th style={{ padding: "10px 12px" }} title="injection tasks still succeeding after the gate, of those that succeed ungoverned — what survives is the false-negative rate">ASR retained</th>
                <th style={{ padding: "10px 12px" }} title="how many calls the gate refused, and at which gate">denials</th>
                <th style={{ padding: "10px 12px" }} title="a published o4-mini run, shown for comparison — not something this harness reproduces">reference</th>
              </tr>
            </thead>
            <tbody>
              {run.data.rows.map((row: BenchRow) => (
                <tr key={row.suite} style={{ borderTop: `1px solid ${C.line}` }}>
                  <td style={{ padding: "10px 12px", color: C.text }}>
                    {row.suite}
                    <div style={{ fontSize: 9.5, color: C.mut }}>{row.note}</div>
                  </td>
                  <td style={{ padding: "10px 12px" }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{pct(row.utility)}</span>
                    <span style={{ fontSize: 10, color: C.mut }}> of {row.utility.base}</span>
                  </td>
                  <td style={{ padding: "10px 12px" }}>
                    <span style={{ fontSize: 15, fontWeight: 700, color: row.asr.governed ? C.red : C.green }}>
                      {pct(row.asr)}
                    </span>
                    <span style={{ fontSize: 10, color: C.mut }}> of {row.asr.base}</span>
                  </td>
                  <td style={{ padding: "10px 12px", color: C.text }}>
                    {row.denials}
                    <div style={{ fontSize: 9.5, color: C.mut }}>
                      {Object.entries(row.by_gate).map(([g, n]) => `${g}×${n}`).join(", ") || "—"}
                    </div>
                  </td>
                  <td style={{ padding: "10px 12px", color: C.mut }}>{row.reference_denials}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {run.isError && (
        <div className="mt-3 p-3" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red }}>
          {String(run.error)}
        </div>
      )}

      <h2 style={{ fontSize: 14, fontWeight: 600, margin: "28px 0 6px" }}>
        What would each secret cost me?
      </h2>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.65, marginBottom: 12 }}>
        Each row is that source declared <b>alone</b>, so the rows compare to each other. They do
        not add up — sources read by the same tasks overlap, so the combined figure is measured
        separately. A source marked <b style={{ color: C.green }}>free</b> is one no benign task
        reads before an egress: declaring it is pure upside, and you cannot tell which those are
        without running this.
      </div>
      <div className="wrapline" style={{ gap: 8, marginBottom: 12 }}>
        <select
          value={sweepSuite || chosenSuites[0] || ""}
          onChange={(e) => setSweepSuite(e.target.value)}
          title="the sweep is per-suite: a source's cost depends on which tasks read it"
          style={{ fontFamily: MONO, fontSize: 11, padding: "5px 8px", background: C.panel, color: C.text, border: `1px solid ${C.line}`, borderRadius: 6 }}
        >
          {chosenSuites.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button style={cta(!sweep.isPending)} onClick={() => sweep.mutate()} disabled={sweep.isPending}>
          <ShieldCheck size={14} /> {sweep.isPending ? "sweeping…" : "Sweep"}
        </button>
      </div>

      {sweep.data && (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11 }}>
            <thead>
              <tr style={{ color: C.mut, textAlign: "left" }}>
                <th style={{ padding: "10px 12px" }} title="the tool declared a secret read, on its own">declared secret source</th>
                <th style={{ padding: "10px 12px" }} title="benign tasks retained with only this source declared">utility</th>
                <th style={{ padding: "10px 12px" }} title="attacks retained — the floor rarely moves this, which is the point of showing it">ASR</th>
                <th style={{ padding: "10px 12px" }}>denials</th>
                <th style={{ padding: "10px 12px" }} title="utility lost against the integrity-only baseline">cost</th>
              </tr>
            </thead>
            <tbody>
              {sweep.data.rows.map((row: SweepRow) => {
                const free = row.cost_pp <= 0;
                return (
                  <tr key={row.source} style={{ borderTop: `1px solid ${C.line}` }}>
                    <td style={{ padding: "9px 12px", color: C.text }}>{row.source}</td>
                    <td style={{ padding: "9px 12px", color: C.text }}>{pct(row.utility)}</td>
                    <td style={{ padding: "9px 12px", color: C.mut }}>{pct(row.asr)}</td>
                    <td style={{ padding: "9px 12px", color: C.mut }}>{row.denials}</td>
                    <td style={{ padding: "9px 12px", fontWeight: 600, color: free ? C.green : C.red }}>
                      {free ? "free" : `−${row.cost_pp.toFixed(1)}pp`}
                    </td>
                  </tr>
                );
              })}
              <tr style={{ borderTop: `1px solid ${C.line}`, background: "rgba(255,255,255,.02)" }}>
                <td style={{ padding: "9px 12px", color: C.mut }} title="measured, not summed — the per-source costs overlap">
                  all of the above together
                </td>
                <td style={{ padding: "9px 12px", color: C.text }}>{pct(sweep.data.combined.utility)}</td>
                <td style={{ padding: "9px 12px", color: C.mut }}>{pct(sweep.data.combined.asr)}</td>
                <td style={{ padding: "9px 12px", color: C.mut }}>{sweep.data.combined.denials}</td>
                <td style={{ padding: "9px 12px", color: C.mut }}>—</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-5 p-3" style={{ border: `1px solid ${C.line}`, borderLeft: `3px solid ${C.amber}`, borderRadius: "0 8px 8px 0", background: C.panel }}>
        <div className="wrapline" style={{ gap: 6, marginBottom: 4 }}>
          <AlertTriangle size={13} color={C.mut} />
          <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: C.text }}>
            What these numbers are not
          </span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
          Utility is measured over the benchmark's own ground-truth call sequences, not over a
          model's attempts — so it says what the gate would cost a <i>perfect</i> agent, and a real
          one makes extra calls that widen the tainted set. Treat the denial counts as a lower
          bound. The reference column is a published o4-mini run shown for comparison only.
        </div>
      </div>
    </div>
  );
}
