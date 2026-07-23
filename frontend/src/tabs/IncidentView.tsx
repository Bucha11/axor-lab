// Incident page (#/i/{incident_id}) — an imported production incident: the
// summary of what was imported (scenario, recorded condition, source pointer
// back to the Control Plane run) and the trace, step by step, reusing the
// evidence-view components. Data: GET /api/incidents/{id}.
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import TraceSteps, { verdictOf } from "../components/TraceSteps";
import Collapse from "../components/Collapse";
import EmptyState from "../components/EmptyState";

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="wrapline" style={{ gap: 8, padding: "3px 0" }}>
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, minWidth: 110 }}>{k}</span>
      <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>{children}</span>
    </div>
  );
}

export default function IncidentView({ incidentId }: { incidentId: string }) {
  const q = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: () => api.getIncident(incidentId),
  });

  if (q.isLoading) {
    return <div style={{ maxWidth: 660, margin: "0 auto", fontFamily: MONO, fontSize: 11, color: C.dim }}>loading incident…</div>;
  }
  if (q.isError || !q.data) {
    return (
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <EmptyState title={`incident ${incidentId} not found`}>
          {String(q.error instanceof Error ? q.error.message : "not found")} — import one at{" "}
          <span style={{ color: C.violet, cursor: "pointer" }} onClick={() => navigate("import")}>#/import</span>
        </EmptyState>
      </div>
    );
  }

  const inc = q.data;
  const cond = inc.condition ?? {};
  const { verdict } = verdictOf(inc.trace);
  const denied = verdict === "DENY";

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <div className="wrapline mb-3" style={{ gap: 8 }}>
        <ArrowLeft size={14} color={C.steel} style={{ cursor: "pointer" }} onClick={() => navigate("import")} />
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.steel, cursor: "pointer" }} onClick={() => navigate("import")}>
          import
        </span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          / incident {inc.incident_id}
        </span>
      </div>

      <h1 style={{ fontSize: 20, fontWeight: 650, lineHeight: 1.3, margin: "0 0 4px" }}>
        Production incident — replayed under its recorded condition
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 16 }}>
        imported {inc.imported_at || "—"} · replay verified at import (the verdicts reproduce bit-identically)
      </div>

      <div className="p-3 mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 6 }}>
          WHAT WAS IMPORTED
        </div>
        <Row k="scenario">{String(inc.scenario?.name ?? "?")}</Row>
        <Row k="condition">{String(cond.id ?? "?")}</Row>
        <Row k="kernel">{String(cond.kernel ?? "?")}</Row>
        <Row k="enforcement">
          <span style={{ color: cond.enforcement === "on" ? C.green : C.amber }}>
            {String(cond.enforcement ?? "?")}
          </span>
        </Row>
        {cond.config_hash != null && <Row k="config_hash">{String(cond.config_hash)}</Row>}
        <Row k="trace">{String(inc.trace?.trace_id ?? "?")} · {inc.trace?.events?.length ?? 0} events</Row>
        <Row k="verdict">
          <span style={{ color: denied ? C.green : C.red }}>{verdict}</span>
        </Row>
        {inc.source && (
          <Row k="source">
            {[inc.source.product, inc.source.run_id].filter(Boolean).join(" · ") || "—"}
            {inc.source.url && (
              <a
                href={inc.source.url}
                target="_blank"
                rel="noreferrer"
                style={{ color: C.steel, marginLeft: 8, display: "inline-flex", alignItems: "center", gap: 3 }}
              >
                open in Control Plane <ExternalLink size={10} />
              </a>
            )}
          </Row>
        )}
      </div>

      <TraceSteps trace={inc.trace} />

      <div className="mt-3">
        <Collapse title="provenance honesty — what this import can and cannot claim" border={C.amber} color={C.amber} defaultOpen>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
            <div><span style={{ color: C.green }}>verified:</span> the recorded condition travelled verbatim
              (config_hash checked) and the trace replays bit-identically under it — verified at import,
              re-verified on every server restart.</div>
            <div className="mt-1"><span style={{ color: C.amber }}>reconstructed:</span> the runtime config hash is
              re-derived from the shipped condition + inputs, so the bundle is marked{" "}
              <span style={{ color: C.text }}>runtime_provenance = reconstructed_incident</span> — it never
              masquerades as the exact config recorded at production execution time.</div>
          </div>
        </Collapse>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
        from here the incident is a trace-replay bundle: test a candidate policy against it, pin the verdict
        as a regression, and export. CLI:{" "}
        <span style={{ color: C.steel }}>axor-lab replay ./bundle · axor-lab pin ./bundle {String(inc.trace?.trace_id ?? "…")} {denied ? "DENY" : "ALLOW"}</span>
      </div>
    </div>
  );
}
