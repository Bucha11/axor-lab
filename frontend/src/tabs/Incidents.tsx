// Incidents (#/incidents) — the browsable list of imported production
// incidents, so a previously imported incident can be found again (not only via
// the deep link shown right after import). Each row links to #/i/{id} and shows
// its approval / regression-pin state. Data: GET /api/incidents.
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronRight } from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import EmptyState from "../components/EmptyState";

function Badge({ on, label, color }: { on: boolean; label: string; color: string }) {
  if (!on) return null;
  return (
    <span
      className="wrapline"
      style={{
        gap: 4, fontFamily: MONO, fontSize: 9.5, color, padding: "1px 7px",
        borderRadius: 20, border: `1px solid ${color}`,
      }}
    >
      <Check size={10} /> {label}
    </span>
  );
}

export default function Incidents() {
  const q = useQuery({ queryKey: ["incidents"], queryFn: api.listIncidents });

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: "0 0 4px" }}>Incidents</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginBottom: 20 }}>
        imported production incidents — open one to review, approve, and pin it
      </div>

      {q.isLoading && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>loading…</div>
      )}
      {q.data && q.data.length === 0 && (
        <EmptyState title="no incidents yet">
          import one at{" "}
          <span style={{ color: C.violet, cursor: "pointer" }} onClick={() => navigate("import")}>
            #/import
          </span>{" "}
          — or use the Control Plane's “Export for Lab”.
        </EmptyState>
      )}
      {q.data?.map((i) => (
        <button
          key={i.incident_id}
          onClick={() => navigate(`i/${i.incident_id}`)}
          className="wrapline"
          style={{
            width: "100%", textAlign: "left", gap: 10, justifyContent: "space-between",
            background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10,
            padding: "10px 12px", marginBottom: 8, cursor: "pointer",
          }}
        >
          <div className="wrapline" style={{ gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>{i.scenario_id}</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>{i.incident_id}</span>
            {i.source?.product && (
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.steel }}>
                ← {i.source.product}
              </span>
            )}
          </div>
          <div className="wrapline" style={{ gap: 6 }}>
            <Badge on={!!i.approved} label="approved" color={C.green} />
            <Badge on={!!i.pinned} label="pinned" color={C.violet} />
            <ChevronRight size={14} color={C.dim} />
          </div>
        </button>
      ))}
    </div>
  );
}
