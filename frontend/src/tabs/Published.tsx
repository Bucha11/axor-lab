// Published catalog: the JSON mirror of the server catalog (GET
// /api/publications — public entries only; unlisted stays reachable by its
// capability URL #/e/{id}, private is never served).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, GitFork, Search } from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import { STATUS_LEGEND, StatusChip } from "../components/Provenance";
import EmptyState, { Cmd } from "../components/EmptyState";

export default function Published() {
  const [q, setQ] = useState("");
  const pubs = useQuery({ queryKey: ["publications"], queryFn: api.listPublications });
  const list = (pubs.data ?? []).filter((p) =>
    (p.question + p.publication_id).toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <div className="wrapline mb-2" style={{ justifyContent: "space-between" }}>
        <h1 style={{ fontSize: 21, fontWeight: 650, margin: 0 }}>Published experiments.</h1>
        <div className="flex items-center gap-2" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6, padding: "5px 10px" }}>
          <Search size={12} color={C.dim} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search"
            style={{ background: "none", border: "none", color: C.text, fontFamily: MONO, fontSize: 11, outline: "none", width: 140 }} />
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>
        immutable, re-runnable, forkable, citable artifacts — each with the full bundle that reproduces it
      </div>

      {pubs.isLoading && <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading catalog…</div>}
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
              Run an experiment and publish its bundle — the server verifies replay before minting:
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
              <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>{p.question}</span>
              <div className="wrapline" style={{ gap: 10, marginTop: 4 }}>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>/e/{p.publication_id}</span>
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
    </div>
  );
}
