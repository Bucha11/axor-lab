// Import an incident (#/import) — the Control Plane → Lab cross-link. The CP's
// "Open in Lab" button deep-links here; the user drops the incident package
// (axor-lab-incident/v1: trace + scenario + manifests + the EXACT recorded
// condition, verbatim) and the server runs the SAME core as the CLI
// `import-incident`: full validation, config-hash check, and replay under the
// recorded condition BEFORE anything is stored. A replay mismatch is shown
// honestly — recorded vs recomputed verdicts — never smoothed over.
import { ChangeEvent, DragEvent, useState } from "react";
import { Check, FileJson, Shield, TriangleAlert, Upload } from "lucide-react";
import { C, MONO, cta, inp } from "../theme";
import { navigate } from "../router";
import {
  api, IncidentImportError, IncidentImportResult, IncidentPackage, ReplayMismatchDetail,
} from "../api";
import { useApp } from "../store";

function parsePackage(text: string): { pkg?: IncidentPackage; error?: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "not valid JSON" };
  }
  const p = parsed as Partial<IncidentPackage> | null;
  if (!p || typeof p !== "object") return { error: "not a JSON object" };
  if (p.schema_version !== "axor-lab-incident/v1") {
    return { error: `schema_version is ${JSON.stringify(p.schema_version)} — expected "axor-lab-incident/v1"` };
  }
  if (!p.trace || !p.scenario || !p.condition || !Array.isArray(p.manifests)) {
    return { error: "package must carry trace, scenario, manifests[] and the recorded condition" };
  }
  return { pkg: p as IncidentPackage };
}

function Row({ k, v, color }: { k: string; v: string; color?: string }) {
  return (
    <div className="wrapline" style={{ gap: 8, padding: "3px 0" }}>
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, minWidth: 110 }}>{k}</span>
      <span style={{ fontFamily: MONO, fontSize: 11.5, color: color ?? C.text }}>{v}</span>
    </div>
  );
}

function MismatchTable({ detail }: { detail: ReplayMismatchDetail }) {
  const n = Math.max(detail.recorded_verdicts.length, detail.recomputed_verdicts.length);
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.06em", marginBottom: 4 }}>
        RECORDED vs RECOMPUTED — per gate decision (status: {detail.status})
      </div>
      {Array.from({ length: n }, (_, i) => {
        const rec = detail.recorded_verdicts[i];
        const cmp = detail.recomputed_verdicts[i];
        const differ = (rec?.verdict ?? "—") !== (cmp?.verdict ?? "—");
        return (
          <div key={i} className="wrapline" style={{ gap: 8, fontFamily: MONO, fontSize: 11 }}>
            <span style={{ color: C.dim }}>#{i}</span>
            <span style={{ color: C.text }}>recorded {rec?.verdict ?? "—"}</span>
            <span style={{ color: C.dim }}>→</span>
            <span style={{ color: differ ? C.red : C.text }}>replayed {cmp?.verdict ?? "—"}</span>
            {rec?.gate && <span style={{ color: C.dim }}>gate {rec.gate}</span>}
          </div>
        );
      })}
    </div>
  );
}

export default function ImportIncident() {
  const writeToken = useApp((s) => s.writeToken);
  const setWriteToken = useApp((s) => s.setWriteToken);
  const [fileName, setFileName] = useState<string | null>(null);
  const [pkg, setPkg] = useState<IncidentPackage | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IncidentImportResult | null>(null);
  const [importError, setImportError] = useState<IncidentImportError | Error | null>(null);
  const [dragging, setDragging] = useState(false);

  const readFile = async (file: File) => {
    setFileName(file.name);
    setResult(null);
    setImportError(null);
    const { pkg: parsed, error } = parsePackage(await file.text());
    setPkg(parsed ?? null);
    setParseError(error ?? null);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void readFile(file);
  };

  const onPick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (file) void readFile(file);
  };

  const doImport = async () => {
    if (!pkg || busy) return;
    setBusy(true);
    setResult(null);
    setImportError(null);
    try {
      setResult(await api.importIncident(pkg));
    } catch (err) {
      setImportError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setBusy(false);
    }
  };

  const cond = pkg?.condition;
  const events = pkg?.trace?.events?.length ?? 0;
  const mismatch = importError instanceof IncidentImportError ? importError.replay : undefined;
  const needsToken = importError instanceof IncidentImportError && importError.status === 401;

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <h1 style={{ fontSize: 20, fontWeight: 650, margin: "0 0 4px" }}>Import an incident</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 16, lineHeight: 1.6 }}>
        drop the incident package a Control Plane run exported (axor-lab-incident/v1) — the server
        validates everything and replays the trace under its RECORDED condition before storing anything
      </div>

      {/* drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        style={{
          border: `1.5px dashed ${dragging ? C.violet : C.line}`, borderRadius: 10,
          padding: "26px 16px", textAlign: "center",
          background: dragging ? "rgba(155,140,204,0.06)" : C.panel2, marginBottom: 14,
        }}
      >
        <Upload size={18} color={dragging ? C.violet : C.dim} />
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginTop: 8 }}>
          drag the incident-package .json here, or{" "}
          <label style={{ color: C.violet, cursor: "pointer", textDecoration: "underline" }}>
            pick a file
            <input type="file" accept=".json,application/json" onChange={onPick} style={{ display: "none" }} />
          </label>
        </div>
        {fileName && (
          <div className="wrapline" style={{ gap: 6, justifyContent: "center", marginTop: 8 }}>
            <FileJson size={12} color={C.dim} />
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>{fileName}</span>
          </div>
        )}
      </div>

      {parseError && (
        <div className="wrapline p-3 mb-3" style={{ gap: 8, background: "rgba(229,72,77,0.05)", border: "1px solid #5a2a2c", borderRadius: 8 }}>
          <TriangleAlert size={13} color={C.red} />
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{parseError}</span>
        </div>
      )}

      {/* local preview — parsed in the browser, nothing uploaded yet */}
      {pkg && (
        <div className="p-3 mb-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 6 }}>
            PACKAGE PREVIEW — parsed locally, not uploaded yet
          </div>
          <Row k="scenario" v={String(pkg.scenario?.name ?? "?")} />
          <Row k="condition" v={String(cond?.id ?? "?")} />
          <Row k="kernel" v={String(cond?.kernel ?? "?")} />
          <Row
            k="enforcement"
            v={String(cond?.enforcement ?? "?")}
            color={cond?.enforcement === "on" ? C.green : C.amber}
          />
          <Row k="trace events" v={`${events} (trace ${String(pkg.trace?.trace_id ?? "?")})`} />
          <Row k="manifests" v={`${pkg.manifests.length} tool manifest(s)`} />
          <Row
            k="source"
            v={pkg.source
              ? [pkg.source.product, pkg.source.run_id].filter(Boolean).join(" · ") || "—"
              : "— (none declared)"}
          />
        </div>
      )}

      {/* write token — the publications server gates writes like publish */}
      <div className="wrapline mb-3" style={{ gap: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut, minWidth: 110 }}>write token</span>
        <input
          type="password"
          value={writeToken}
          onChange={(e) => setWriteToken(e.target.value)}
          placeholder="empty if the server runs open (local dev)"
          style={{ ...inp, flex: 1, minWidth: 200 }}
        />
      </div>

      <button style={cta(!!pkg && !busy)} disabled={!pkg || busy} onClick={() => void doImport()}>
        <Shield size={14} />
        {busy ? "importing — validating + replaying…" : "Import & replay"}
      </button>

      {/* result */}
      {result && (
        <div className="p-3 mt-3" style={{ background: "rgba(70,167,88,0.05)", border: `1px solid ${C.green}`, borderRadius: 10 }}>
          <div className="wrapline" style={{ gap: 8 }}>
            <Check size={14} color={C.green} />
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, fontWeight: 600 }}>
              replay: {result.replay} — the recorded verdicts reproduce under the recorded condition
            </span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 6, paddingLeft: 22 }}>
            incident{" "}
            <span
              style={{ color: C.violet, cursor: "pointer", textDecoration: "underline" }}
              onClick={() => navigate(`i/${result.incident_id}`)}
            >
              {result.incident_id}
            </span>{" "}
            · trace {result.trace_id}
          </div>
        </div>
      )}

      {importError && (
        <div className="p-3 mt-3" style={{ background: "rgba(229,72,77,0.05)", border: "1px solid #5a2a2c", borderRadius: 10 }}>
          <div className="wrapline" style={{ gap: 8 }}>
            <TriangleAlert size={14} color={C.red} />
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.red, fontWeight: 600 }}>
              {mismatch ? "import refused — the incident does not replay" : "import failed"}
            </span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 6, paddingLeft: 22, lineHeight: 1.6 }}>
            {importError.message}
            {needsToken && (
              <div style={{ color: C.amber, marginTop: 4 }}>
                the server gates writes — paste the write token above (--write-token / AXOR_LAB_WRITE_TOKEN)
              </div>
            )}
            {mismatch && <MismatchTable detail={mismatch} />}
          </div>
        </div>
      )}

      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 18, lineHeight: 1.7 }}>
        the recorded condition travels VERBATIM (enforcement, policy, config_hash) — reconstructing it
        could silently change the verdict, so the server refuses any package whose trace does not replay
        bit-identically under the condition it shipped with. CLI equivalent:{" "}
        <span style={{ color: C.steel }}>axor-lab import-incident --trace … --condition … --out ./bundle</span>
      </div>
    </div>
  );
}
