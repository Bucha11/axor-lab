// Workspace (#/workspace) — the paid Security-Workspace surface: the license
// tier, the append-only workflow history, and the compliance export. On a
// hosted server below the Security tier the API answers 402; this screen shows
// an honest "needs the Security tier" panel instead of the data.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Lock, ShieldCheck } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { api, type ComplianceReport } from "../api";

function isGated(err: unknown): boolean {
  return err instanceof Error && err.message.startsWith("402");
}

function Gated({ what }: { what: string }) {
  return (
    <div
      className="p-3"
      style={{ background: C.panel, border: `1px solid ${C.amber}`, borderRadius: 10 }}
    >
      <div className="wrapline" style={{ gap: 6, color: C.amber, fontFamily: MONO, fontSize: 12 }}>
        <Lock size={13} /> {what} needs the Security Workspace tier
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 6, lineHeight: 1.6 }}>
        This hosted workspace is below the Security tier. Incident history,
        approvals and compliance export are Security-tier features (see pricing).
        Safety features are never gated.
      </div>
    </div>
  );
}

function download(name: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function LicenseCard() {
  const q = useQuery({ queryKey: ["license-status"], queryFn: api.licenseStatus });
  const s = q.data;
  const tier = s?.workspace_tier ?? "community";
  const active = s?.active === true;
  return (
    <div
      className="p-3 mb-4"
      style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}
    >
      <div className="wrapline" style={{ gap: 8, justifyContent: "space-between" }}>
        <div className="wrapline" style={{ gap: 8 }}>
          <ShieldCheck size={15} color={active ? C.green : C.dim} />
          <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 700, color: C.text }}>
            {tier} workspace
          </span>
        </div>
        <span
          style={{
            fontFamily: MONO, fontSize: 9.5, padding: "2px 8px", borderRadius: 20,
            color: active ? C.green : C.dim, border: `1px solid ${active ? C.green : C.line}`,
          }}
        >
          {active ? "licensed" : "community"}
        </span>
      </div>
      {active && s && (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginTop: 6, lineHeight: 1.6 }}>
          {s.organization} ·{" "}
          {[
            s.modules?.private_lab && "Private Lab",
            s.modules?.control_plane && "Control Plane",
          ].filter(Boolean).join(" + ") || "no modules"}
          {s.self_hosted_runner ? " · self-hosted" : ""} · expires {s.expires_at}
        </div>
      )}
    </div>
  );
}

function History() {
  const q = useQuery({ queryKey: ["audit"], queryFn: api.auditLog, retry: false });
  if (q.isError && isGated(q.error)) return <Gated what="Workflow history" />;
  const events = q.data ?? [];
  return (
    <div
      className="p-3 mb-4"
      style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}
    >
      <div style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 8 }}>
        workflow history
      </div>
      {events.length === 0 ? (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
          no actions yet — import an incident, then approve it.
        </div>
      ) : (
        events
          .slice()
          .reverse()
          .map((e) => (
            <div
              key={e.seq}
              className="wrapline"
              style={{ gap: 8, padding: "3px 0", fontFamily: MONO, fontSize: 10.5 }}
            >
              <span style={{ color: C.dim, minWidth: 155 }}>{e.ts}</span>
              <span style={{ color: C.text, minWidth: 140 }}>{e.action}</span>
              <span style={{ color: C.mut }}>
                {e.actor}
                {e.target ? ` → ${e.target}` : ""}
              </span>
            </div>
          ))
      )}
    </div>
  );
}

function Regression() {
  const q = useQuery({ queryKey: ["regression"], queryFn: api.regressionCorpus, retry: false });
  const [report, setReport] = useState<Awaited<ReturnType<typeof api.runRegression>> | null>(null);
  const [busy, setBusy] = useState(false);
  if (q.isError && isGated(q.error)) return <Gated what="Regression corpus" />;
  const pins = q.data ?? [];
  const run = async () => {
    setBusy(true);
    try {
      setReport(await api.runRegression());
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="p-3 mb-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
      <div className="wrapline" style={{ gap: 8, justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, color: C.text }}>
          regression corpus · {pins.length} pin{pins.length === 1 ? "" : "s"}
        </span>
        <button onClick={run} disabled={busy || pins.length === 0} style={cta(!busy && pins.length > 0)}>
          {busy ? "running…" : "run corpus"}
        </button>
      </div>
      {pins.length === 0 ? (
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
          no pins yet — pin an incident's verdict from its page.
        </div>
      ) : (
        pins.map((p) => (
          <div key={p.trace_id} className="wrapline" style={{ gap: 8, padding: "2px 0", fontFamily: MONO, fontSize: 10.5 }}>
            <span style={{ color: p.side === "must_block" ? C.red : C.green, minWidth: 90 }}>{p.side}</span>
            <span style={{ color: C.mut }}>{p.trace_id}</span>
          </div>
        ))
      )}
      {report && (
        <div style={{ marginTop: 10, borderTop: `1px solid ${C.line}`, paddingTop: 8, fontFamily: MONO, fontSize: 10.5 }}>
          <span style={{ color: report.safe_to_ship ? C.green : C.red }}>
            {report.safe_to_ship ? "safe to ship" : "NOT safe"} ·{" "}
          </span>
          <span style={{ color: C.mut }}>
            {report.held} held · {report.passed} passed · {report.regressed} regressed ·{" "}
            {report.escaped} escaped · {report.skipped} skipped
          </span>
        </div>
      )}
    </div>
  );
}

function Compliance() {
  const [report, setReport] = useState<ComplianceReport | null>(null);
  const [gated, setGated] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setGated(false);
    try {
      const r = await api.complianceReport();
      setReport(r);
    } catch (err) {
      if (isGated(err)) setGated(true);
      else throw err;
    } finally {
      setBusy(false);
    }
  };

  if (gated) return <Gated what="Compliance export" />;
  return (
    <div className="p-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
      <div className="wrapline" style={{ gap: 8, justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 700, color: C.text }}>
          compliance export
        </span>
        <button onClick={run} disabled={busy} style={cta(!busy)}>
          {busy ? "generating…" : "generate report"}
        </button>
      </div>
      {report && (
        <>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
            generated {report.generated_at} · {report.total_events} events ·{" "}
            {report.incidents.length} incidents ·{" "}
            {report.incidents.filter((i) => i.approved).length} approved
          </div>
          <button
            onClick={() => download(`compliance-${report.generated_at}.json`, report)}
            style={{ ...cta(), marginTop: 10 }}
          >
            <Download size={12} /> download JSON
          </button>
        </>
      )}
    </div>
  );
}

export default function Workspace() {
  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: "0 0 4px" }}>Workspace</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginBottom: 20 }}>
        entitlement, workflow history, and compliance export — the Security-tier surface
      </div>
      <LicenseCard />
      <History />
      <Regression />
      <Compliance />
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 12, lineHeight: 1.6 }}>
        On a self-hosted Lab these are free; on a hosted workspace they need the
        Security tier. The audit log is append-only — every import, approval and
        export is recorded.
      </div>
    </div>
  );
}
