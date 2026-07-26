// Landing: a reproducible lab for agent governance. Entry points that DO
// something, plus the published catalog — fetched live from GET /api/publications.
//
// The first result is reachable from here with one click. The bundled example is
// offline (scripted agent, reference kernel, simulated tools), so the server can
// execute it directly — no agent, no provider, no CLI. That run is byte-identical
// to `axor-lab run` over the same file, so the shortcut is not a lesser path.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot, ChevronRight, Compass, Gauge, GitFork, PenLine, Play, RefreshCw, Search,
  ShieldAlert, Upload, Wrench,
} from "lucide-react";
import { C, MONO, cta } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import { STATUS_LEGEND, StatusChip } from "../components/Provenance";
import EmptyState from "../components/EmptyState";

// Three, not four. "Explore experiments" and "Reproduce a run" were two cards
// on one route — two doors into the same room. The catalog IS where you reproduce
// someone's run, so it says so once.
// These are the WAYS TO START, and this is now their only door — they were
// removed from the top nav because "bring an agent" is a verb, not a place you
// return to. That makes this list load-bearing: an entry point linked from
// nowhere is one that does not exist, so every route taken out of the bar has a
// card here.
const ENTRIES = [
  {
    icon: Compass, title: "Explore & reproduce", color: C.violet, tag: "no agent needed",
    desc: "browse published runs, replay their verdicts bit-for-bit, fork one", to: "published",
  },
  {
    icon: Gauge, title: "Measure against a benchmark", color: C.violet, tag: "start here",
    desc: "published suites with reference numbers → what the gate costs, what it buys",
    to: "benchmark",
  },
  {
    icon: Wrench, title: "Measure your own scenarios", color: C.steel, tag: "your tasks",
    desc: "compose suites and conditions → a validated .axl → run it under the same gate",
    to: "builder",
  },
  {
    icon: Upload, title: "Bring your agent", color: C.steel, tag: "your setup",
    desc: "code · endpoint · traces → run your own", to: "agent-ingest",
  },
  {
    icon: Bot, title: "Compare live models", color: C.amber, tag: "BYOK · costs money",
    desc: "the only run where the ungoverned arm measures a model, not a stand-in",
    to: "models",
  },
  {
    icon: PenLine, title: "Author a scenario", color: C.steel, tag: "advanced",
    desc: "write the task, the injection and the violation predicate by hand",
    to: "scenario-author",
  },
  {
    // this card promises the incident path, so it goes to the incident
    // importer — it used to land on the generic agent-ingest screen
    icon: ShieldAlert, title: "Investigate a production incident", color: C.red, tag: "for teams",
    desc: "import the trace → reproduce it → test a fix → pin a regression", to: "import",
  },
] as const;

export default function Landing() {
  const [q, setQ] = useState("");
  const qc = useQueryClient();
  const pubs = useQuery({ queryKey: ["publications"], queryFn: api.listPublications });

  // Run the bundled example in the server and go straight to its results.
  const runExample = useMutation({
    mutationFn: () => api.runLocal(),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["run-results", run.run_id] });
      navigate(`results/${run.run_id}`);
    },
  });

  const list = (pubs.data ?? []).filter((p) =>
    p.question.toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <h1 style={{ fontSize: 27, fontWeight: 700, lineHeight: 1.2, margin: "0 0 8px", maxWidth: 540 }}>
        A reproducible lab for agent governance.
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.mut, marginBottom: 20, maxWidth: 560, lineHeight: 1.6 }}>
        Run experiments on LLM agents under Axor governance — or reproduce someone else's, bit-for-bit on the
        governance layer. Free for research. Standalone; no Axor deployment needed.
      </div>

      {/* The shortest path to a real result: one click, no setup. */}
      <div className="mb-8 p-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 10 }}>
        <div className="wrapline" style={{ gap: 12, justifyContent: "space-between" }}>
          <div style={{ flex: "1 1 320px", minWidth: 260 }}>
            <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, fontWeight: 600 }}>
              Start with the worked example
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 4, lineHeight: 1.6 }}>
              A banking agent gets a prompt-injected exfiltration attempt, run 60 times
              ungoverned and governed. Nothing to install: the agent is a deterministic
              stand-in and the tools are simulated, so it runs here in a few seconds.
            </div>
          </div>
          <button
            onClick={() => runExample.mutate()}
            disabled={runExample.isPending}
            style={cta(!runExample.isPending)}
          >
            {runExample.isPending
              ? <><RefreshCw size={13} className="animate-spin" /> running…</>
              : <><Play size={13} /> Run the example</>}
          </button>
        </div>
        {runExample.isError && (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.red, marginTop: 10, lineHeight: 1.6 }}>
            {(runExample.error as Error).message}
          </div>
        )}
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 10, lineHeight: 1.6 }}>
          The server executes it and records the run — the same run `axor-lab run` produces from
          the same file, down to the bundle id. A run that needs a live model is refused here and
          stays with the CLI, where the cost ceiling lives.
        </div>
      </div>

      {/* entry points */}
      <div className="entrygrid mb-10">
        {ENTRIES.map(({ icon: Icon, title, desc, color, tag, to }) => (
          <div key={title} onClick={() => navigate(to)}
            style={{ cursor: "pointer", padding: 16, borderRadius: 10, background: C.panel, border: `1px solid ${C.line}` }}>
            <Icon size={18} color={color} />
            <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, fontWeight: 600, marginTop: 10 }}>{title}</div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 3, lineHeight: 1.5 }}>{desc}</div>
            <div style={{ fontFamily: MONO, fontSize: 9, color, marginTop: 8 }}>{tag}</div>
          </div>
        ))}
      </div>

      {/* catalog */}
      <div className="wrapline mb-3" style={{ justifyContent: "space-between" }}>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>Published experiments</span>
        <div className="flex items-center gap-2" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6, padding: "5px 10px" }}>
          <Search size={12} color={C.dim} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search"
            style={{ background: "none", border: "none", color: C.text, fontFamily: MONO, fontSize: 11, outline: "none", width: 120 }} />
        </div>
      </div>

      {pubs.isLoading && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading catalog…</div>
      )}
      {pubs.isError && (
        <EmptyState title="catalog unreachable">
          The publications server is not answering — start it and reload. One process
          serves the catalog and the run API:
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 6 }}>
            python -m lab_server --root ./lab-store
          </div>
        </EmptyState>
      )}
      {pubs.isSuccess && list.length === 0 && (
        <EmptyState title={q ? "no publications match the search" : "no published experiments yet"}>
          {!q && (
            <>
              Run the example above, then publish it from its results page — the bundle is
              re-verified server-side (content hashes, bit-identical replay, recomputed
              statistics) before it is minted.
            </>
          )}
        </EmptyState>
      )}

      <div className="flex flex-col gap-2">
        {list.map((p) => (
          <div key={p.publication_id} className="wrapline"
            onClick={() => navigate(`e/${p.publication_id}`)}
            style={{ justifyContent: "space-between", background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: "12px 14px", cursor: "pointer" }}>
            <div style={{ flex: "1 1 260px", minWidth: 220 }}>
              <div className="wrapline" style={{ gap: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>{p.question}</span>
              </div>
              <div className="wrapline" style={{ gap: 10, marginTop: 4 }}>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{p.publication_id}</span>
                {p.license && <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{p.license}</span>}
              </div>
            </div>
            <div className="wrapline" style={{ gap: 12 }}>
              <StatusChip axes={p.provenance} />
              {p.provenance.reproductions.count > 0 && (
                <span className="flex items-center gap-1" style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                  <GitFork size={10} /> {p.provenance.reproductions.count}
                </span>
              )}
              <ChevronRight size={14} color={C.dim} />
            </div>
          </div>
        ))}
      </div>

      {/* status legend — the distinction is explicit, per review */}
      <div className="wrapline mt-4" style={{ gap: 14 }}>
        {STATUS_LEGEND.map((st) => {
          const Icon = st.icon;
          return (
            <span key={st.label} className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 9, color: st.color }}>
              <Icon size={10} /> {st.label} <span style={{ color: C.dim }}>— {st.note}</span>
            </span>
          );
        })}
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 20, lineHeight: 1.7 }}>
        The catalog never equates a verified run with an uploaded JSON: <b style={{ color: C.green }}>Lab-executed</b> ran here,
        <b style={{ color: C.steel }}> independently reproduced</b> was re-run by others, <b style={{ color: C.amber }}>self-reported</b> is
        integrity-hashed but not independently verified. Reproductions are counted, not assumed.
      </div>
    </div>
  );
}
