// Landing (lab-landing mockup): a reproducible lab for agent governance. Three
// low-barrier entry points plus the incident path, and the published catalog —
// fetched live from GET /api/publications, never hardcoded.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronRight, Compass, GitFork, RefreshCw, Search, ShieldAlert, Upload,
} from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import { STATUS_LEGEND, StatusChip } from "../components/Provenance";
import EmptyState, { Cmd } from "../components/EmptyState";

const ENTRIES = [
  {
    icon: Compass, title: "Explore experiments", color: C.violet, tag: "lowest barrier",
    desc: "browse published runs, see the mechanism, fork one", to: "published",
  },
  {
    icon: RefreshCw, title: "Reproduce a run", color: C.green, tag: "no agent needed",
    desc: "drop a bundle → re-run its governance verdicts exactly", to: "published",
  },
  {
    icon: Upload, title: "Bring your agent", color: C.steel, tag: "your setup",
    desc: "code · endpoint · traces → run your own", to: "agent-ingest",
  },
  {
    icon: ShieldAlert, title: "Investigate a production incident", color: C.red, tag: "for teams",
    desc: "import the trace → reproduce it → test a fix → pin a regression", to: "agent-ingest",
  },
] as const;

export default function Landing() {
  const [q, setQ] = useState("");
  const pubs = useQuery({ queryKey: ["publications"], queryFn: api.listPublications });

  const list = (pubs.data ?? []).filter((p) =>
    p.question.toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <h1 style={{ fontSize: 27, fontWeight: 700, lineHeight: 1.2, margin: "0 0 8px", maxWidth: 540 }}>
        A reproducible lab for agent governance.
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.mut, marginBottom: 28, maxWidth: 560, lineHeight: 1.6 }}>
        Run experiments on LLM agents under Axor governance — or reproduce someone else's, bit-for-bit on the
        governance layer. Free for research. Standalone; no Axor deployment needed.
      </div>

      {/* three entry points + incidents — a lower first step than "bring your agent" */}
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
          The publications server is not answering. Start it, then reload:
          <Cmd>python -m lab_server --root ./lab-store --port 8000</Cmd>
        </EmptyState>
      )}
      {pubs.isSuccess && list.length === 0 && (
        <EmptyState title={q ? "no publications match the search" : "no published experiments yet"}>
          {!q && (
            <>
              Publish a run to fill the catalog — the bundle is verified server-side before it is minted:
              <Cmd>{`axor-lab run examples/banking-exfil-01.axl --out ./bundle --yes
axor-lab publish ./bundle --question "…" --visibility public \\
    --server http://127.0.0.1:8000`}</Cmd>
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
