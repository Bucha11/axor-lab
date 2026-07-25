// Verify a downloaded reproduction package — in YOUR browser.
//
// This is the one capability that could not move to the server. `axor-lab
// verify` exists to check a package without trusting the server that served it;
// a "the server verified itself" button would not have moved that guarantee, it
// would have deleted it. So the integrity and binding checks run here, on your
// machine, over bytes you already hold — the canonicalizer is pinned
// byte-for-byte against the same vectors the Python one is.
//
// Replay is the exception and is labelled as one: recomputing verdicts needs the
// kernel, so it is the server doing that work, and the panel says so rather than
// folding it in with the checks you performed yourself.
import { useState } from "react";
import { Check, FileJson, ShieldCheck, TriangleAlert, X } from "lucide-react";
import { C, MONO, btn, cta, inp } from "../theme";
import { api } from "../api";
import { verifyPackage, type Check as VerifyCheck, type VerifyResult } from "../verify";

const ICON = {
  pass: <Check size={12} color={C.green} />,
  fail: <X size={12} color={C.red} />,
  skipped: <TriangleAlert size={12} color={C.amber} />,
};
const COLOR = { pass: C.green, fail: C.red, skipped: C.amber };

function Row({ check }: { check: VerifyCheck }) {
  return (
    <div className="wrapline" style={{ gap: 8, alignItems: "flex-start", padding: "5px 0" }}>
      <span style={{ marginTop: 2, flexShrink: 0 }}>{ICON[check.status]}</span>
      <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, minWidth: 130 }}>{check.name}</span>
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: COLOR[check.status], flex: "1 1 240px", lineHeight: 1.5 }}>
        {check.detail}
      </span>
    </div>
  );
}

export default function Verify() {
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [replay, setReplay] = useState<string | null>(null);
  const [pkg, setPkg] = useState<Record<string, unknown> | null>(null);
  const [fileName, setFileName] = useState("");
  const [allowBare, setAllowBare] = useState(false);
  const [publicKey, setPublicKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (parsed: Record<string, unknown>) => {
    setBusy(true); setError(null); setReplay(null);
    try {
      setResult(await verifyPackage(parsed, {
        allowBare, publicKeyHex: publicKey.trim() || undefined,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setFileName(file.name); setResult(null); setReplay(null); setError(null);
    try {
      const parsed = JSON.parse(await file.text());
      setPkg(parsed);
      await run(parsed);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  // Replay is the server's work, not yours — kept separate and labelled.
  const askServerToReplay = async () => {
    if (!pkg?.bundle || !pkg?.traces) return;
    setBusy(true); setReplay(null);
    try {
      const report = await api.replayUpload(
        pkg.bundle as Record<string, unknown>, pkg.traces,
      );
      setReplay(
        report.outcome === "reproduced"
          ? `reproduced — ${report.decisions} verdicts recomputed identically`
          : report.outcome === "not_attempted"
            ? "not attempted — the server does not have the kernel this bundle pins"
            : "diverged — recomputed verdicts differ from the recorded ones",
      );
    } catch (e) {
      setReplay(`the server could not replay it: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 4px" }}>Verify a package.</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20, lineHeight: 1.7 }}>
        Drop a reproduction package downloaded from any publication page. Every check below runs
        <b style={{ color: C.text }}> in this browser</b> — nothing is uploaded, and no server is
        trusted, which is the entire point of verifying.
      </div>

      <div className="p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        <div className="wrapline" style={{ gap: 10 }}>
          <FileJson size={14} color={C.steel} />
          <input type="file" accept=".json,application/json" onChange={onFile}
            style={{ fontFamily: MONO, fontSize: 11, color: C.mut }} />
        </div>

        <label className="wrapline mt-3" style={{ gap: 8 }}>
          <input type="checkbox" checked={allowBare} onChange={(e) => setAllowBare(e.target.checked)}
            style={{ accentColor: C.violet }} />
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.5 }}>
            bare file (a local bundle+traces, integrity only) — a package that SHOULD carry server
            proofs will not be accepted as bare
          </span>
        </label>

        <label className="wrapline mt-2" style={{ gap: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, minWidth: 108 }}>author pubkey</span>
          <input value={publicKey} onChange={(e) => setPublicKey(e.target.value)}
            placeholder="ed25519 hex — optional; without it authenticity is not checked"
            style={{ ...inp, flex: "1 1 240px", fontSize: 10.5 }} />
        </label>

        {pkg && (
          <button onClick={() => run(pkg)} disabled={busy} style={{ ...btn({ padding: "4px 10px", fontSize: 10.5 }), marginTop: 10 }}>
            re-check with these settings
          </button>
        )}
      </div>

      {error && (
        <div className="mt-3" style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>
          {fileName}: {error}
        </div>
      )}

      {result && (
        <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${result.passed ? C.green : C.red}`, borderRadius: 10 }}>
          <div className="wrapline mb-2" style={{ gap: 8 }}>
            <ShieldCheck size={15} color={result.passed ? C.green : C.red} />
            <span style={{ fontFamily: MONO, fontSize: 13, color: result.passed ? C.green : C.red, fontWeight: 600 }}>
              {result.passed ? "every check you ran passed" : "verification FAILED"}
            </span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
              {result.serverIssued ? "server-issued package" : "bare file"}
            </span>
          </div>
          {result.checks.map((c) => <Row key={c.name} check={c} />)}

          {result.checks.some((c) => c.status === "skipped") && (
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.amber, marginTop: 8, lineHeight: 1.6 }}>
              A skipped check is not a passed one. Integrity says the bytes are intact; only a
              verified signature says who produced them.
            </div>
          )}

          {/* replay is the server's, and stays visibly separate */}
          <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${C.line}` }}>
            <div className="wrapline" style={{ gap: 10, justifyContent: "space-between" }}>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, flex: "1 1 280px", lineHeight: 1.6 }}>
                Replay needs the governance kernel, which this browser does not have. Asking the
                server to replay is a check <b style={{ color: C.text }}>it</b> performs, not one you
                do — for a verdict that trusts nobody, run{" "}
                <span style={{ color: C.steel }}>axor-lab verify</span> on your own machine.
              </span>
              <button onClick={askServerToReplay} disabled={busy || !pkg?.bundle} style={cta(!busy && !!pkg?.bundle)}>
                ask the server to replay
              </button>
            </div>
            {replay && (
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: replay.startsWith("reproduced") ? C.green : C.amber, marginTop: 8 }}>
                server replay: {replay}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
