// The governance benchmark: what the gate costs, what it buys, and what your
// own secret declaration would cost YOU.
//
// The old headline — "ASR 55% → 0%" — came from a scripted agent whose
// injection-following rate is a parameter, so it was a dial, not a finding.
// This screen shows the two axes together instead: utility retained and ASR
// retained. A defense that reports only one of them is reporting half a trade.
//
// The sweep is the part an operator cannot get anywhere else. The
// confidentiality floor is sound but coarse — after ANY declared secret read,
// every egress in the session is refused — so the useful question is never
// "should I turn it on" but "which reads am I willing to pay for". That is a
// per-source number that depends on your own workflows, and on the stock
// banking suite three of six sources turn out to cost nothing at all.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Play, ShieldCheck } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { api, type BenchRates, type BenchRow, type SweepRow } from "../api";

const pct = (r: BenchRates) =>
  r.base ? `${((100 * r.governed) / r.base).toFixed(1)}%` : "n/a";

function Rate({ value, of, tone }: { value: BenchRates; of: string; tone: string }) {
  return (
    <div>
      <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700, color: tone }}>
        {pct(value)}
      </span>
      <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}> of {value.base} {of}</span>
    </div>
  );
}

export default function Benchmark() {
  const index = useQuery({ queryKey: ["bench-index"], queryFn: api.benchIndex });
  const [allowlist, setAllowlist] = useState(false);
  const [secrets, setSecrets] = useState<Record<string, string[]>>({});
  const [sweepSuite, setSweepSuite] = useState("banking");

  const run = useMutation({
    mutationFn: () => api.benchRun({ allowlist, secrets }),
  });
  const sweep = useMutation({
    mutationFn: () => api.benchSweep(sweepSuite, allowlist),
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
    <div style={{ maxWidth: 860, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, margin: "0 0 8px" }}>
        Governance benchmark
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7, marginBottom: 20 }}>
        The four AgentDojo suites, replayed through the real axor-core governor over the
        benchmark's own ground truth. Two axes, always together:{" "}
        <b style={{ color: C.text }}>utility retained</b> is how much legitimate work
        survives the gate, <b style={{ color: C.text }}>ASR retained</b> is how much attack
        survives it. Reporting one without the other describes half a trade.
      </div>

      {index.isError && (
        <div className="p-3 mb-4" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red }}>
          the runtime API is not reachable — start the server without --no-runtime-api
        </div>
      )}

      {/* ── policy ─────────────────────────────────────────────────────── */}
      <div className="p-4 mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600, marginBottom: 10 }}>
          Policy
        </div>
        <label className="wrapline" style={{ gap: 8, cursor: "pointer", marginBottom: 6 }}>
          <input type="checkbox" checked={allowlist} onChange={(e) => setAllowlist(e.target.checked)} />
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>
            declare the banking known-payee enum
          </span>
        </label>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.6, marginLeft: 24 }}>
          An enum on a driving argument <b>restricts as well as supersedes</b>: it lifts the
          taint on payees you vetted, and denies every destination outside the set — including
          clean, prompt-given ones you forgot to list.
        </div>
      </div>

      {/* ── secrets ────────────────────────────────────────────────────── */}
      <div className="p-4 mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 6 }}>
          <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 600 }}>
            Secrets <span style={{ color: C.mut, fontWeight: 400 }}>· {declared} declared</span>
          </div>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.6, marginBottom: 12 }}>
          Which tools read secrets is a property of <i>your</i> deployment — the same
          <code> search_files</code> is a secret read in a law firm and routine in a public wiki.
          Declaring one arms a floor that is content-blind and sticky: after that read, every
          egress in the session is refused, whatever the outgoing value looks like. That is what
          makes it paraphrase-proof, and why it can be expensive. Run the sweep below before
          choosing.
        </div>
        {(index.data?.suites ?? []).map((suite) => (
          <div key={suite.suite} style={{ marginBottom: 10 }}>
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginBottom: 4 }}>
              {suite.suite}
              <span style={{ color: C.mut }}> · {suite.user_tasks} benign / {suite.injection_tasks} attack</span>
            </div>
            <div className="wrapline" style={{ gap: 6 }}>
              {suite.secret_candidates.map((tool) => {
                const on = (secrets[suite.suite] ?? []).includes(tool);
                return (
                  <button
                    key={tool}
                    onClick={() => toggleSecret(suite.suite, tool)}
                    style={{
                      fontFamily: MONO, fontSize: 10, padding: "3px 8px", borderRadius: 999,
                      cursor: "pointer",
                      border: `1px solid ${on ? C.violet : C.line}`,
                      background: on ? C.violet : "transparent",
                      color: on ? "#fff" : C.mut,
                    }}
                  >
                    {tool}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <button style={cta(!run.isPending)} onClick={() => run.mutate()} disabled={run.isPending}>
        <Play size={14} /> {run.isPending ? "measuring…" : "Measure this policy"}
      </button>

      {/* ── the matrix ─────────────────────────────────────────────────── */}
      {run.data && (
        <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11 }}>
            <thead>
              <tr style={{ color: C.mut, textAlign: "left" }}>
                <th style={{ padding: "10px 12px" }}>suite</th>
                <th style={{ padding: "10px 12px" }}>utility retained</th>
                <th style={{ padding: "10px 12px" }}>ASR retained</th>
                <th style={{ padding: "10px 12px" }}>denials</th>
                <th style={{ padding: "10px 12px" }}>reference</th>
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
                    <Rate value={row.utility} of="benign" tone={C.text} />
                  </td>
                  <td style={{ padding: "10px 12px" }}>
                    <Rate value={row.asr} of="attacks"
                          tone={row.asr.base && row.asr.governed ? C.red : C.green} />
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

      {/* ── the sweep ──────────────────────────────────────────────────── */}
      <h2 style={{ fontSize: 14, fontWeight: 600, margin: "28px 0 6px" }}>
        What would each secret cost me?
      </h2>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.6, marginBottom: 12 }}>
        Each row is that source declared <b>alone</b>, so the rows compare to each other.
        They do not add up: sources read by the same tasks overlap, so the combined figure is
        measured separately. A source marked <b style={{ color: C.green }}>free</b> is one no
        benign task reads before an egress — declaring it is pure upside.
      </div>
      <div className="wrapline" style={{ gap: 8, marginBottom: 12 }}>
        <select
          value={sweepSuite}
          onChange={(e) => setSweepSuite(e.target.value)}
          style={{ fontFamily: MONO, fontSize: 11, padding: "5px 8px", background: C.panel, color: C.text, border: `1px solid ${C.line}`, borderRadius: 6 }}
        >
          {(index.data?.suites ?? []).map((s) => (
            <option key={s.suite} value={s.suite}>{s.suite}</option>
          ))}
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
                <th style={{ padding: "10px 12px" }}>declared secret source</th>
                <th style={{ padding: "10px 12px" }}>utility</th>
                <th style={{ padding: "10px 12px" }}>ASR</th>
                <th style={{ padding: "10px 12px" }}>denials</th>
                <th style={{ padding: "10px 12px" }}>cost</th>
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
                <td style={{ padding: "9px 12px", color: C.mut }}>all of the above together</td>
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
          Utility here is measured over the benchmark's own ground-truth call sequences, not over
          a model's attempts — so it says what the gate would cost a <i>perfect</i> agent, and a
          real one makes extra calls that widen the tainted set. Treat the denial counts as a
          lower bound. The reference column is a published run on o4-mini and is shown for
          comparison only; it is not something this harness reproduces.
        </div>
      </div>
    </div>
  );
}
